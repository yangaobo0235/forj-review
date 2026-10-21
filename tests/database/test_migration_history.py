"""Append-only upgrades, full-history validation and atomic failure recovery."""

import hashlib

import psycopg
import pytest

from forj_review_database import migrations, schema
from forj_review_database.migrations import Migration
from forj_review_service.interfaces.database_tools import migrate
from tests.support.postgres import isolated_database


def register(monkeypatch, tmp_path, *statements):
    upgrades = []
    for version, statement in enumerate(statements, 5):
        path = tmp_path / f"{version:03d}.sql"
        path.write_text(statement, encoding="utf8")
        upgrades.append(
            Migration(
                version, f"test_v{version}", path, hashlib.sha256(path.read_bytes()).hexdigest()
            )
        )
    monkeypatch.setattr(migrations, "UPGRADES", tuple(upgrades))


def test_baseline_edit_is_detected_even_when_only_comments_change(tmp_path, monkeypatch):
    path = tmp_path / "schema.sql"
    path.write_bytes(schema.SCHEMA_SQL.read_bytes() + b"\n-- changed comment\n")
    monkeypatch.setattr(schema, "SCHEMA_SQL", path)
    with pytest.raises(ValueError, match="基线被修改"):
        schema.schema_checksum()


def test_upgrade_then_validate_every_managed_history_entry(database, tmp_path, monkeypatch):
    register(
        monkeypatch, tmp_path, "ALTER TABLE ref_engine_emission ADD COLUMN migration_test text;"
    )
    with pytest.raises(ValueError, match="落后"):
        database.require_ready()
    report = migrate(database, backup_dir=tmp_path / "backup")
    assert report["version"] == 5
    database.require_ready()
    with database.connect() as db:
        db.execute("UPDATE sys_schema_migration SET checksum=%s WHERE version=4", ("0" * 64,))
    # A correct newest row must not hide a corrupted older managed row.
    with pytest.raises(ValueError, match="历史摘要"):
        database.require_ready()


def test_failed_multi_step_upgrade_rolls_back_ddl_and_ledger(database, tmp_path, monkeypatch):
    register(
        monkeypatch,
        tmp_path,
        "ALTER TABLE ref_engine_emission ADD COLUMN migration_test text;",
        "SELECT 1/0;",
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(database, backup_dir=tmp_path / "backup")
    with database.connect() as db:
        assert db.execute("SELECT max(version) FROM sys_schema_migration").fetchone() == (4,)
        assert db.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema=%s "
            "AND table_name='ref_engine_emission' AND column_name='migration_test'",
            (database.schema,),
        ).fetchone() == (0,)
    assert not (tmp_path / "backup/migration-record.json").exists()


def test_upgrade_cannot_change_protected_business_rows(database, tmp_path, monkeypatch):
    register(monkeypatch, tmp_path, "INSERT INTO forj_vehicle_brand(id) VALUES(999);")
    with pytest.raises(ValueError, match="受保护业务表"):
        migrate(database, backup_dir=tmp_path / "backup")
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM forj_vehicle_brand").fetchone() == (0,)
        assert db.execute("SELECT max(version) FROM sys_schema_migration").fetchone() == (4,)


def test_modified_upgrade_file_is_rejected_before_running(database, tmp_path, monkeypatch):
    register(monkeypatch, tmp_path, "SELECT 1;")
    (tmp_path / "005.sql").write_text("SELECT 2;", encoding="utf8")
    with pytest.raises(ValueError, match="文件摘要"):
        migrate(database, backup_dir=tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_current_schema_does_not_create_a_backup_for_a_noop(database, tmp_path):
    with pytest.raises(ValueError, match="无需迁移"):
        migrate(database, backup_dir=tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_empty_schema_initialization_applies_registered_upgrades(database, tmp_path, monkeypatch):
    register(
        monkeypatch, tmp_path, "ALTER TABLE ref_engine_emission ADD COLUMN migration_test text;"
    )
    with isolated_database(database.url) as initialized:
        initialized.require_ready()
        with initialized.connect() as db:
            assert db.execute("SELECT max(version) FROM sys_schema_migration").fetchone() == (5,)
            assert db.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema=%s "
                "AND table_name='ref_engine_emission' AND column_name='migration_test'",
                (initialized.schema,),
            ).fetchone() == (1,)
