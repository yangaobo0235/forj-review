"""Real PostgreSQL transactions, executor fencing and in-memory publication."""

import asyncio
import json
import tempfile

import psycopg
import pytest
from fastapi.testclient import TestClient

from forj_review_config_service.bootstrap.application import create_app as configuration_app
from forj_review_config_service.contracts.models import ConfigurationError, SessionRecord
from forj_review_config_service.integrations.async_database import (
    AsyncDatabase,
    ExecutorCheckpointer,
)
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncPageSnapshots,
    AsyncSessionStore,
)
from forj_review_config_service.integrations.configuration_store.postgres import (
    PageSnapshots,
    write_files,
)
from forj_review_config_service.integrations.database import Database
from forj_review_config_service.integrations.review_kernel.postgres import PostgresReviewKernel
from forj_review_config_service.integrations.review_kernel.snapshot import SnapshotKernel
from scripts.dev.page_tools import fingerprint
from tests.integration.test_review_config_service import SNAPSHOT, FakeModel, create, headers, wait
from tests.integration.test_review_config_service import settings as settings


@pytest.mark.asyncio
async def test_store_atomic_receipt_events_and_schema_isolation(async_database):
    store = AsyncSessionStore(async_database)
    record = SessionRecord(session_id="session", snapshot=SNAPSHOT, baseline_digest="baseline")
    await store.put(record, "created", request=("session:create", "request", "digest", {}))
    record.messages.append({"role": "user", "content": "must roll back"})
    with pytest.raises(psycopg.errors.UniqueViolation):
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
            f"/api/review-config/sessions/{sid}/messages",
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
    draft = (await kernel.context("example_permit"))["source"]
    draft["version"] = "2.0"
    record = SessionRecord(
        session_id="test", snapshot=SNAPSHOT, baseline_digest=await kernel.baseline(), draft=draft
    )
    record.report = await kernel.validate(record)
    receipt = await kernel.commit(record, "commit123")
    assert (
        await kernel.commit(record, "commit123")
        == receipt
        == await kernel.commit_receipt("commit123")
    )
    assert (await kernel.context("example_permit"))["reference"]["version"] == "2.0"
    rollback = SessionRecord(
        session_id="rollback",
        snapshot=SNAPSHOT,
        activation_only=True,
        baseline_digest=await kernel.baseline(),
        draft=await kernel.rollback_draft("example_permit", "1.0"),
    )
    rollback.report = await kernel.validate(rollback)
    pages = AsyncPageSnapshots(async_database)
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
    async with old.connect() as connection:
        await connection.execute("SELECT pg_terminate_backend(%s)", (old.identity[0],))
    async with AsyncDatabase(old.settings) as new:
        await new.acquire_executor()
        with pytest.raises(ConfigurationError, match="所有权"):
            async with old.connect(write=True):
                pytest.fail("old executor entered a write transaction")
        with pytest.raises(ConfigurationError, match="所有权"):
            await ExecutorCheckpointer(old).adelete_thread("old-thread")
        async with new.connect(write=True) as connection:
            await connection.execute("UPDATE page_snapshots SET revision=revision+1 WHERE id=1")


@pytest.mark.asyncio
async def test_takeover_waits_for_already_fenced_transaction(async_database):
    old = async_database
    async with AsyncDatabase(old.settings) as new:
        async with old.connect(write=True) as connection:
            await connection.execute("SELECT pg_terminate_backend(%s)", (old.identity[0],))
            taking_over = asyncio.create_task(new.acquire_executor())
            try:
                # Ask PostgreSQL itself whether the contender is waiting on the fence.
                for _ in range(100):
                    await connection.execute("SELECT pg_stat_clear_snapshot()")
                    cursor = await connection.execute(
                        "SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' "
                        "AND application_name='forj-review-config-service' AND pid<>pg_backend_pid()"
                    )
                    if await cursor.fetchone():
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
            async with old.connect(write=True):
                pass


@pytest.mark.asyncio
async def test_slow_database_query_keeps_event_loop_responsive(async_database):
    async with async_database.connect() as connection:
        slow = asyncio.create_task(connection.execute("SELECT pg_sleep(.2)"))
        await asyncio.wait_for(asyncio.sleep(0.01), 0.1)
        assert not slow.done()
        await slow


def test_snapshot_paths_cannot_escape_export_directory(tmp_path):
    for name in ("../escape.json", "/absolute.json", "C:/escape.json", "..\\escape.json"):
        with pytest.raises(ValueError):
            write_files(tmp_path, {name: "{}"})


def test_publication_preserves_versions_and_rejects_stale_target(database, settings, tmp_path):
    from scripts.dev.page_tools import publish

    store = PageSnapshots(database)
    digest = fingerprint(store.get())
    incoming = tmp_path / "incoming"
    write_files(incoming, store.get())
    definition = json.loads((incoming / "permit.json").read_text(encoding="utf-8"))
    definition["active"] = False
    (incoming / "permit.json").write_text(json.dumps(definition), encoding="utf-8")
    definition = {**definition, "version": "2.0", "active": True}
    (incoming / "v2.json").write_text(json.dumps(definition), encoding="utf-8")
    publish(store, incoming, digest, tmp_path / "before-publication")
    with pytest.raises(ValueError, match="目标配置已变化"):
        publish(store, incoming, digest, tmp_path / "stale")
    assert ".history/versions.json" not in store.get()
    (incoming / "permit.json").unlink()
    with pytest.raises(ValueError, match="全部配置版本"):
        publish(store, incoming, fingerprint(store.get()), tmp_path / "lost-history")


@pytest.mark.asyncio
async def test_executor_monitor_stops_work_after_keeper_disconnect(async_database):
    stopped = asyncio.Event()

    async def stop():
        stopped.set()

    async_database.watch_executor(stop)
    async with async_database.connect() as connection:
        await connection.execute("SELECT pg_terminate_backend(%s)", (async_database.identity[0],))
    await asyncio.wait_for(stopped.wait(), 4)
    assert async_database.lost.is_set()
    with pytest.raises(ConfigurationError):
        await AsyncSessionStore(async_database).put(
            SessionRecord(session_id="lost", snapshot=SNAPSHOT, baseline_digest="baseline")
        )


@pytest.mark.asyncio
async def test_concurrent_publications_cannot_overwrite_one_another(
    async_database, settings, monkeypatch
):
    from threading import Barrier

    kernel = PostgresReviewKernel(
        async_database, settings.cases_dir, settings.review_url, settings.state_dir / "case-sets"
    )
    draft = (await kernel.context("example_permit"))["source"]
    draft["version"] = "2.0"
    record = SessionRecord(
        session_id="concurrent",
        snapshot=SNAPSHOT,
        baseline_digest=await kernel.baseline(),
        draft=draft,
    )
    record.report = await kernel.validate(record)
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
