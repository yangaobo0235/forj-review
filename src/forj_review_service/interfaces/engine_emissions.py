"""Explicitly preview or rebuild the unique engine emission reference from the catalog."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import delete, func, insert, select

from forj_review_database.models import DatasetImport, EngineEmission, VehicleModelConfig
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
            connection.orm.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(
                func.current_database() + "." + func.current_schema() + ".ref_engine_emission", 0))))
        source = [tuple(row) for row in connection.orm.execute(select(
            VehicleModelConfig.engine_brand, VehicleModelConfig.engine_type,
            VehicleModelConfig.emission_standard, func.count()).group_by(
                VehicleModelConfig.engine_brand, VehicleModelConfig.engine_type,
                VehicleModelConfig.emission_standard)).all()]
        mappings, counts = unique_engine_emissions(source)
        source_digest = hashlib.sha256(json.dumps(
            sorted(source, key=lambda row: json.dumps(row, ensure_ascii=False)),
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if not mappings:
            raise ValueError("源车型目录没有唯一有效映射，未修改参考表")
        present = connection.orm.scalar(select(func.to_regclass("ref_engine_emission")))
        query = select(EngineEmission.engine_model, EngineEmission.emission_standard,
                       EngineEmission.source_config_count).order_by(EngineEmission.engine_model)
        previous = [tuple(row) for row in connection.orm.execute(query).all()] if present else []
        report = {"database": connection.info.dbname, "schema": database.schema,
                  "table": "ref_engine_emission", "applied": False,
                  "apply_requested": apply,
                  "source_digest": source_digest,
                  "source_rows": sum(row[3] for row in source), **counts,
                  "previous_mappings": previous, "mappings": mappings}
        # Preserve the previous mapping before changing an existing reference table.
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n",
                                   encoding="utf-8")
        if apply:
            create_reference_table(connection)
            connection.orm.execute(delete(EngineEmission))
            connection.orm.execute(insert(EngineEmission), [dict(
                engine_model=model, emission_standard=standard, source_config_count=count)
                for model, standard, count in mappings])
            stored = [tuple(row) for row in connection.orm.execute(query).all()]
            if sorted(stored) != mappings:
                raise ValueError("参考表写入后校验不一致，事务已回滚")
            connection.orm.add(DatasetImport(dataset_code="engine_emission",
                source_digest=source_digest, format_version="1", row_count=len(mappings),
                source_name="forj_vehicle_model_config", source_tables=["forj_vehicle_model_config"],
                builder_version="1", source_row_count=report["source_rows"],
                excluded_ambiguous_count=counts["excluded_ambiguous_models"],
                excluded_invalid_count=counts["excluded_without_valid_standard"], imported_by="local"))
            connection.orm.flush()
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
