"""Synthetic PostgreSQL catalog and protocol tests; no source-business database access."""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError

from forj_review_service.bootstrap.application import create_app
from forj_review_service.contracts.vehicle_models import (
    VehicleModelSearch,
    engine_code,
    model_code,
)
from forj_review_service.integrations.database import Database
from forj_review_service.integrations.vehicle_models import (
    VehicleModelCatalog,
    VehicleModelCatalogUnavailable,
    candidates_from_rows,
)
from forj_review_service.interfaces.http.vehicle_models import model_catalog
from forj_review_service.runtime.review_service import ReviewService

SCOPE = {"brandName": "示例品牌", "seriesName": "示例车系", "typeLevel": "牵引车"}
QUERY = {"announcementModel": "MODEL100A80", "engineModel": "ENGINE42-E6", "pageScope": SCOPE}


def row(identifier=1, **changes):
    return (
        dict(
            model_id=identifier,
            model_name=f"示例完整车型 6X4 手动版 {identifier}",
            brand_id=2,
            series_id=3,
            brand_name="示例品牌",
            series_name="示例车系",
            type_level="牵引车",
            announcement_model="MODEL100A80",
            config_id=identifier,
            config_announcement="MODEL100A80",
            drive_form="6X4",
            engine_type="ENGINE42-E6",
            max_hp=420,
        )
        | changes
    )


@pytest.mark.parametrize("size", [4, 9])
def test_exact_engine_filter_keeps_every_sales_variant_and_ids(size):
    rows = [row(i) for i in range(size)]
    rows.extend([row(99, engine_brand="其他品牌")])
    rows[-1]["engine_type"] = "ENGINE42-E6A"
    result = candidates_from_rows(rows, VehicleModelSearch(**QUERY))
    assert [c.modelId for c in result] == [str(i) for i in range(size)]
    assert all(c.modelName.startswith("示例完整车型") for c in result)


def test_search_normalizes_case_spaces_and_engine_brand_but_preserves_suffix():
    query = VehicleModelSearch(
        announcementModel=" model100a80 ", engineModel="锡柴 engine42-e6 ", pageScope=SCOPE
    )
    assert query.model_dump() == QUERY
    example = row()
    example["engine_type"] = "锡柴 ENGINE42-E6"
    assert len(candidates_from_rows([example], query)) == 1
    example["engine_type"] += "A"
    assert not candidates_from_rows([example], query)
    for invalid in ["", "MODEL%", "MODEL' OR 1=1", "MODEL_A"]:
        with pytest.raises(ValidationError):
            VehicleModelSearch(**{**QUERY, "announcementModel": invalid})


def test_discrepant_config_and_duplicate_name_are_reported_without_silent_repair():
    example = row(same_name_count=2)
    example.update(drive_form="6X2", config_announcement="MODEL999")
    candidate = candidates_from_rows([example], VehicleModelSearch(**QUERY))[0]
    assert len(candidate.warnings) == 3 and candidate.sameNameCount == 2
    assert candidate.config["driveForm"] == "6X2"


def test_incomplete_or_unbounded_catalog_never_presents_false_complete_results():
    missing = row()
    missing["config_id"] = None
    with pytest.raises(VehicleModelCatalogUnavailable, match="缺少配置"):
        candidates_from_rows([missing], VehicleModelSearch(**QUERY))
    with pytest.raises(VehicleModelCatalogUnavailable, match="过多"):
        candidates_from_rows([row()] * 2001, VehicleModelSearch(**QUERY))


def test_same_name_records_keep_ids_and_configuration_differences_for_operator_selection():
    records = [
        row(
            10,
            model_name="同名完整车型",
            same_name_count=2,
            gearbox_model="DT1420",
            rear_axle_ratio="3.42",
        ),
        row(
            11,
            model_name="同名完整车型",
            same_name_count=2,
            gearbox_model="DT1420超速挡",
            rear_axle_ratio="3.73",
        ),
    ]
    candidates = candidates_from_rows(records, VehicleModelSearch(**QUERY))
    assert [item.modelId for item in candidates] == ["10", "11"]
    assert [item.config["rearAxleRatio"] for item in candidates] == ["3.42", "3.73"]
    assert [item.config["gearboxModel"] for item in candidates] == ["DT1420", "DT1420超速挡"]
    assert all("回填按所选记录ID执行" in item.warnings[0] for item in candidates)


TABLE_COLUMNS = {
    "forj_vehicle_brand": "id,brand_name,del_flag",
    "forj_vehicle_series": "id,series_id,series_name,del_flag",
    "forj_vehicle_model": "model_id,model_name,brand_id,s_id,series_id,type_level,announcement_model,del_flag,operate",
    "forj_vehicle_model_config": "model_id,announcement_model,drive_form,fuel_type,emission_standard,max_hp,engine_brand,engine_type,packing_case_length,gearbox_model,shift_way,max_output,weight_kg,total_traction_mass,rear_axle_ratio",
}


