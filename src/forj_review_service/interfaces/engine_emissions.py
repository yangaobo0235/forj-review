"""Explicitly preview or rebuild the unique engine emission reference from the catalog."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from forj_review_service.contracts.engine_emissions import EMISSION_STANDARDS, engine_model_key
from forj_review_service.integrations.database import Database, review_database
from forj_review_service.integrations.engine_emissions import create_reference_table


def unique_engine_emissions(source_rows):
    standards = defaultdict(set)
    counts = Counter()
    all_models = set()
    for brand, model, standard, count in source_rows:
        key = engine_model_key(model, engine_brand=brand)
        if not key:
            continue
        all_models.add(key)
        if standard not in EMISSION_STANDARDS:
            continue
        standards[key].add(standard)
        counts[key, standard] += count
    mappings = [(key, next(iter(values)), counts[key, next(iter(values))])
                for key, values in sorted(standards.items()) if len(values) == 1]
    return mappings, {
        "mapping_rows": len(mappings),
        "excluded_ambiguous_models": sum(len(values) > 1 for values in standards.values()),
        "excluded_without_valid_standard": len(all_models - standards.keys()),
    }


def rebuild_reference(database: Database, *, apply: bool = False, report_path: Path | None = None):
    with database.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" +
                           ("" if apply else " READ ONLY"))
        if apply:
            # Maintenance is explicit and atomic. Concurrent rebuilds must not interleave.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                               "current_database()||'.'||current_schema()||'.engine_emission_standards',0))")
        source = connection.execute(
            "SELECT engine_brand,engine_type,emission_standard,count(*) "
            "FROM forj_vehicle_model_config GROUP BY 1,2,3",
        ).fetchall()
        mappings, counts = unique_engine_emissions(source)
        if not mappings:
            raise ValueError("源车型目录没有唯一有效映射，未修改参考表")
        present = connection.execute("SELECT to_regclass('engine_emission_standards')").fetchone()[0]
        previous = (connection.execute(
            "SELECT engine_model,emission_standard,source_config_count "
            "FROM engine_emission_standards ORDER BY engine_model",
        ).fetchall() if present else [])
        report = {"database": connection.info.dbname, "schema": database.schema,
                  "table": "engine_emission_standards", "applied": False,
                  "apply_requested": apply,
                  "source_rows": sum(row[3] for row in source), **counts,
                  "previous_mappings": previous, "mappings": mappings}
        # Preserve the previous mapping before changing an existing reference table.
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n",
                                   encoding="utf-8")
        if apply:
            create_reference_table(connection)
            connection.execute("DELETE FROM engine_emission_standards")
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO engine_emission_standards "
                    "(engine_model,emission_standard,source_config_count) VALUES(%s,%s,%s)", mappings,
                )
            stored = connection.execute(
                "SELECT engine_model,emission_standard,source_config_count "
                "FROM engine_emission_standards ORDER BY engine_model",
            ).fetchall()
            if sorted(stored) != mappings:
                raise ValueError("参考表写入后校验不一致，事务已回滚")
    report["applied"] = apply
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return {key: value for key, value in report.items() if key not in {"previous_mappings", "mappings"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="原子重建参考表；默认只预览")
    parser.add_argument("--report", type=Path, required=True, help="保存候选及原有映射，供追溯和恢复")
    args = parser.parse_args()
    print(json.dumps(rebuild_reference(review_database(), apply=args.apply, report_path=args.report),
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
