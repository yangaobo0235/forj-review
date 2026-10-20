"""Verify relational integrity without dropping legacy documents or framework state."""

import psycopg
import pytest
from psycopg.types.json import Jsonb

from forj_review_config_service.contracts.models import CapturedPage, SessionRecord
from forj_review_config_service.integrations.configuration_store.async_postgres import (
    AsyncSessionStore,
)
from forj_review_config_service.integrations.database import Database as ConfigurationDatabase
from forj_review_service.integrations.database import Database as ReviewDatabase
from tests.integration.test_review_config_service import SNAPSHOT


@pytest.mark.parametrize("backend", [ConfigurationDatabase, ReviewDatabase])
def test_backends_refuse_previous_storage_contract(database, backend):
    runtime = backend(database.url, database.schema)
    runtime.require_ready()
    with database.connect() as db:
        db.execute("UPDATE storage_version SET version=1")
    with pytest.raises(ValueError, match="存储版本不兼容"):
        runtime.require_ready()


@pytest.mark.asyncio
async def test_session_projection_and_capture_reference_are_atomic(database, async_database):
    store = AsyncSessionStore(async_database)
    capture = CapturedPage(
        capture_id="capture",
        extension_id="a" * 32,
        snapshot=SNAPSHOT,
        created_at="2026-10-18T00:00:00+00:00",
    )
    await store.put_capture(capture, "capture-request", "capture-fingerprint")
    record = SessionRecord(
        session_id="session", capture_id="capture", snapshot=SNAPSHOT, baseline_digest="baseline"
    )
    await store.put(record)
    record.state, record.turn_status, record.revision = ("NEEDS_INPUT", "WAITING", 2)
    await store.put(record)
    with database.connect() as db:
        assert db.execute(
            "SELECT capture_id,state,turn_status,revision FROM sessions"
        ).fetchone() == ("capture", "NEEDS_INPUT", "WAITING", 2)
        assert db.execute("SELECT created_at FROM events LIMIT 1").fetchone()[0].tzinfo is not None
    with pytest.raises(psycopg.errors.GeneratedAlways), database.connect() as db:
        db.execute("UPDATE sessions SET state='DRAFTING'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), database.connect() as db:
        db.execute("DELETE FROM captures WHERE id='capture'")
    record.session_id, record.capture_id = ("invalid-session", "missing-capture")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await store.put(record)
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_invalid_document_and_orphan_event_are_rejected(database):
    record = SessionRecord(session_id="session", snapshot=SNAPSHOT, baseline_digest="baseline")
    body = record.model_dump(mode="json")
    for invalid in (
        [],
        {**body, "session_id": "different"},
        {**body, "revision": -1},
        {**body, "state": "UNKNOWN"},
        {**body, "turn_status": "UNKNOWN"},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), database.connect() as db:
            db.execute("INSERT INTO sessions(id,body) VALUES(%s,%s)", ("session", Jsonb(invalid)))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), database.connect() as db:
        db.execute("INSERT INTO events(session_id,kind,body) VALUES('missing','progress','{}')")
    with pytest.raises(psycopg.errors.CheckViolation), database.connect() as db:
        db.execute("UPDATE page_snapshots SET revision=0")


@pytest.mark.asyncio
async def test_request_relationships_and_explicit_cleanup(database, async_database):
    store = AsyncSessionStore(async_database)
    capture = CapturedPage(
        capture_id="capture",
        extension_id="a" * 32,
        snapshot=SNAPSHOT,
        created_at="2026-10-18T00:00:00+00:00",
    )
    await store.put_capture(capture, "capture-request", "capture-fingerprint")
    record = SessionRecord(
        session_id="session", capture_id="capture", snapshot=SNAPSHOT, baseline_digest="baseline"
    )
    await store.put(
        record, request=("create", "create-request", "fingerprint", {"session_id": "session"})
    )
    await store.put(
        record, request=("session:message", "message-request", "fingerprint", {"revision": 0})
    )
    with database.connect() as db:
        assert db.execute(
            "SELECT scope,session_id,capture_id FROM requests ORDER BY scope"
        ).fetchall() == [
            ("capture", None, "capture"),
            ("create", "session", None),
            ("session:message", "session", None),
        ]
        db.execute("INSERT INTO requests VALUES('legacy','legacy-request','digest','{}')")
        db.execute("DELETE FROM requests WHERE scope='legacy'")
    with pytest.raises(psycopg.errors.CheckViolation), database.connect() as db:
        db.execute(
            "INSERT INTO requests(scope,id,fingerprint,result) VALUES('create','bad-id','digest','{}')"
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), database.connect() as db:
        db.execute(
            "INSERT INTO requests(scope,id,fingerprint,result) VALUES('missing:message','id','digest','{}')"
        )
    await store.delete("session")
    await store.delete_capture("capture")
    with database.connect() as db:
        for table in ("sessions", "captures", "events", "requests"):
            assert (
                db.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(table))
                ).fetchone()[0]
                == 0
            )
