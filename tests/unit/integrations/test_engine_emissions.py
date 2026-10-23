"""Reference lookup and pool ownership without touching a business service."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from forj_review_service.contracts.engine_emissions import EngineEmissionLookupUnavailable
from forj_review_service.integrations.engine_emissions import lookup_engine_emissions_async
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler


class ReferenceDatabase:
    def __init__(self, rows=(), failure=None):
        self.rows = rows
        self.failure = failure
        self.queries = []

    @asynccontextmanager
    async def session(self, *, readonly):
        assert readonly
        yield self

    async def execute(self, query):
        self.queries.append(query)
        if self.failure:
            raise self.failure
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.asyncio
async def test_reference_lookup_normalizes_deduplicates_and_batches_exact_models():
    database = ReferenceDatabase([("ENGINE42-E6", "国六")])
    result = await lookup_engine_emissions_async(
        ["示例 ＥＮＧＩＮＥ４２－Ｅ６", "ENGINE42-E6", "ENGINE42-E6A", ""], database=database
    )
    assert result == {"ENGINE42-E6": "国六"}
    assert len(database.queries) == 1
    query = database.queries[0]
    assert "ref_engine_emission" in str(query)
    assert query.compile().params == {"engine_model_1": ["ENGINE42-E6", "ENGINE42-E6A"]}


@pytest.mark.asyncio
async def test_empty_input_does_not_query_and_normal_miss_is_empty():
    database = ReferenceDatabase()
    assert await lookup_engine_emissions_async([], database=database) == {}
    assert not database.queries
    assert await lookup_engine_emissions_async(["UNKNOWN-E6"], database=database) == {}
    assert len(database.queries) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,failure", [([], SQLAlchemyError("secret")), ([("MODEL", "京六")], None)]
)
async def test_reference_failure_and_invalid_standards_are_not_normal_misses(rows, failure):
    with pytest.raises(EngineEmissionLookupUnavailable) as error:
        await lookup_engine_emissions_async(["MODEL"], database=ReferenceDatabase(rows, failure))
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_shared_service_reuses_one_engine_per_worker_loop_and_closes_on_owner(monkeypatch):
    databases = []

    class LoopDatabase(ReferenceDatabase):
        def __init__(self, settings):
            super().__init__([("LOOPTEST", "国六")])
            self.loop = asyncio.get_running_loop()
            self.engine = SimpleNamespace(dispose=AsyncMock(side_effect=self.dispose))
            databases.append(self)

        async def execute(self, query):
            assert asyncio.get_running_loop() is self.loop
            return await super().execute(query)

        async def dispose(self):
            assert asyncio.get_running_loop() is self.loop

    monkeypatch.setattr("forj_review_service.integrations.database.AsyncDatabase", LoopDatabase)
    monkeypatch.setattr(
        "forj_review_service.integrations.database.review_database", lambda: object()
    )
    service = ReviewService(load_model=False)

    async def run():
        for _ in range(2):
            assert await service.lookup_engine_emissions(["LOOPTEST"]) == {"LOOPTEST": "国六"}

    schedulers = [FairAsyncScheduler(global_limit=1, per_job_limit=1) for _ in range(2)]
    try:
        await asyncio.gather(*(asyncio.wrap_future(s.submit("lookup", run)) for s in schedulers))
    finally:
        for scheduler in schedulers:
            await asyncio.to_thread(scheduler.close, cleanup=service.aclose)
    assert len(databases) == 2
    assert all(len(db.queries) == 2 for db in databases)
    assert all(db.engine.dispose.await_count == 1 for db in databases)
    assert not service._emission_databases