def write_catalog(database, records=None, version=1):
    records = records if records is not None else [row()]
    with database.connect() as db:
        for table in [*TABLE_COLUMNS, "catalog_keys", "catalog_metadata"]:
            db.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))
        for table, columns in TABLE_COLUMNS.items():
            definitions = [
                sql.SQL("{} {}").format(
                    sql.Identifier(c),
                    sql.SQL(
                        "BIGINT"
                        if c
                        in {
                            "id",
                            "model_id",
                            "brand_id",
                            "s_id",
                            "series_id",
                            "del_flag",
                            "operate",
                        }
                        else "TEXT"
                    ),
                )
                for c in columns.split(",")
            ]
            db.execute(
                sql.SQL("CREATE TABLE {} ({})").format(
                    sql.Identifier(table), sql.SQL(",").join(definitions)
                )
            )
        db.execute("INSERT INTO forj_vehicle_brand VALUES (2,'示例品牌',0)")
        db.execute("INSERT INTO forj_vehicle_series VALUES (30,3,'示例车系',0)")
        db.execute(
            "CREATE TABLE catalog_keys(model_id BIGINT PRIMARY KEY,announcement_code TEXT,engine_code TEXT)"
        )
        db.execute("CREATE INDEX catalog_codes ON catalog_keys(announcement_code,engine_code)")
        for item in records:
            db.execute(
                "INSERT INTO forj_vehicle_model VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    item["model_id"],
                    item["model_name"],
                    item["brand_id"],
                    item.get("s_id", 30),
                    item["series_id"],
                    item["type_level"],
                    item["announcement_model"],
                    item.get("del_flag", 0),
                    item.get("operate", 1),
                ),
            )
            if item["config_id"] is not None:
                columns = TABLE_COLUMNS["forj_vehicle_model_config"].split(",")
                values = [
                    item.get("config_announcement")
                    if key == "announcement_model"
                    else item.get(key)
                    for key in columns
                ]
                db.execute(
                    "INSERT INTO forj_vehicle_model_config VALUES ("
                    + ",".join("%s" for _ in columns)
                    + ")",
                    values,
                )
            db.execute(
                "INSERT INTO catalog_keys VALUES (%s,%s,%s)",
                (
                    item["model_id"],
                    model_code(item["announcement_model"]),
                    engine_code(item["engine_type"]) if item["config_id"] is not None else "",
                ),
            )
        manifest = {"format": "forj-vehicle-model-catalog", "version": version}
        db.execute("CREATE TABLE catalog_metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute(
            "INSERT INTO catalog_metadata VALUES (%s,%s)", ("manifest", json.dumps(manifest))
        )
    return manifest


def test_postgres_query_uses_both_normalized_codes_and_valid_records_without_network(
    monkeypatch, tmp_path, database
):
    records = [row(1), row(2), row(3), row(4), row(5)]
    records[1]["engine_type"] = "ENGINE42-E6A"
    records[2]["del_flag"] = 1
    records[3]["operate"] = 0
    records[4].update(announcement_model=" model100a80 ", engine_type="锡柴 engine42-e6 ")
    write_catalog(database, records)
    result = VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert [item.modelId for item in result] == ["1", "5"]
    assert result[0].seriesName == "示例车系" and result[0].seriesId == "3"
    assert (
        VehicleModelCatalog().search(VehicleModelSearch(**{**QUERY, "engineModel": "OTHER42"}))
        == []
    )


def test_page_scope_filters_by_resolved_brand_series_ids_and_type_without_global_fallback(
    monkeypatch, tmp_path, database
):
    records = [
        row(1),
        row(2, brand_id=9),
        row(3, series_id=4, s_id=40),
        row(4, type_level="载货车"),
        row(5, series_id=4, s_id=40, config_id=None),
    ]
    write_catalog(database, records)
    with database.connect() as db:
        db.execute("INSERT INTO forj_vehicle_brand VALUES (9,'其他品牌',0)")
        db.execute("INSERT INTO forj_vehicle_series VALUES (40,4,'其他车系',0)")
        db.commit()
    result = VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert [item.modelId for item in result] == ["1"]
    assert result[0].seriesId == "3"  # 业务车系ID，不是关联表主键30。
    assert (
        VehicleModelCatalog().search(
            VehicleModelSearch(**{**QUERY, "pageScope": {**SCOPE, "seriesName": "不存在车系"}})
        )
        == []
    )
    assert [
        item.modelId
        for item in VehicleModelCatalog().search(
            VehicleModelSearch(**{**QUERY, "pageScope": {**SCOPE, "typeLevel": "载货车"}})
        )
    ] == ["4"]


