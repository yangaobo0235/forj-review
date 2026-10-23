"""Relational ownership, immutable history, rollback and schema isolation."""

from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from forj_review_service.integrations.database import Database
from forj_review_service.integrations.storage.configuration import (
    historical_selection,
    publish_state,
    read_state,
)
from forj_review_service.interfaces.database_tools import migrate
from tests.support.postgres import publish_validated_test_state, seed_complete_pages


def test_field_edit_reuses_other_page_versions_and_release_rollback_preserves_history(database):
    seed_complete_pages(database)
    with database.connect() as db:
        before = read_state(db)
        old_ids = db.execute(
            "SELECT p.page_code,v.id FROM cfg_release_item i JOIN cfg_page p ON p.id=i.page_id "
            "JOIN cfg_page_version v ON v.id=i.page_version_id WHERE i.release_id=%s",
            (before.release_id,),
        ).fetchall()
        draft = {
            **before.source("transfer:default", "1.0"),
            "version": "relational-test",
            "active": True,
        }
        _, staged = before.stage(draft)
        published = publish_validated_test_state(
            db, staged, operation_id="fieldedit", operation_type="PUBLISH"
        )
    with database.connect() as db:
        after = read_state(db)
        new_ids = dict(
            db.execute(
                "SELECT p.page_code,i.page_version_id FROM cfg_release_item i JOIN cfg_page p ON p.id=i.page_id "
                "WHERE i.release_id=%s",
                (published,),
            ).fetchall()
        )
        assert all(new_ids[code] == vid for code, vid in old_ids if code != "transfer:default")
        assert len(after.definitions) == len(before.definitions) + 1
        assert db.execute("SELECT count(*) FROM cfg_release").fetchone()[0] == 2
        target = historical_selection(db, after, before.release_id)
        rollback = publish_validated_test_state(
            db,
            target,
            operation_id="rollback",
            operation_type="ROLLBACK",
            rollback_target=before.release_id,
        )
        assert rollback != before.release_id
    with database.connect() as db:
        final = read_state(db)
        assert final.active == before.active
        assert final.revision == before.revision + 2
        assert len(final.definitions) == len(after.definitions)


@pytest.mark.parametrize(
    "table",
    [
        "cfg_page",
        "cfg_page_version",
        "cfg_page_field",
        "cfg_page_material",
        "cfg_release",
        "cfg_release_item",
    ],
)
def test_history_cannot_be_updated_or_deleted(database, table):
    seed_complete_pages(database)
    with pytest.raises(psycopg.errors.RaiseException), database.connect() as db:
        db.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))


def test_cross_page_release_and_stale_publication_are_rejected(database):
    seed_complete_pages(database)
    with database.connect() as db:
        state = read_state(db)
        publish_state(db, state, operation_id="another")
    with pytest.raises(ValueError, match="基线"), database.connect() as db:
        publish_state(db, state, operation_id="stale")
    with database.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM cfg_release WHERE operation_id='stale'").fetchone()[0]
            == 0
        )
        pages = db.execute("SELECT page_id,id FROM cfg_page_version ORDER BY page_id").fetchall()
        first = pages[0]
        current = read_state(db)
        omitted = db.execute("SELECT page_code FROM cfg_page WHERE id=%s", (first[0],)).fetchone()[
            0
        ]
        partial = replace(current, active={p: v for p, v in current.active.items() if p != omitted})
        rid = publish_state(db, partial, operation_id="crosspage")
        other = next(p for p in pages if p[0] != first[0])
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
            db.execute("INSERT INTO cfg_release_item VALUES(%s,%s,%s)", (rid, first[0], other[1]))


def test_public_upgrade_requires_explicit_approval(database, tmp_path):
    with pytest.raises(ValueError, match="allow-public"):
        migrate(Database(database.url, "public"), backup_dir=tmp_path / "backup")


def test_missing_target_never_falls_back_to_public(database):
    with pytest.raises(ValueError, match="不会回退 public"):
        Database(database.url, "forj_absent_" + uuid4().hex[:12]).require_ready()


def test_operation_retries_are_idempotent_and_different_payload_is_rejected(database):
    seed_complete_pages(database)
    with database.connect() as db:
        original = read_state(db)
        rid = publish_state(db, original, operation_id="retry")
        assert publish_state(db, original, operation_id="retry") == rid
    with pytest.raises(ValueError, match="其他内容"), database.connect() as db:
        publish_state(db, replace(original, active={}), operation_id="retry")
