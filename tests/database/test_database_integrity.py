"""Verify relational integrity without dropping legacy documents or framework state."""

import psycopg
import pytest

from forj_review_config_service.contracts.models import CapturedPage, ConfigurationError
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncSessionStore,
)
from forj_review_config_service.integrations.database import Database as ConfigurationDatabase
from forj_review_service.integrations.database import Database as ReviewDatabase
from tests.support.configuration import SNAPSHOT, candidate


@pytest.mark.parametrize("backend", [ConfigurationDatabase, ReviewDatabase])
def test_backends_refuse_previous_storage_contract(database, backend):
    runtime = backend(database.url, database.schema)
    runtime.require_ready()
    with database.connect() as db:
        db.execute("UPDATE sys_schema_migration SET version=1")
    with pytest.raises(ValueError, match="存储版本"):
        runtime.require_ready()


@pytest.mark.asyncio
async def test_session_projection_and_capture_reference_are_atomic(database, async_database, settings):
    store = AsyncSessionStore(async_database)
    capture = CapturedPage(
        capture_id="capture",
        extension_id="a" * 32,
        snapshot=SNAPSHOT,
        created_at="2026-10-18T00:00:00+00:00",
    )
    await store.put_capture(capture, "capture-request", "capture-fingerprint")
    _, record = candidate(settings)
    record.session_id, record.capture_id, record.report = "session", "capture", None
    await store.put(record)
    record.state, record.turn_status, record.revision = ("NEEDS_INPUT", "WAITING", 2)
    await store.put(record)
    with database.connect() as db:
        assert db.execute(
            "SELECT capture_id,state,turn_status,revision FROM cfg_session"
        ).fetchone() == ("capture", "NEEDS_INPUT", "WAITING", 2)
        assert (
            db.execute("SELECT created_at FROM cfg_session_event LIMIT 1").fetchone()[0].tzinfo
            is not None
        )
    with database.connect() as db:
        assert db.execute("SELECT workflow ? 'state' FROM cfg_session").fetchone() == (False,)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), database.connect() as db:
        db.execute("DELETE FROM cfg_capture WHERE id='capture'")
    record.session_id, record.capture_id = ("invalid-session", "missing-capture")
    with pytest.raises(ConfigurationError) as rejected:
        await store.put(record)
    assert rejected.value.code == "CAPTURE_NOT_FOUND"
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM cfg_session").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM cfg_session_event").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_invalid_document_and_orphan_event_are_rejected(database, async_database, settings):
    _, record = candidate(settings)
    record.report = None
    await AsyncSessionStore(async_database).put(record)
    for assignment in (
        "revision=-1",
        "state='UNKNOWN'",
        "turn_status='UNKNOWN'",
        "draft='[]'::jsonb",
    ):
        with pytest.raises(psycopg.errors.CheckViolation), database.connect() as db:
            db.execute("UPDATE cfg_session SET " + assignment)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), database.connect() as db:
        db.execute(
            "INSERT INTO cfg_session_event(session_id,kind,payload) VALUES('missing','progress','{}')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), database.connect() as db:
        db.execute("UPDATE cfg_head SET revision=-1")


@pytest.mark.asyncio
async def test_request_relationships_and_explicit_cleanup(database, async_database, settings):
    store = AsyncSessionStore(async_database)
    capture = CapturedPage(
        capture_id="capture",
        extension_id="a" * 32,
        snapshot=SNAPSHOT,
        created_at="2026-10-18T00:00:00+00:00",
    )
    await store.put_capture(capture, "capture-request", "capture-fingerprint")
    _, record = candidate(settings)
    record.session_id, record.capture_id, record.report = "session", "capture", None
    await store.put(
        record, request=("create", "create-request", "fingerprint", {"session_id": "session"})
    )
    await store.put(
        record, request=("session:message", "message-request", "fingerprint", {"revision": 0})
    )
    with database.connect() as db:
        assert db.execute(
            "SELECT operation_type,scope_key,result FROM cfg_idempotency ORDER BY operation_type"
        ).fetchall() == [
            ("capture", "global", {"capture_id": "capture"}),
            ("create", "global", {"session_id": "session"}),
            ("message", "session", {"revision": 0}),
        ]
    await store.delete("session")
    await store.delete_capture("capture")
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM cfg_idempotency").fetchone()[0] == 3
        for table in ("cfg_session", "cfg_capture", "cfg_session_event"):
            assert (
                db.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(table))
                ).fetchone()[0]
                == 0
            )
    with pytest.raises(ConfigurationError) as rejected:
        await store.cached("create", "create-request", "fingerprint")
    assert rejected.value.code == "RESOURCE_EXPIRED"
