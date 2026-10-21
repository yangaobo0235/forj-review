"""Explicit, backed-up application schema upgrades; source business tables are protected."""

import argparse
import gzip
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from psycopg import sql
from psycopg.rows import dict_row

from forj_review_database.migrations import migration_history, validate_history
from forj_review_database.schema import BASELINE_VERSION, SCHEMA_SQL, schema_checksum
from forj_review_service.businesses.page_config.state import ConfigurationState
from forj_review_service.config.database import load_connection_settings
from forj_review_service.integrations.database import Database
from forj_review_service.integrations.storage.configuration import publish_state, read_state
from forj_review_service.integrations.storage.migrations import apply_schema_comments

BUSINESS_TABLES = (
    "forj_vehicle_brand",
    "forj_vehicle_series",
    "forj_vehicle_model",
    "forj_vehicle_model_config",
)
FRAMEWORK_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")


def table_fingerprint(connection, schema, table):
    # Compute a deterministic digest on the server; only two scalar values
    # cross the network, even for the 148k-row vehicle configuration table.
    columns = connection.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid "
        "AND a.attnum=ANY(i.indkey) WHERE i.indrelid=to_regclass(%s) AND i.indisprimary ORDER BY a.attnum",
        (schema + "." + table,),
    ).fetchall()
    order = (
        sql.SQL(",").join(sql.Identifier(c[0]) for c in columns)
        if columns
        else sql.SQL("row_to_json(t)::text")
    )
    count, checksum = connection.execute(
        sql.SQL(
            "SELECT count(*),encode(sha256(convert_to(coalesce(string_agg("
            "encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex'),'' ORDER BY {}),''),'UTF8')),'hex') "
            "FROM {}.{} t"
        ).format(order, sql.Identifier(schema), sql.Identifier(table))
    ).fetchone()
    return {"rows": count, "sha256": checksum, "algorithm": "sha256-of-ordered-row-sha256"}


def protected_catalog(connection, schema):
    return connection.execute(
        "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY 1,2",
        (schema, list(BUSINESS_TABLES)),
    ).fetchall()


