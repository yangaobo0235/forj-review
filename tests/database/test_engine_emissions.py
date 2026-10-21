"""Reference import and exact review lookup against an isolated PostgreSQL schema."""

import json

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError

from forj_review_service.integrations.database import Database
from forj_review_service.integrations.engine_emissions import (
    EngineEmissionLookupUnavailable,
    lookup_engine_emissions,
)
from forj_review_service.interfaces.engine_emissions import rebuild_reference
from tests.support.emissions import observation, run


@pytest.fixture
def source_catalog(database):
    with database.connect() as connection:
        connection.execute("DROP TABLE forj_vehicle_model_config")
        connection.execute(
            "CREATE TABLE forj_vehicle_model_config "
            "(engine_brand TEXT,engine_type TEXT,emission_standard TEXT)"
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO forj_vehicle_model_config VALUES(%s,%s,%s)",
                [
                    ("全柴", "全柴Q23-132E60", "国六"),
                    ("全柴", " Ｑ２３－１３２Ｅ６０ ", "国六"),
                    ("玉柴", "玉柴YC4FA130-50", "国五"),
                    (None, "YC4FA130-50", "国四"),
                    (None, "OLDMODEL", "国二"),
                    (None, "OTHER", "京六"),
                    (None, "Q23-132E60", None),
                    (None, "", "国五"),
                    (None, "X' OR 1=1--", "国三"),
                ],
            )
    return Database(database.url, database.schema)


def test_import_only_unique_valid_values_and_exact_runtime_lookup(source_catalog, tmp_path):
    preview = rebuild_reference(source_catalog, report_path=tmp_path / "preview.json")
    assert preview["mapping_rows"] == 2
    assert preview["excluded_ambiguous_models"] == 1
    assert preview["excluded_without_valid_standard"] == 2
    assert not preview["applied"]
    with source_catalog.connect() as connection:
        assert connection.execute("SELECT count(*) FROM ref_dataset_import").fetchone() == (0,)
    assert lookup_engine_emissions(["Q23-132E60"], database=source_catalog) == {}
    result = rebuild_reference(source_catalog, apply=True, report_path=tmp_path / "apply.json")
    assert result["applied"]
    assert lookup_engine_emissions(
        ["全柴 Ｑ２３－１３２Ｅ６０", "Q23-132E60A", "YC4FA130-50", "OLDMODEL", "X' OR 1=1--"],
        database=source_catalog,
    ) == {"Q23-132E60": "国六", "X'OR1=1--": "国三"}
    with source_catalog.connect() as connection:
        assert connection.execute(
            "SELECT source_config_count FROM ref_engine_emission WHERE engine_model='Q23-132E60'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT obj_description('ref_engine_emission'::regclass)"
        ).fetchone()[0]
        assert connection.execute("SELECT count(*) FROM forj_vehicle_model_config").fetchone() == (
            9,
        )
        imported = connection.execute(
            "SELECT source_digest,row_count,source_row_count FROM ref_dataset_import "
            "WHERE dataset_code='engine_emission'",
        ).fetchone()
        assert imported[:2] == (result["source_digest"], 2)
        assert imported[2] == 9
    backup = json.loads((tmp_path / "apply.json").read_text(encoding="utf-8"))
    assert backup["applied"] and backup["previous_mappings"] == []


def test_refresh_removes_newly_ambiguous_models_and_rolls_back_failed_import(
    source_catalog, tmp_path
):
    rebuild_reference(source_catalog, apply=True)
    with source_catalog.connect() as connection:
        connection.execute("INSERT INTO forj_vehicle_model_config VALUES(NULL,'Q23-132E60','国五')")
    rebuild_reference(source_catalog, apply=True, report_path=tmp_path / "refresh.json")
    assert lookup_engine_emissions(["Q23-132E60"], database=source_catalog) == {}
    assert (
        len(
            json.loads((tmp_path / "refresh.json").read_text(encoding="utf-8"))["previous_mappings"]
        )
        == 2
    )
    with source_catalog.connect() as connection:
        connection.execute(
            "ALTER TABLE ref_engine_emission ADD CONSTRAINT reject_bad CHECK(engine_model<>'BAD')"
        )
        connection.execute("INSERT INTO forj_vehicle_model_config VALUES(NULL,'BAD','国四')")
    with pytest.raises(IntegrityError) as rejected:
        rebuild_reference(source_catalog, apply=True, report_path=tmp_path / "failed.json")
    assert isinstance(rejected.value.orig, psycopg.errors.CheckViolation)
    assert lookup_engine_emissions(["X' OR 1=1--"], database=source_catalog) == {
        "X'OR1=1--": "国三"
    }
    assert not json.loads((tmp_path / "failed.json").read_text(encoding="utf-8"))["applied"]
    with source_catalog.connect() as connection:
        assert connection.execute("SELECT count(*) FROM ref_dataset_import").fetchone() == (2,)


def test_real_rule_uses_reference_and_degrades_when_table_unavailable(source_catalog):
    rebuild_reference(source_catalog, apply=True)
    check = run(
        "国六",
        [
            observation("old_vehicle.engine_model", "Q23-132E60"),
            observation("old_vehicle.brand", "一汽解放"),
        ],
    )
    assert check.status == "MATCH"
    assert check.details["standard"] == "国六"
    check = run("国五", [observation("old_vehicle.engine_model", "YC4FA130-50")])
    # 源目录多值型号不入精确表；独立系列规则仍可兜底，不使用目录多数值。
    assert check.status == "MATCH"
    assert check.details["method"] == "pattern"
    assert check.details["lookup_results"] == {}
    check = run("国四", [observation("old_vehicle.engine_model", "SC9DK240Q4")])
    assert check.status == "MATCH"
    assert check.details["rule_results"]["SC9DK240Q4"]["marker"] == "Q4"
    with source_catalog.connect() as connection:
        connection.execute("DROP TABLE ref_engine_emission")
    with pytest.raises(EngineEmissionLookupUnavailable):
        lookup_engine_emissions(["Q23-132E60"], database=source_catalog)
    check = run("国六", [observation("old_vehicle.engine_model", "Q23-132E60")])
    assert check.status == "INSUFFICIENT"
    assert "读取失败" in check.reason
    check = run("国四", [observation("old_vehicle.engine_model", "SC9DK240Q4")])
    assert check.status == "INSUFFICIENT"
    assert "读取失败" in check.reason
