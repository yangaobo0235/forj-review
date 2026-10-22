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


def test_runtime_readiness_excludes_retired_reference_tables_but_checks_current_storage(database):
    from psycopg import sql

    from forj_review_database.readiness import require_ready_sync
    from forj_review_database.schema import RETIRED_REFERENCE_TABLES

    # This database fixture owns a disposable schema, never the developer database.
    with database.connect() as connection:
        connection.execute(sql.SQL("DROP TABLE ") + sql.SQL(",").join(
            sql.Identifier(table) for table in sorted(RETIRED_REFERENCE_TABLES)))
        require_ready_sync(connection.orm, database.schema)
        connection.execute("DROP TABLE cfg_idempotency")
        with pytest.raises(ValueError, match="cfg_idempotency"):
            require_ready_sync(connection.orm, database.schema)
