"""离线车型目录使用临时 SQLite 测试，绝不访问源数据库或业务接口。"""

import hashlib
import importlib.util
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

import pymysql
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forj_review_assistant.bootstrap.application import create_app
from forj_review_assistant.config.vehicle_models import VehicleModelCatalogSettings
from forj_review_assistant.contracts.vehicle_models import (
    VehicleModelSearch,
    engine_code,
    model_code,
)
from forj_review_assistant.integrations.vehicle_models import (
    VehicleModelCatalog,
    VehicleModelCatalogUnavailable,
    candidates_from_rows,
)
from forj_review_assistant.interfaces.http.vehicle_models import model_catalog
from forj_review_assistant.runtime.review_service import ReviewService

SCOPE = {"brandName": "示例品牌", "seriesName": "示例车系", "typeLevel": "牵引车"}
QUERY = {"announcementModel": "MODEL100A80", "engineModel": "ENGINE42-E6", "pageScope": SCOPE}


def row(identifier=1, **changes):
    return dict(model_id=identifier, model_name=f"示例完整车型 6X4 手动版 {identifier}",
                brand_id=2, series_id=3, brand_name="示例品牌", series_name="示例车系",
                type_level="牵引车", announcement_model="MODEL100A80", config_id=identifier,
                config_announcement="MODEL100A80", drive_form="6X4", engine_type="ENGINE42-E6",
                max_hp=420) | changes


@pytest.mark.parametrize("size", [4, 9])
def test_exact_engine_filter_keeps_every_sales_variant_and_ids(size):
    rows = [row(i) for i in range(size)]
    rows.extend([row(99, engine_brand="其他品牌")])
    rows[-1]["engine_type"] = "ENGINE42-E6A"
    result = candidates_from_rows(rows, VehicleModelSearch(**QUERY))
    assert [c.modelId for c in result] == [str(i) for i in range(size)]
    assert all(c.modelName.startswith("示例完整车型") for c in result)


def test_search_normalizes_case_spaces_and_engine_brand_but_preserves_suffix():
    query = VehicleModelSearch(announcementModel=" model100a80 ", engineModel="锡柴 engine42-e6 ", pageScope=SCOPE)
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
    records = [row(10, model_name="同名完整车型", same_name_count=2,
                   gearbox_model="DT1420", rear_axle_ratio="3.42"),
               row(11, model_name="同名完整车型", same_name_count=2,
                   gearbox_model="DT1420超速挡", rear_axle_ratio="3.73")]
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


def write_catalog(path, records=None, version=1):
    records = records if records is not None else [row()]
    with closing(sqlite3.connect(path)) as db:
        for table, columns in TABLE_COLUMNS.items():
            # id/flag 保持整数；其余列可同时容纳字符串与空值。
            db.execute(f"CREATE TABLE {table} ({columns})")
        db.execute("INSERT INTO forj_vehicle_brand VALUES (2,'示例品牌',0)")
        db.execute("INSERT INTO forj_vehicle_series VALUES (30,3,'示例车系',0)")
        db.execute("CREATE TABLE catalog_keys(model_id INTEGER PRIMARY KEY,announcement_code TEXT,engine_code TEXT)")
        db.execute("CREATE INDEX catalog_codes ON catalog_keys(announcement_code,engine_code)")
        for item in records:
            db.execute("INSERT INTO forj_vehicle_model VALUES (?,?,?,?,?,?,?,?,?)",
                       (item["model_id"], item["model_name"], item["brand_id"], item.get("s_id", 30), item["series_id"], item["type_level"],
                        item["announcement_model"], item.get("del_flag", 0), item.get("operate", 1)))
            if item["config_id"] is not None:
                columns = TABLE_COLUMNS["forj_vehicle_model_config"].split(',')
                db.execute("INSERT INTO forj_vehicle_model_config VALUES (" + ','.join('?' for _ in columns) + ")",
                           [item.get("config_announcement") if key == "announcement_model" else item.get(key) for key in columns])
            # 即使索引混入无效记录，运行查询也必须再次检查源记录有效标记。
            db.execute("INSERT INTO catalog_keys VALUES (?,?,?)", (item["model_id"],
                       model_code(item["announcement_model"]), engine_code(item["engine_type"]) if item["config_id"] is not None else ""))
        manifest = {"format": "forj-vehicle-model-catalog", "version": version,
                    "exported_at": "2026-10-17T13:21:14+00:00",
                    "validation": {"active_models": len(records)},
                    "tables": {table: {"rows": db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]} for table in TABLE_COLUMNS}}
        db.execute("CREATE TABLE catalog_metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO catalog_metadata VALUES ('manifest',?)", (json.dumps(manifest),))
        db.commit()
    return manifest


def configure(monkeypatch, path):
    monkeypatch.setattr("forj_review_assistant.config.vehicle_models.load_dotenv", lambda: None)
    monkeypatch.setenv("REVIEW_MODEL_CATALOG_PATH", str(path))
    monkeypatch.setattr(pymysql, "connect", lambda **_kwargs: pytest.fail("runtime must never connect to MySQL"))


