"""Keep existing validation/confirmation rules with atomic PostgreSQL page snapshots."""

import asyncio
from pathlib import Path

from forj_review_config_service.integrations.async_database import AsyncDatabase
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncConfigurationStore,
)
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel


class PostgresReviewKernel:
    edit_fields = staticmethod(SnapshotKernel.edit_fields)
    evaluate_collection = staticmethod(SnapshotKernel.evaluate_collection)
    proof_is_current = staticmethod(SnapshotKernel.proof_is_current)
    field_dependencies = staticmethod(SnapshotKernel.field_dependencies)
    page_options = staticmethod(SnapshotKernel.page_options)

    def __init__(
        self, database: AsyncDatabase, cases_dir: Path, review_url: str, local_cases_dir: Path
    ):
        self.pages = AsyncConfigurationStore(database)
        self.cases_dir, self.review_url, self.local_cases_dir = (
            cases_dir,
            review_url,
            local_cases_dir,
        )
        self.source = "postgresql:" + database.schema

    def _snapshot(self, configuration) -> SnapshotKernel:
        return SnapshotKernel(
            configuration,
            self.cases_dir,
            self.review_url,
            self.local_cases_dir,
            configuration_source=self.source,
        )

    async def _load(self) -> SnapshotKernel:
        return self._snapshot(await self.pages.get())

    async def baseline(self):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.baseline)

    async def context(self, page_id=None):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.context, page_id)

    async def capabilities(self):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.capabilities)

    async def rollback_draft(self, page_id, version):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.rollback_draft, page_id, version)

    async def template_members(self, reference):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.template_members, reference)

    async def case_sets(self):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.case_sets)

    async def register_cases(self, case_set_id, suite):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.register_cases, case_set_id, suite)

    async def validate(self, record):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.validate, record)

    async def replay(self, record, case_set_id):
        # A replay owns an immutable in-memory snapshot; no database lock spans await.
        kernel = await self._load()
        return await kernel.replay(record, case_set_id)

    async def verify_replay_receipt(self, result):
        kernel = await self._load()
        return await asyncio.to_thread(kernel.verify_replay_receipt, result)

    async def commit(self, record, operation_id):
        previous = await self.pages.receipt(operation_id)
        if previous:
            if (previous["page_id"], previous["version"], previous["candidate_digest"]) != (
                record.draft["page_id"], record.draft["version"], record.report["candidate_digest"]):
                from forj_review_config_service.contracts.models import ConfigurationError

                raise ConfigurationError("IDEMPOTENCY_CONFLICT", "保存操作 ID 已用于其他候选")
            return previous
        state = await self.pages.get()
        kernel = self._snapshot(state)
        receipt = await asyncio.to_thread(kernel.commit, record, operation_id)
        return await self.pages.publish(kernel.configuration, receipt, record)

    async def commit_receipt(self, operation_id):
        return await self.pages.receipt(operation_id)

    async def activation(self, record):
        kernel = await self._load()
        return await kernel.activation(record)