def test_missing_or_ambiguous_page_scope_never_runs_a_global_search(
    monkeypatch, tmp_path, database
):
    for scope in [None, {}, {**SCOPE, "brandName": "  "}, {**SCOPE, "typeLevel": ""}]:
        with pytest.raises(ValidationError):
            VehicleModelSearch(**{**QUERY, "pageScope": scope})
    with pytest.raises(ValidationError):
        VehicleModelSearch(announcementModel="MODEL100A80", engineModel="ENGINE42-E6")
    write_catalog(database, [row(1), row(2, series_id=4, s_id=40)])
    with database.connect() as db:
        db.execute("INSERT INTO forj_vehicle_series VALUES (40,4,'示例车系',0)")
        db.commit()
    with pytest.raises(VehicleModelCatalogUnavailable, match="多个目录ID"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_query_is_readonly_and_connections_close_on_success_and_errors(
    monkeypatch, tmp_path, database
):
    write_catalog(database)
    original = Database.connect
    connections = []

    @contextmanager
    def tracked(self, **kwargs):
        with original(self, **kwargs) as connection:
            connections.append(connection)
            yield connection
            assert (
                connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"]
                == "on"
            )

    monkeypatch.setattr(Database, "connect", tracked)
    assert len(VehicleModelCatalog().search(VehicleModelSearch(**QUERY))) == 1
    assert connections[-1].closed
    monkeypatch.setattr(Database, "connect", original)
    with database.connect() as db:
        db.execute("DROP TABLE catalog_keys")
    monkeypatch.setattr(Database, "connect", tracked)
    with pytest.raises(VehicleModelCatalogUnavailable, match="目录读取失败"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert connections[-1].closed


def test_missing_postgres_catalog_fails_without_creating_files(monkeypatch, tmp_path, database):
    monkeypatch.setenv("REVIEW_MODEL_DB_HOST", "must-not-use.invalid")
    before = set(tmp_path.iterdir())
    with pytest.raises(VehicleModelCatalogUnavailable, match="目录读取失败"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    "bad_metadata",
    [
        [],
        {"format": "other", "version": 1},
        {"format": "forj-vehicle-model-catalog", "version": 99},
    ],
)
def test_unsupported_catalog_metadata_is_actionable_error(
    monkeypatch, tmp_path, database, bad_metadata
):
    write_catalog(database)
    with database.connect() as db:
        db.execute("UPDATE catalog_metadata SET value=%s", (json.dumps(bad_metadata),))
        db.commit()
    with pytest.raises(VehicleModelCatalogUnavailable, match="格式不受支持"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_database_failure_is_sanitized_without_empty_success(monkeypatch, tmp_path, database):
    def unavailable(self, **kwargs):
        raise psycopg.OperationalError("secret-database-details")

    monkeypatch.setattr(Database, "connect", unavailable)
    with pytest.raises(VehicleModelCatalogUnavailable, match="目录读取失败") as error:
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert "secret-database-details" not in str(error.value)


def test_missing_config_for_matching_announcement_prevents_false_complete_results(
    monkeypatch, tmp_path, database
):
    missing = row(2)
    missing["config_id"] = None
    write_catalog(database, [row(), missing])
    with pytest.raises(VehicleModelCatalogUnavailable, match="缺少配置"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_postgres_query_retains_duplicates_and_never_truncates_large_results(
    monkeypatch, tmp_path, database
):
    records = [row(1), row(2)]
    records[1]["model_name"] = records[0]["model_name"]
    write_catalog(database, records)
    assert [
        item.sameNameCount for item in VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    ] == [2, 2]
    write_catalog(database, [row(i) for i in range(2001)])
    with pytest.raises(VehicleModelCatalogUnavailable, match="过多"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_review_endpoint_uses_own_catalog_and_reports_unavailable_without_fallback():
    app = create_app(review_service=ReviewService(load_model=False))
    repository = MagicMock()
    repository.search.return_value = candidates_from_rows([row()], VehicleModelSearch(**QUERY))
    app.dependency_overrides[model_catalog] = lambda: repository
    with TestClient(app) as client:
        result = client.post("/api/review/vehicle-models/search", json=QUERY)
        assert result.status_code == 200 and result.json()[0]["modelName"].startswith(
            "示例完整车型"
        )
        assert client.post("/api/review/vehicle-models/search", json={}).status_code == 422
        repository.search.side_effect = VehicleModelCatalogUnavailable("PostgreSQL 车型目录不可用")
        failed = client.post("/api/review/vehicle-models/search", json=QUERY)
        assert failed.status_code == 503 and failed.json()["detail"] == "PostgreSQL 车型目录不可用"