def test_offline_query_uses_both_normalized_codes_and_valid_records_without_network(monkeypatch, tmp_path):
    records = [row(1), row(2), row(3), row(4), row(5)]
    records[1]["engine_type"] = "ENGINE42-E6A"
    records[2]["del_flag"] = 1
    records[3]["operate"] = 0
    records[4].update(announcement_model=" model100a80 ", engine_type="锡柴 engine42-e6 ")
    path = tmp_path / "目录 #1.sqlite"
    write_catalog(path, records)
    configure(monkeypatch, path)
    before = path.read_bytes()
    result = VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert [item.modelId for item in result] == ["1", "5"]
    assert result[0].seriesName == "示例车系" and result[0].seriesId == "3"
    assert path.read_bytes() == before
    assert VehicleModelCatalog().search(VehicleModelSearch(**{**QUERY, "engineModel": "OTHER42"})) == []


def test_page_scope_filters_by_resolved_brand_series_ids_and_type_without_global_fallback(monkeypatch, tmp_path):
    records = [row(1), row(2, brand_id=9), row(3, series_id=4, s_id=40),
               row(4, type_level="载货车"), row(5, series_id=4, s_id=40, config_id=None)]
    path = tmp_path / "scoped.sqlite"
    write_catalog(path, records)
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO forj_vehicle_brand VALUES (9,'其他品牌',0)")
        db.execute("INSERT INTO forj_vehicle_series VALUES (40,4,'其他车系',0)")
        db.commit()
    configure(monkeypatch, path)
    result = VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert [item.modelId for item in result] == ["1"]
    assert result[0].seriesId == "3"  # 业务车系ID，不是关联表主键30。
    assert VehicleModelCatalog().search(VehicleModelSearch(
        **{**QUERY, "pageScope": {**SCOPE, "seriesName": "不存在车系"}})) == []
    assert [item.modelId for item in VehicleModelCatalog().search(VehicleModelSearch(
        **{**QUERY, "pageScope": {**SCOPE, "typeLevel": "载货车"}}))] == ["4"]


