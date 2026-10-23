"""Upgrade a real v3 catalog and verify protected parent RI triggers in place."""

import json
import re
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from forj_review_database.migrations import migration_history
from forj_review_service.businesses.page_config.inspection import digest
from forj_review_service.integrations.storage.configuration import read_state
from forj_review_service.interfaces.database_tools import (
    BUSINESS_TABLES,
    migrate,
    protected_catalog,
    table_fingerprint,
)
from tests.support.postgres import complete_test_pages


def install_v3(database):
    with database.connect() as db:
        owned = db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s AND (tablename LIKE 'cfg_%%' OR tablename IN ('ref_dataset_import','sys_schema_migration'))",
            (database.schema,),
        ).fetchall()
        db.execute(sql.SQL("DROP TABLE ") + sql.SQL(",").join(sql.Identifier(t[0]) for t in owned))
        db.execute(
            "DROP FUNCTION guard_configuration_history(),guard_configuration_append(),guard_validation_delete(),guard_release_validation(),guard_task_capture_page()"
        )
        ddl = Path("tests/fixtures/database/v3.sql").read_text(encoding="utf8")
        for table in ("ref_vehicle_model_key", "ref_engine_emission"):
            ddl = re.sub(r"CREATE TABLE " + table + r" \(.*?\);\n", "", ddl, flags=re.S)
        ddl = re.sub(r"CREATE INDEX idx_vehicle_model_key_codes[^;]+;\n", "", ddl)
        db.execute(ddl, prepare=False)
        db.execute(
            "INSERT INTO sys_schema_migration VALUES(3,'v3',%s,CURRENT_TIMESTAMP)", ("0" * 64,)
        )
        state = complete_test_pages()
        selection = []
        for d in state.definitions:
            pid = db.execute(
                "INSERT INTO cfg_page(page_code,business_type,region) VALUES(%s,%s,%s) RETURNING id",
                (d["page_id"], d["business_type"], d["region"]),
            ).fetchone()[0]
            options = {
                k: v
                for k, v in d.items()
                if k
                not in {
                    "schema_version",
                    "page_id",
                    "business_type",
                    "region",
                    "version",
                    "title",
                    "fields",
                    "materials",
                    "actions",
                }
            }
            vid = db.execute(
                "INSERT INTO cfg_page_version(page_id,version_no,schema_version,title,page_options,content_digest,created_by) VALUES(%s,%s,'2.0',%s,%s,%s,'test') RETURNING id",
                (pid, d["version"], d["title"], Jsonb(options), digest(d)),
            ).fetchone()[0]
            for i, f in enumerate(d["fields"]):
                extras = {
                    k: v
                    for k, v in f.items()
                    if k not in {"key", "label", "reviewable", "required", "writable", "normalizer"}
                }
                db.execute(
                    "INSERT INTO cfg_page_field(page_version_id,field_key,label,sort_order,reviewable,required,writable,normalizer,options) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        vid,
                        f["key"],
                        f["label"],
                        i,
                        f["reviewable"],
                        f["required"],
                        f["writable"],
                        f["normalizer"],
                        Jsonb(extras),
                    ),
                )
            for i, m in enumerate(d["materials"]):
                extras = {
                    k: v
                    for k, v in m.items()
                    if k not in {"document_type", "label", "required", "minimum_count"}
                }
                db.execute(
                    "INSERT INTO cfg_page_material(page_version_id,material_code,label,sort_order,required,minimum_count,options) VALUES(%s,%s,%s,%s,false,1,%s)",
                    (vid, m["document_type"], m["label"], i, Jsonb(extras)),
                )
            selection.append((pid, vid))
        rid = db.execute(
            "INSERT INTO cfg_release(release_no,operation_type,source,operation_id,request_digest,manifest_digest,validation_summary,created_by) VALUES('initial','IMPORT','test','migrationtest',%s,%s,'{}','test') RETURNING id",
            ("0" * 64, "0" * 64),
        ).fetchone()[0]
        for pid, vid in selection:
            db.execute("INSERT INTO cfg_release_item VALUES(%s,%s,%s)", (rid, pid, vid))
        db.execute("UPDATE cfg_head SET release_id=%s,revision=1", (rid,))


def test_v3_upgrade_preserves_business_and_reference_fk(database, tmp_path):
    install_v3(database)
    with database.connect() as db:
        before = {t: table_fingerprint(db, database.schema, t) for t in BUSINESS_TABLES}
        triggers = protected_catalog(db, database.schema)
    report = migrate(database, backup_dir=tmp_path / "backup")
    assert report["version"] == migration_history()[-1].version
    database.require_ready()
    with database.connect() as db:
        assert {t: table_fingerprint(db, database.schema, t) for t in BUSINESS_TABLES} == before
        assert protected_catalog(db, database.schema) == triggers
        assert len(read_state(db).active) == 4
        assert db.execute("SELECT count(*) FROM cfg_validation_run").fetchone()[0] == 1
        assert db.execute(
            "SELECT page_options ? 'settings' FROM cfg_page_version LIMIT 1"
        ).fetchone() == (False,)
        assert (
            db.execute("SELECT count(*) FROM sys_schema_migration").fetchone()[0]
            == len(migration_history()) + 1
        )
    assert (
        json.loads((tmp_path / "backup/migration-record.json").read_text(encoding="utf8"))["pages"]
        == 4
    )
