"""经操作人员授权临时导出车型目录；不修改审核服务的运行时连接配置。

凭据只从指定 Spring datasource 配置读取，不写入日志或导出文件。
输出四张目录表的 SQLite 快照、统计校验、示例匹配结果及可搬运 ZIP。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pymysql

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from forj_review_assistant.contracts.vehicle_models import (  # noqa: E402
    VehicleModelSearch,
    engine_code,
    model_code,
)
from forj_review_assistant.integrations.vehicle_models import (  # noqa: E402
    _SQL,
    candidates_from_rows,
)

TABLES = ("forj_vehicle_brand", "forj_vehicle_series", "forj_vehicle_model",
          "forj_vehicle_model_config")
EXCLUDED = {"create_by", "update_by"}
EXAMPLES = (("DFH4250A4", "DCI450-51"), ("CA4255P25K2T1E6A80", "CA6DM2-42E6"),
            ("ZZ4256W324HF1B", "MC13H.51-61"))


def source_settings(config: Path, endpoint_reference: Path | None) -> dict:
    text = config.read_text("utf-8-sig")
    match = re.search(r"^  datasource:\s*\n(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    if not match:
        raise ValueError("未找到 Spring datasource 配置")

    def setting(name):
        value = re.search(r"^    " + name + r":\s*(.*?)\s*$", match[1], re.M)
        if not value:
            raise ValueError("数据源配置缺少必要参数")
        value = value[1]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or "${" in value:
            raise ValueError("数据源配置仍有未解析参数")
        return value

    endpoint = urlsplit(setting("url").removeprefix("jdbc:"))
    if endpoint_reference:
        # 只接受配置中同一 RDS 实例的公开地址，不猜测或扫描其他数据库。
        reference = re.search(r'jdbc:mysql://[^"\s]+', endpoint_reference.read_text("utf-8-sig"))
        if not reference:
            raise ValueError("参考文件未找到数据库地址")
        alternate = urlsplit(reference[0].removeprefix("jdbc:"))
        if (alternate.path != endpoint.path or alternate.port != endpoint.port
                or (alternate.hostname or "").split(".")[0].removesuffix("io")
                != (endpoint.hostname or "").split(".")[0]
                or not (alternate.hostname or "").endswith(".mysql.rds.aliyuncs.com")
                or not (endpoint.hostname or "").endswith(".mysql.rds.aliyuncs.com")):
            raise ValueError("参考地址与指定的数据源实例不一致")
        endpoint = alternate
    return {"host": endpoint.hostname, "port": endpoint.port or 3306,
            "database": endpoint.path.lstrip("/"), "user": setting("username"),
            "password": setting("password")}


def ident(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("目录存在不支持的字段名")
    return f'"{value}"'


def cell(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def export_tables(remote, local: sqlite3.Connection) -> dict:
    tables = {}
    with remote.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cursor.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s,%s,%s)", TABLES)
        engines = dict(cursor.fetchall())
        if set(engines) != set(TABLES) or any(value != "InnoDB" for value in engines.values()):
            raise ValueError("目录表缺失或不支持一致性快照，停止导出")
        for table in TABLES:
            cursor.execute(f"SHOW COLUMNS FROM {ident(table)}".replace('"', '`'))
            schema = [row for row in cursor.fetchall() if row[0] not in EXCLUDED]
            columns = [row[0] for row in schema]
            definitions = [f"{ident(row[0])} " + ("INTEGER" if re.match(r"^(?:big|small|tiny|medium)?int\b", row[1]) else "TEXT") for row in schema]
            primary = [ident(row[0]) for row in schema if row[3] == "PRI"]
            if primary:
                definitions.append(f"PRIMARY KEY ({','.join(primary)})")
            local.execute(f"CREATE TABLE {ident(table)} ({','.join(definitions)})")
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            expected = cursor.fetchone()[0]
            insert = f"INSERT INTO {ident(table)} VALUES ({','.join('?' for _ in columns)})"
            total = 0
            with remote.cursor(pymysql.cursors.SSCursor) as stream:
                stream.execute(f"SELECT {','.join('`'+c+'`' for c in columns)} FROM `{table}`")
                while rows := stream.fetchmany(1000):
                    local.executemany(insert, [tuple(cell(value) for value in row) for row in rows])
                    total += len(rows)
                    local.commit()
            if total != expected:
                raise ValueError("导出条数与一致性快照不一致")
            tables[table] = {"rows": total, "columns": columns, "engine": engines[table]}
            print(f"Exported {table}: {total} rows", flush=True)
    return tables


def validate(local: sqlite3.Connection) -> dict:
    local.executescript("""
      CREATE INDEX model_announcement ON forj_vehicle_model(announcement_model);
      CREATE INDEX model_same_name ON forj_vehicle_model(brand_id,series_id,model_name,del_flag,operate);
      CREATE TABLE catalog_keys(model_id INTEGER PRIMARY KEY, announcement_code TEXT, engine_code TEXT);
    """)
    cursor = local.execute("SELECT vm.model_id,vm.announcement_model,c.engine_type "
                           "FROM forj_vehicle_model vm LEFT JOIN forj_vehicle_model_config c "
                           "ON c.model_id=vm.model_id WHERE vm.del_flag=0 AND vm.operate=1")
    while rows := cursor.fetchmany(1000):
        local.executemany("INSERT INTO catalog_keys VALUES (?,?,?)",
                          [(row[0], model_code(row[1]), engine_code(row[2])) for row in rows])
    local.execute("CREATE INDEX catalog_codes ON catalog_keys(announcement_code,engine_code)")
    local.commit()
    counts = {
        "active_models": "SELECT COUNT(*) FROM catalog_keys",
        "active_models_missing_config": "SELECT COUNT(*) FROM catalog_keys k LEFT JOIN forj_vehicle_model_config c ON c.model_id=k.model_id WHERE c.model_id IS NULL",
        "active_models_missing_brand": "SELECT COUNT(*) FROM catalog_keys k JOIN forj_vehicle_model vm ON vm.model_id=k.model_id LEFT JOIN forj_vehicle_brand b ON b.id=vm.brand_id AND b.del_flag=0 WHERE b.id IS NULL",
        "active_models_missing_series": "SELECT COUNT(*) FROM catalog_keys k JOIN forj_vehicle_model vm ON vm.model_id=k.model_id LEFT JOIN forj_vehicle_series s ON s.id=vm.s_id AND s.del_flag=0 WHERE s.id IS NULL",
        "active_models_missing_codes": "SELECT COUNT(*) FROM catalog_keys WHERE announcement_code='' OR engine_code=''",
        "orphan_configs": "SELECT COUNT(*) FROM forj_vehicle_model_config c LEFT JOIN forj_vehicle_model vm ON vm.model_id=c.model_id WHERE vm.model_id IS NULL",
    }
    report = {name: local.execute(sql).fetchone()[0] for name, sql in counts.items()}
    report["integrity_check"] = local.execute("PRAGMA integrity_check").fetchone()[0]
    if report["integrity_check"] != "ok":
        raise ValueError("SQLite 完整性检查失败")
    return report


def examples(local: sqlite3.Connection) -> list:
    local.row_factory = sqlite3.Row
    result = []
    for announcement, engine in EXAMPLES:
        scopes = local.execute(
            "SELECT DISTINCT vm.brand_id,vm.series_id,vm.type_level,vb.brand_name,vs.series_name "
            "FROM catalog_keys k JOIN forj_vehicle_model vm ON vm.model_id=k.model_id "
            "JOIN forj_vehicle_brand vb ON vb.id=vm.brand_id AND vb.del_flag=0 "
            "JOIN forj_vehicle_series vs ON vs.id=vm.s_id AND vs.del_flag=0 "
            "WHERE k.announcement_code=? AND k.engine_code=?",
            (announcement, engine),
        ).fetchall()
        for scope in scopes:
            query = VehicleModelSearch(announcementModel=announcement, engineModel=engine,
                                       pageScope={"brandName": scope["brand_name"],
                                                  "seriesName": scope["series_name"],
                                                  "typeLevel": scope["type_level"]})
            rows = [dict(row) for row in local.execute(
                _SQL, (query.announcementModel, query.engineModel, scope["brand_id"],
                       scope["series_id"], scope["type_level"]))]
            candidates = candidates_from_rows(rows, query)
            result.append({"query": query.model_dump(), "count": len(candidates),
                           "candidates": [c.model_dump() for c in candidates]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--endpoint-reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    remote = None
    try:
        settings = source_settings(args.source_config, args.endpoint_reference)
        remote = pymysql.connect(**settings, charset="utf8mb4", connect_timeout=8,
                                 read_timeout=120, write_timeout=8, autocommit=False)
        sqlite_path = output / "vehicle-model-catalog.sqlite"
        with closing(sqlite3.connect(sqlite_path)) as local:
            tables = export_tables(remote, local)
            # 后续所有校验和样例匹配仅查询本地导出库。
            remote.rollback()
            remote.close()
            remote = None
            report = validate(local)
            samples = examples(local)
            manifest = {"format": "forj-vehicle-model-catalog", "version": 1,
                        "exported_at": datetime.now(UTC).isoformat(),
                        "source_environment": "test", "source_access": "temporary_read_only_export",
                        "runtime_requires_source_database": False, "tables": tables,
                        "excluded_columns": sorted(EXCLUDED), "validation": report}
            local.execute("CREATE TABLE catalog_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            local.execute("INSERT INTO catalog_metadata VALUES (?,?)",
                          ("manifest", json.dumps(manifest, ensure_ascii=False)))
            local.commit()
        for filename, value in (("manifest.json", manifest), ("example-matches.json", samples)):
            (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=cell) + "\n", "utf-8")
        (output / "README.md").write_text(
            "# 车型目录离线导出包\n\n"
            "本包由临时只读连接测试库导出，不包含连接地址、账号或密码。\n\n"
            "- vehicle-model-catalog.sqlite：品牌、车系、车型、完整车型配置四张目录表；保留有效状态标记，查询时仅使用 del_flag=0 且 operate=1 的车型。\n"
            "- catalog_keys：有效车型的规范化整车型号、发动机型号及检索索引。\n"
            "- manifest.json：导出时间、表结构、数量与关联问题统计。异常记录保留原样，不编造补齐。\n"
            "- example-matches.json：断开源库后执行的本地匹配结果及完整参数。\n\n"
            "保留原系统品牌、内部车系 ID(id/s_id)、外部车系 ID(series_id)、车型 ID，供以后接入审核助手；本次仅导出，未切换运行中的查询服务。\n"
            "原表的 create_by/update_by 不导出；不包含车辆申请、客户或证件资料。\n", "utf-8")
        files = sorted(path for path in output.iterdir() if path.is_file())
        checksums = {}
        for path in files:
            with path.open("rb") as stream:
                checksums[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        (output / "sha256.json").write_text(json.dumps(checksums, indent=2) + "\n", "utf-8")
        archive = output.with_suffix(".zip")
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(output.iterdir()):
                bundle.write(path, path.name)
        print(json.dumps({"output": str(output), "archive": str(archive), "validation": report,
                          "examples": [{"query": s["query"], "count": s["count"]} for s in samples]}, ensure_ascii=False), flush=True)
    except pymysql.MySQLError as exc:
        print(f"Export failed: database driver code {exc.args[0] if exc.args else 'unknown'}; no credentials logged", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if remote:
            remote.rollback()
            remote.close()


if __name__ == "__main__":
    main()