def migrate(database, *, backup_dir: Path, allow_public=False):
    if database.schema == "public" and not allow_public:
        raise ValueError("public 升级需要明确 --allow-public，本机只使用 local")
    with database.connect() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL statement_timeout=0")
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (database.schema + ":schema-migration",),
        )
        version = db.execute("SELECT max(version) FROM sys_schema_migration").fetchone()[0]
        history = migration_history()
        if version != 3:
            rows = db.execute(
                "SELECT version,checksum FROM sys_schema_migration ORDER BY version"
            ).fetchall()
            validate_history(rows, history)
            if version == history[-1].version:
                raise ValueError("当前结构已是最新版本，无需迁移")
        # This migration preserves all formal versions/releases. The former local
        # installation contains no sessions. Refuse instead of silently discarding
        # an active workflow when upgrading a different installation.
        if version == 3 and db.execute("SELECT count(*) FROM cfg_session").fetchone()[0]:
            raise ValueError("升级前请完成并清理旧会话；不会静默丢弃工作流")
        backup_dir.mkdir(parents=True, exist_ok=False)
        before = {t: table_fingerprint(db, database.schema, t) for t in BUSINESS_TABLES}
        triggers = protected_catalog(db, database.schema)
        tables = [
            r[0]
            for r in db.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY 1", (database.schema,)
            ).fetchall()
        ]
        saved = {}
        with db.cursor(row_factory=dict_row) as cur:
            for table in tables:
                if table in BUSINESS_TABLES:
                    continue
                with gzip.open(backup_dir / (table + ".copy.gz"), "wb") as output:
                    with db.cursor().copy(
                        sql.SQL("COPY {} TO STDOUT (FORMAT BINARY)").format(sql.Identifier(table))
                    ) as stream:
                        for block in stream:
                            output.write(block)
                cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
                saved[table] = cur.fetchall()
        (backup_dir / "rows-before.json").write_text(
            json.dumps(saved, default=str, ensure_ascii=False, indent=2), encoding="utf8"
        )
        catalog = db.execute(
            "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,a.attnum",
            (database.schema,),
        ).fetchall()
        (backup_dir / "catalog-before.json").write_text(
            json.dumps(
                {"columns": catalog, "protected": before, "triggers": triggers},
                default=str,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf8",
        )
        mapping = {}
        if version == 3:
            pages = {r["id"]: r for r in saved["cfg_page"]}
            documents = []
            version_keys = {}
            for v in saved["cfg_page_version"]:
                p = pages[v["page_id"]]
                d = {
                    **v["page_options"],
                    "schema_version": v["schema_version"],
                    "page_id": p["page_code"],
                    "business_type": p["business_type"],
                    "region": p["region"],
                    "version": v["version_no"],
                    "title": v["title"],
                }
                d["fields"] = [
                    {
                        **r["options"],
                        **{
                            k: r[c]
                            for k, c in [
                                ("key", "field_key"),
                                ("label", "label"),
                                ("reviewable", "reviewable"),
                                ("required", "required"),
                                ("writable", "writable"),
                                ("normalizer", "normalizer"),
                            ]
                        },
                    }
                    for r in sorted(saved["cfg_page_field"], key=lambda r: r["sort_order"])
                    if r["page_version_id"] == v["id"]
                ]
                d["materials"] = [
                    {
                        **r["options"],
                        "document_type": r["material_code"],
                        "label": r["label"],
                        "required": r["required"],
                        "minimum_count": r["minimum_count"],
                    }
                    for r in sorted(saved["cfg_page_material"], key=lambda r: r["sort_order"])
                    if r["page_version_id"] == v["id"]
                ]
                documents.append(d)
                version_keys[v["id"]] = (p["page_code"], v["version_no"])
            owned = [t for t in tables if t.startswith("cfg_") or t == "ref_dataset_import"]
            db.execute(sql.SQL("DROP TABLE ") + sql.SQL(",").join(map(sql.Identifier, owned)))
            ddl = SCHEMA_SQL.read_text(encoding="utf8")
            # Reuse both reference tables IN PLACE. In particular never drop/recreate
            # ref_vehicle_model_key's FK, which owns RI triggers on protected forj_.
            for table in ("sys_schema_migration", "ref_vehicle_model_key", "ref_engine_emission"):
                ddl = re.sub(r"CREATE TABLE " + table + r" \(.*?\);\n", "", ddl, flags=re.S)
            ddl = re.sub(r"CREATE INDEX idx_vehicle_model_key_codes[^;]+;\n", "", ddl)
            db.execute("DROP FUNCTION guard_configuration_history(),guard_configuration_append()")
            db.execute(ddl, prepare=False)
            from forj_review_database.models import DatasetImport

            for r in saved["ref_dataset_import"]:
                m = r["metadata"]
                exported = m.get("exported_at")
                db.orm.add(
                    DatasetImport(
                        dataset_code=r["dataset_code"],
                        source_name=m.get("source_access", "legacy-import"),
                        source_environment=m.get("source_environment"),
                        source_exported_at=datetime.fromisoformat(exported) if exported else None,
                        source_tables=list(m.get("tables", {}))
                        or ([m["source_table"]] if m.get("source_table") else []),
                        source_digest=r["source_digest"],
                        format_version=r["format_version"],
                        builder_version="1",
                        source_row_count=m.get("source_rows"),
                        row_count=r["row_count"],
                        excluded_ambiguous_count=m.get("excluded_ambiguous_models"),
                        excluded_invalid_count=m.get("excluded_without_valid_standard"),
                        imported_by="migration",
                        imported_at=r["imported_at"],
                    )
                )
            db.orm.flush()
            revision = 0
            for release in sorted(saved["cfg_release"], key=lambda r: r["id"]):
                active = dict(
                    version_keys[r["page_version_id"]]
                    for r in saved["cfg_release_item"]
                    if r["release_id"] == release["id"]
                )
                state = ConfigurationState(tuple(documents), active, revision)
                rid = publish_state(
                    db,
                    state,
                    operation_id=release["operation_id"],
                    source="MIGRATION",
                    operator="migration",
                    change_reason="v3 到 v4 结构迁移；原始证据及摘要见维护备份",
                )
                mapping[release["id"]] = rid
                revision += 1
            head = next(r for r in saved["cfg_head"] if r["id"] == 1)
            db.execute(
                "UPDATE cfg_head SET release_id=%s,revision=%s WHERE id=1",
                (mapping.get(head["release_id"]), max(head["revision"], revision)),
            )
            db.execute(
                "INSERT INTO sys_schema_migration(version,name,checksum) VALUES(%s,%s,%s)",
                (BASELINE_VERSION, "bounded_configuration_workflow", schema_checksum()),
            )
        for migration in history:
            if migration.version > max(version, BASELINE_VERSION):
                db.execute(migration.read_sql(), prepare=False)
                db.execute(
                    "INSERT INTO sys_schema_migration(version,name,checksum) VALUES(%s,%s,%s)",
                    (migration.version, migration.name, migration.checksum),
                )
        validate_history(
            db.execute(
                "SELECT version,checksum FROM sys_schema_migration ORDER BY version"
            ).fetchall(),
            history,
            require_current=True,
        )
        db.orm.expire_all()
        apply_schema_comments(db)
        stored = read_state(db)
        after = {t: table_fingerprint(db, database.schema, t) for t in BUSINESS_TABLES}
        if before != after or triggers != protected_catalog(db, database.schema):
            raise ValueError("受保护业务表校验失败，整笔迁移回滚")
        report = {
            "schema": database.schema,
            "version": history[-1].version,
            "pages": len(stored.active),
            "versions": len(stored.definitions),
            "release_mapping": mapping,
            "protected": after,
            "backup": str(backup_dir),
            "created_at": datetime.now(UTC).isoformat(),
        }
    (backup_dir / "migration-record.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "migrate"))
    parser.add_argument("--schema", required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-public", action="store_true")
    args = parser.parse_args()
    database = Database(load_connection_settings().conninfo(), args.schema)
    if args.action == "check":
        database.require_ready()
        report = {"schema": args.schema, "ready": True}
    else:
        if not args.apply or not args.backup_dir:
            parser.error("迁移需要 --apply 和新的 --backup-dir")
        report = migrate(database, backup_dir=args.backup_dir, allow_public=args.allow_public)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
