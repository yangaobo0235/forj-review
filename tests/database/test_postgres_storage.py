"""Real PostgreSQL transactions, executor fencing and in-memory publication."""

import asyncio
import json
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from forj_review_config_service.bootstrap.application import create_app as configuration_app
from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from forj_review_config_service.integrations.async_database import (
    AsyncDatabase,
    ExecutorCheckpointer,
)
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncConfigurationStore,
    AsyncTaskStore,
)
from forj_review_config_service.integrations.database import Database
from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel
from tests.support.configuration import (
    SNAPSHOT,
    FakeModel,
    candidate,
    create,
    database_candidate,
    headers,
    wait,
)


@pytest.mark.asyncio
async def test_store_atomic_receipt_events_and_schema_isolation(async_database, settings):
    store = AsyncTaskStore(async_database)
    _, record = candidate(settings)
    record.task_id = "session"
    record.report = None
    await store.put(record, "created", request=("session:create", "request", "digest", {}))
    record.messages.append({"role": "user", "content": "must roll back"})
    with pytest.raises(IntegrityError):
        await store.put(record, request=("session:create", "request", "digest", {}))
    assert len(await store.events("session", 0)) == 1
    assert not (await store.get("session")).messages
    with pytest.raises(ConfigurationError, match="同一个请求"):
        await store.cached("session:create", "request", "different")
    async with AsyncDatabase(async_database.settings) as contender:
        with pytest.raises(ValueError, match="已有配置服务"):
            await contender.acquire_executor()
    with pytest.raises(ValueError):
        Database(async_database.settings.url, "local;DROP SCHEMA public")


def test_configuration_app_runs_real_postgres_graph_and_restarts(database, settings):
    with TestClient(configuration_app(settings, model=FakeModel())) as client:
        sid = create(client, headers())
        result = client.post(
            f"/api/review-config/tasks/{sid}/messages",
            json={"client_request_id": "message", "expected_revision": 0, "text": "核对配置"},
        )
        assert result.status_code == 200
        assert wait(client, sid, headers()).model_calls == 1

        async def assert_released():
            # The completion callback runs on the same loop as the executor.
            await asyncio.sleep(0)
            service = client.app.state.service
            assert sid not in service.tasks
            assert sid not in service.locks

        client.portal.call(assert_released)
    with TestClient(configuration_app(settings, model=FakeModel())) as client:
        assert client.portal.call(client.app.state.service.store.get, sid).model_calls == 1
        kernel = client.app.state.service.kernel
        assert client.portal.call(kernel.baseline) == client.portal.call(kernel.baseline)
        assert (
            client.portal.call(kernel.context, "example_permit")["source"]["page_id"]
            == "example_permit"
        )


@pytest.mark.asyncio
async def test_publication_and_rollback_are_atomic_without_temporary_files(
    async_database, settings, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration snapshots must stay in memory")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "case-sets"
    )
    store = AsyncTaskStore(async_database)
    record = await database_candidate(kernel, store)
    receipt = await kernel.commit(record, "commit123")
    assert (
        await kernel.commit(record, "commit123")
        == receipt
        == await kernel.commit_receipt("commit123")
    )
    assert (await kernel.context("example_permit"))["reference"]["version"] == "2.0"
    rollback = await database_candidate(kernel, store, sid="rollback", rollback="1.0")
    pages = AsyncConfigurationStore(async_database)
    before = await pages.get()
    original = SnapshotKernel.commit

    def interrupted(self, record, operation):
        original(self, record, operation)
        raise RuntimeError("abort before transaction commit")

    monkeypatch.setattr(SnapshotKernel, "commit", interrupted)
    with pytest.raises(RuntimeError):
        await kernel.commit(rollback, "rollback123")
    assert await pages.get() == before
    monkeypatch.setattr(SnapshotKernel, "commit", original)
    await kernel.commit(rollback, "rollback123")
    assert (await kernel.context("example_permit"))["reference"]["version"] == "1.0"
    assert (
        json.loads((settings.pages_dir / "permit.json").read_text(encoding="utf-8"))["version"]
        == "1.0"
    )


@pytest.mark.asyncio
async def test_disconnected_executor_cannot_write_after_takeover(async_database):
    old = async_database
    async with old.session() as connection:
        await connection.execute(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": old.identity[0]}
        )
    async with AsyncDatabase(old.settings) as new:
        await new.acquire_executor()
        with pytest.raises(ConfigurationError, match="所有权"):
            async with old.session(write=True):
                pytest.fail("old executor entered a write transaction")
        with pytest.raises(ConfigurationError, match="所有权"):
            await ExecutorCheckpointer(old).adelete_thread("old-thread")
        async with new.session(write=True) as connection:
            await connection.execute(text("UPDATE cfg_head SET revision=revision+1 WHERE id=1"))


