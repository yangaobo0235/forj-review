"""Compose the HTTP application and own the review runtime lifetime."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from forj_review_assistant.interfaces.http.catalog import router as catalog_router
from forj_review_assistant.interfaces.http.review import router as review_router
from forj_review_assistant.observability.http import RequestLoggingMiddleware
from forj_review_assistant.observability.logging import configured_logging
from forj_review_assistant.runtime.jobs.manager import ReviewJobManager
from forj_review_assistant.runtime.review_service import ReviewService


def create_app(*, review_service: ReviewService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with configured_logging():
            logger = logging.getLogger(__name__)
            service = review_service if review_service is not None else ReviewService()
            jobs = ReviewJobManager(service)
            app.state.review_service = service
            app.state.review_jobs = jobs
            app.state.process_instance_id = uuid4().hex
            app.state.configuration_directory = str(Path(os.environ["REVIEW_PAGES_DIR"]).resolve()) if os.getenv("REVIEW_PAGES_DIR") else None
            logger.info("review service started release=%s profiles=%d model_configured=%s model_global=%d model_per_job=%d finalize=%d",
                        os.getenv("REVIEW_RELEASE_ID", "development"), len(service.registry.active_profiles),
                        service.agent is not None, jobs.scheduler.global_limit, jobs.scheduler.per_job_limit,
                        jobs.finalize_scheduler.global_limit)
            try:
                yield
            finally:
                jobs.close()
                logger.info("review service stopped")

    app = FastAPI(title="FORJ Review Assistant", lifespan=lifespan)
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(catalog_router)
    app.include_router(review_router)
    return app