def test_missing_or_ambiguous_page_scope_never_runs_a_global_search(monkeypatch, tmp_path):
    for scope in [None, {}, {**SCOPE, "brandName": "  "}, {**SCOPE, "typeLevel": ""}]:
        with pytest.raises(ValidationError):
            VehicleModelSearch(**{**QUERY, "pageScope": scope})
    with pytest.raises(ValidationError):
        VehicleModelSearch(announcementModel="MODEL100A80", engineModel="ENGINE42-E6")
    path = tmp_path / "ambiguous.sqlite"
    write_catalog(path, [row(1), row(2, series_id=4, s_id=40)])
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO forj_vehicle_series VALUES (40,4,'示例车系',0)")
        db.commit()
    configure(monkeypatch, path)
    with pytest.raises(VehicleModelCatalogUnavailable, match="多个目录ID"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_readonly_uri_and_connections_close_on_success_and_errors(monkeypatch, tmp_path):
    path = tmp_path / "catalog.sqlite"
    write_catalog(path)
    configure(monkeypatch, path)
    real_connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            self.was_readonly = self.execute("PRAGMA query_only").fetchone()[0] == 1
            super().close()
            self.was_closed = True

    def connect(database_url, **kwargs):
        assert database_url.endswith("?mode=ro") and kwargs["uri"] is True
        connection = real_connect(database_url, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert len(VehicleModelCatalog().search(VehicleModelSearch(**QUERY))) == 1
    with closing(real_connect(path)) as db:
        db.execute("DROP TABLE catalog_keys")
        db.commit()
    with pytest.raises(VehicleModelCatalogUnavailable, match="离线车型目录读取失败"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert len(connections) == 2
    assert all(connection.was_closed and connection.was_readonly for connection in connections)


def test_missing_catalog_never_creates_empty_file_or_uses_legacy_mysql_settings(monkeypatch, tmp_path):
    path = tmp_path / "missing.sqlite"
    configure(monkeypatch, path)
    monkeypatch.setenv("REVIEW_MODEL_DB_HOST", "must-not-use.invalid")
    monkeypatch.setenv("REVIEW_MODEL_DB_NAME", "forj_vehicle_prod")
    with pytest.raises(VehicleModelCatalogUnavailable, match="尚未安装"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert not path.exists()


@pytest.mark.parametrize("bad_metadata", [[], {"format": "other", "version": 1}, {"format": "forj-vehicle-model-catalog", "version": 99}])
def test_unsupported_catalog_metadata_is_actionable_error(monkeypatch, tmp_path, bad_metadata):
    path = tmp_path / "catalog.sqlite"
    write_catalog(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE catalog_metadata SET value=?", (json.dumps(bad_metadata),))
        db.commit()
    configure(monkeypatch, path)
    with pytest.raises(VehicleModelCatalogUnavailable, match="格式不受支持"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_corrupt_catalog_is_error_not_false_empty_result(monkeypatch, tmp_path):
    path = tmp_path / "catalog.sqlite"
    path.write_text("not a database", encoding="utf-8")
    configure(monkeypatch, path)
    with pytest.raises(VehicleModelCatalogUnavailable, match="目录读取失败") as error:
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))
    assert str(path) not in str(error.value)


def test_missing_config_for_matching_announcement_prevents_false_complete_results(monkeypatch, tmp_path):
    missing = row(2)
    missing["config_id"] = None
    path = tmp_path / "catalog.sqlite"
    write_catalog(path, [row(), missing])
    configure(monkeypatch, path)
    with pytest.raises(VehicleModelCatalogUnavailable, match="缺少配置"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_offline_query_retains_duplicates_and_never_truncates_large_results(monkeypatch, tmp_path):
    records = [row(1), row(2)]
    records[1]["model_name"] = records[0]["model_name"]
    path = tmp_path / "catalog.sqlite"
    write_catalog(path, records)
    configure(monkeypatch, path)
    assert [item.sameNameCount for item in VehicleModelCatalog().search(VehicleModelSearch(**QUERY))] == [2, 2]
    large = tmp_path / "large.sqlite"
    write_catalog(large, [row(i) for i in range(2001)])
    monkeypatch.setenv("REVIEW_MODEL_CATALOG_PATH", str(large))
    with pytest.raises(VehicleModelCatalogUnavailable, match="过多"):
        VehicleModelCatalog().search(VehicleModelSearch(**QUERY))


def test_configuration_resolves_relative_paths_against_service_environment(monkeypatch, tmp_path):
    env = tmp_path / "service" / ".env"
    env.parent.mkdir()
    catalog = tmp_path / "models.sqlite"
    catalog.touch()
    configure(monkeypatch, "../models.sqlite")
    monkeypatch.setattr("forj_review_assistant.config.vehicle_models.DEFAULT_ENV_FILE", env)
    assert VehicleModelCatalogSettings.from_env().path == catalog.resolve()


def make_package(folder, name, model_name="完整车型", corrupt=False, missing_column=False):
    folder.mkdir()
    example = row()
    example["model_name"] = model_name
    manifest = write_catalog(folder / "vehicle-model-catalog.sqlite", [example])
    if missing_column:
        with closing(sqlite3.connect(folder / "vehicle-model-catalog.sqlite")) as db:
            db.execute("ALTER TABLE forj_vehicle_model_config DROP COLUMN gearbox_model")
            db.commit()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "example-matches.json").write_text("[]", encoding="utf-8")
    (folder / "README.md").write_text("test catalog", encoding="utf-8")
    checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir()}
    if corrupt:
        checksums["vehicle-model-catalog.sqlite"] = "0" * 64
    (folder / "sha256.json").write_text(json.dumps(checksums), encoding="utf-8")
    package = folder.parent / name
    with zipfile.ZipFile(package, "w") as archive:
        for path in folder.iterdir():
            archive.write(path, path.name)
    return package


def test_package_install_checks_integrity_keeps_old_on_failure_and_replaces_on_success(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts/dev/install-vehicle-model-catalog.py"
    spec = importlib.util.spec_from_file_location("catalog_installer", script)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    destination = tmp_path / "active" / "catalog.sqlite"
    installer.install(make_package(tmp_path / "first", "first.zip"), destination)
    configure(monkeypatch, destination)
    assert VehicleModelCatalog().search(VehicleModelSearch(**QUERY))[0].modelName == "完整车型"
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="校验码"):
        installer.install(make_package(tmp_path / "bad", "bad.zip", corrupt=True), destination)
    assert destination.read_bytes() == before
    with pytest.raises(sqlite3.Error):
        installer.install(make_package(tmp_path / "bad-schema", "bad-schema.zip", missing_column=True), destination)
    assert destination.read_bytes() == before
    installer.install(make_package(tmp_path / "new", "new.zip", model_name="更新后的完整车型"), destination)
    assert destination.with_suffix(".previous.sqlite").read_bytes() == before
    assert VehicleModelCatalog().search(VehicleModelSearch(**QUERY))[0].modelName == "更新后的完整车型"


def test_assistant_endpoint_uses_own_catalog_and_reports_unavailable_without_fallback():
    app = create_app(review_service=ReviewService(load_model=False))
    repository = MagicMock()
    repository.search.return_value = candidates_from_rows([row()], VehicleModelSearch(**QUERY))
    app.dependency_overrides[model_catalog] = lambda: repository
    with TestClient(app) as client:
        result = client.post("/api/review/vehicle-models/search", json=QUERY)
        assert result.status_code == 200 and result.json()[0]["modelName"].startswith("示例完整车型")
        assert client.post("/api/review/vehicle-models/search", json={}).status_code == 422
        repository.search.side_effect = VehicleModelCatalogUnavailable("离线车型目录尚未安装")
        failed = client.post("/api/review/vehicle-models/search", json=QUERY)
        assert failed.status_code == 503 and failed.json()["detail"] == "离线车型目录尚未安装"