@pytest.mark.asyncio
async def test_takeover_waits_for_already_fenced_transaction(async_database):
    old = async_database
    async with AsyncDatabase(old.settings) as new:
        async with old.session(write=True) as connection:
            await connection.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": old.identity[0]}
            )
            taking_over = asyncio.create_task(new.acquire_executor())
            try:
                # Ask PostgreSQL itself whether the contender is waiting on the fence.
                for _ in range(100):
                    await connection.execute(text("SELECT pg_stat_clear_snapshot()"))
                    cursor = await connection.execute(
                        text(
                            "SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' "
                            "AND application_name='forj-review-config-service' AND pid<>pg_backend_pid()"
                        )
                    )
                    if cursor.first():
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("takeover did not wait for the active transaction")
                assert not taking_over.done()
            except BaseException:
                taking_over.cancel()
                await asyncio.gather(taking_over, return_exceptions=True)
                raise
        await asyncio.wait_for(taking_over, 3)
        with pytest.raises(ConfigurationError):
            async with old.session(write=True):
                pass


@pytest.mark.asyncio
async def test_slow_database_query_keeps_event_loop_responsive(async_database):
    async with async_database.session() as connection:
        slow = asyncio.create_task(connection.execute(text("SELECT pg_sleep(.2)")))
        await asyncio.wait_for(asyncio.sleep(0.01), 0.1)
        assert not slow.done()
        await slow


@pytest.mark.asyncio
async def test_executor_monitor_stops_work_after_keeper_disconnect(async_database):
    stopped = asyncio.Event()

    async def stop():
        stopped.set()

    async_database.watch_executor(stop)
    async with async_database.session() as connection:
        await connection.execute(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": async_database.identity[0]}
        )
    await asyncio.wait_for(stopped.wait(), 4)
    assert async_database.lost.is_set()
    with pytest.raises(ConfigurationError):
        await AsyncTaskStore(async_database).put(
            TaskRecord(task_id="lost", snapshot=SNAPSHOT, baseline_digest="baseline")
        )


@pytest.mark.asyncio
async def test_concurrent_publications_cannot_overwrite_one_another(
    async_database, settings, monkeypatch
):
    from threading import Barrier

    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "case-sets"
    )
    record = await database_candidate(kernel, AsyncTaskStore(async_database), sid="concurrent")
    ready = Barrier(2, timeout=5)
    original = SnapshotKernel.commit

    def prepare(self, record, operation_id):
        receipt = original(self, record, operation_id)
        ready.wait()
        return receipt

    monkeypatch.setattr(SnapshotKernel, "commit", prepare)
    results = await asyncio.gather(
        kernel.commit(record, "race1"), kernel.commit(record, "race2"), return_exceptions=True
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, Exception))
    assert isinstance(rejected, ConfigurationError) and rejected.code == "BASELINE_CHANGED"
    receipts = [await kernel.commit_receipt(operation) for operation in ("race1", "race2")]
    assert sum(receipt is not None for receipt in receipts) == 1


@pytest.mark.asyncio
async def test_other_page_release_preserves_task_baseline_and_merges_latest_manifest(
    async_database, database, settings
):
    from copy import deepcopy
    from dataclasses import replace
    from uuid import uuid4

    from forj_review_service.integrations.storage.configuration import read_state
    from tests.support.postgres import publish_validated_test_state

    with database.connect() as db:
        state = read_state(db)
        other = deepcopy(state.source("example_permit", "1.0"))
        other["page_id"] = "independent_permit"
        staged = replace(
            state,
            definitions=(*state.definitions, other),
            active={**state.active, other["page_id"]: "1.0"},
        )
        publish_validated_test_state(
            db, staged, operation_id="secondpage", operation_type="PUBLISH"
        )
    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "case-sets"
    )
    store = AsyncTaskStore(async_database)
    record = await database_candidate(kernel, store, sid="isolatedpage")
    baseline, candidate_digest = record.baseline_digest, record.report["candidate_digest"]
    with database.connect() as db:
        state = read_state(db)
        other = deepcopy(state.source("independent_permit", "1.0"))
        other["version"] = "2.0"
        other["fields"][0]["label"] = "另一页面自己的修改"
        _, staged = state.stage(other)
        publish_validated_test_state(
            db, staged, operation_id="otherchange", operation_type="PUBLISH"
        )
    assert await kernel.baseline("example_permit") == baseline
    assert (await kernel.validate(record))["candidate_digest"] == candidate_digest
    receipt = await kernel.commit(record, uuid4().hex)
    with database.connect() as db:
        current = read_state(db)
        assert current.active == {"example_permit": "2.0", "independent_permit": "2.0"}
        assert (
            current.source("independent_permit", "2.0")["fields"][0]["label"]
            == "另一页面自己的修改"
        )
        proof = db.execute(
            "SELECT verification_context FROM cfg_validation_run WHERE id=(SELECT validation_run_id FROM cfg_release WHERE id=%s)",
            (receipt["release_id"],),
        ).fetchone()[0]
        assert proof["merged_from_validation"] == record.report["report_id"]
