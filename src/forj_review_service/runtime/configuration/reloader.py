"""Build a complete execution snapshot before atomically making it available."""

import asyncio
import logging
from copy import copy
from datetime import UTC, datetime

from sqlalchemy import select

from forj_review_database.models import Head
from forj_review_service.runtime.configuration.postgres import load_registry
from forj_review_service.runtime.workflow.graph import ReviewWorkflow

logger = logging.getLogger(__name__)


class ConfigurationReloader:
    def __init__(self, app, database, interval=2.0):
        self.app, self.database, self.interval = app, database, interval
        self.status = {"desired_release_id": app.state.review_service.registry.release_id,
                       "load_error": None, "last_loaded_at": datetime.now(UTC).isoformat()}

    async def refresh(self):
        async with self.database.session(readonly=True) as session:
            desired = await session.scalar(select(Head.release_id).where(Head.id == 1))
        self.status["desired_release_id"] = desired
        current = self.app.state.review_service
        if desired == current.registry.release_id:
            self.status["load_error"] = None
            return False
        registry = await load_registry(self.database)
        # Share resource pools, but compile the workflow and all capability
        # registries against the new immutable page registry.
        replacement = copy(current)
        replacement.registry = registry
        replacement.workflow = ReviewWorkflow(replacement, **current._handler_overrides)
        self.app.state.review_jobs.review_service = replacement
        self.app.state.review_service = replacement
        self.status.update(load_error=None, last_loaded_at=datetime.now(UTC).isoformat())
        logger.info("configuration loaded release_id=%s pages=%d", registry.release_id,
                    len(registry.active_profiles))
        return True

    async def run(self):
        while True:
            try:
                await self.refresh()
            except Exception as exc:
                error = type(exc).__name__
                if self.status["load_error"] != error:
                    logger.error("configuration reload failed desired_release_id=%s error_type=%s",
                                 self.status["desired_release_id"], error)
                self.status["load_error"] = error
            await asyncio.sleep(self.interval)
