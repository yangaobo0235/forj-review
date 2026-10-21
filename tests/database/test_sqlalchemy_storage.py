"""Real database behavior at the ORM boundary, independent of application SQL text."""

import asyncio

import pytest
from sqlalchemy import func, select

from forj_review_database.database import AsyncDatabase
from forj_review_database.models import EngineEmission, Head
from forj_review_service.integrations.storage.configuration import (
    publish_state_async,
    read_state_async,
)
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler
from tests.support.pages import business_registry
from tests.support.postgres import seed_complete_pages


@pytest.mark.asyncio
async def test_pooled_transactions_restore_readonly_and_roll_back_errors(database, monkeypatch):
    monkeypatch.setenv("DATABASE_POOL_SIZE", "1")
    async with AsyncDatabase(database) as runtime:
        async with runtime.session(readonly=True, repeatable=True) as session:
            assert (
                await session.scalar(select(func.current_setting("transaction_read_only"))) == "on"
            )
            assert (
                await session.scalar(select(func.current_setting("transaction_isolation")))
                == "repeatable read"
            )
        with pytest.raises(RuntimeError, match="abort"):
            async with runtime.session() as session:
                assert (
                    await session.scalar(select(func.current_setting("transaction_read_only")))
                    == "off"
                )
                assert (
                    await session.scalar(select(func.current_setting("transaction_isolation")))
                    == "read committed"
                )
                session.add(
                    EngineEmission(
                        engine_model="ROLLBACK", emission_standard="国六", source_config_count=1
                    )
                )
                await session.flush()
                raise RuntimeError("abort")
        async with runtime.session(readonly=True) as session:
            assert await session.get(EngineEmission, "ROLLBACK") is None
        assert runtime.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_concurrent_same_operation_has_one_release_and_one_revision(database):
    seed_complete_pages(database)
    async with AsyncDatabase(database) as runtime:
        async with runtime.session(readonly=True, repeatable=True) as session:
            state = await read_state_async(session)

        async def publish():
            async with runtime.session() as session:
                return await publish_state_async(session, state, operation_id="concurrent-retry")

        first, second = await asyncio.gather(publish(), publish())
        assert first == second
        async with runtime.session(readonly=True) as session:
            head = await session.get(Head, 1)
            assert head.release_id == first
            assert head.revision == state.revision + 1


@pytest.mark.asyncio
async def test_review_reference_pools_belong_to_each_execution_loop(database):
    async with AsyncDatabase(database) as runtime:
        async with runtime.session() as session:
            session.add(
                EngineEmission(
                    engine_model="LOOPTEST", emission_standard="国六", source_config_count=1
                )
            )
    service = ReviewService(
        load_model=False, database_settings=database, registry=business_registry()
    )

    async def run():
        assert await service.lookup_engine_emissions(["LOOPTEST"]) == {"LOOPTEST": "国六"}
        assert await service.lookup_engine_emissions(["ABSENT"]) == {}

    schedulers = [FairAsyncScheduler(global_limit=1, per_job_limit=1) for _ in range(2)]
    try:
        await asyncio.gather(
            *(asyncio.wrap_future(scheduler.submit("lookup", run)) for scheduler in schedulers)
        )
    finally:
        for scheduler in schedulers:
            await asyncio.to_thread(scheduler.close, cleanup=service.aclose)
    assert not service._databases
