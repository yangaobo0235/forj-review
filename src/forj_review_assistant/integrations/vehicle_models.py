"""只读查询离线 SQLite 车型目录；运行时不连接源数据库或业务接口。"""

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from threading import BoundedSemaphore

from forj_review_assistant.config.vehicle_models import VehicleModelCatalogSettings
from forj_review_assistant.contracts.vehicle_models import (
    VehicleModelCandidate,
    VehicleModelSearch,
    engine_code,
    model_code,
)

_CONNECTIONS = BoundedSemaphore(4)
_SQL = """
SELECT vm.model_id, vm.model_name, vm.brand_id, vm.series_id, vm.type_level,
       vm.announcement_model, vb.brand_name, vs.series_name,
       (SELECT COUNT(*) FROM forj_vehicle_model other
        WHERE other.del_flag=0 AND other.operate=1
          AND other.brand_id=vm.brand_id AND other.series_id=vm.series_id
          AND other.type_level=vm.type_level
          AND other.model_name=vm.model_name) AS same_name_count,
       c.model_id AS config_id, c.announcement_model AS config_announcement,
       c.drive_form, c.fuel_type, c.emission_standard, c.max_hp,
       c.engine_brand, c.engine_type, c.packing_case_length,
       c.gearbox_model, c.shift_way, c.max_output,
       c.weight_kg, c.total_traction_mass, c.rear_axle_ratio
FROM forj_vehicle_model vm
LEFT JOIN forj_vehicle_model_config c ON c.model_id=vm.model_id
LEFT JOIN forj_vehicle_brand vb ON vb.id=vm.brand_id AND vb.del_flag=0
LEFT JOIN forj_vehicle_series vs ON vs.id=vm.s_id AND vs.del_flag=0
WHERE vm.del_flag=0 AND vm.operate=1 AND vm.model_id IN (
  SELECT model_id FROM catalog_keys WHERE announcement_code=? AND engine_code=?
)
AND vm.brand_id=? AND vm.series_id=? AND vm.type_level=?
ORDER BY vm.model_id ASC LIMIT 2001
"""
_CONFIG = {
    "drive_form": "driveForm", "fuel_type": "fuelType", "emission_standard": "emissionStandard",
    "max_hp": "maxHp", "engine_brand": "engineBrand", "engine_type": "engineType",
    "packing_case_length": "packingCaseLength", "gearbox_model": "gearboxModel",
    "shift_way": "shiftWay", "max_output": "maxOutput", "weight_kg": "weightKg",
    "total_traction_mass": "totalTractionMass", "config_announcement": "announcementModel",
    "rear_axle_ratio": "rearAxleRatio",
}


class VehicleModelCatalogUnavailable(Exception):
    pass


@contextmanager
def _open_catalog() -> Iterator[sqlite3.Connection]:
    """每次查询在同一只读快照内完成；连接配额、超时和关闭统一在这里管理。"""
    try:
        settings = VehicleModelCatalogSettings.from_env()
    except (ValueError, TypeError) as exc:
        raise VehicleModelCatalogUnavailable(str(exc)) from None
    if not _CONNECTIONS.acquire(timeout=1):
        raise VehicleModelCatalogUnavailable("车型查询繁忙，请稍后重试")
    try:
        with closing(sqlite3.connect(settings.path.as_uri() + "?mode=ro", uri=True,
                                     timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            deadline = time.monotonic() + 8
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            _validate_format(connection)
            yield connection
    finally:
        _CONNECTIONS.release()


def _validate_format(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT value FROM catalog_metadata WHERE key='manifest'",
    ).fetchone()
    manifest = json.loads(metadata[0]) if metadata else {}
    if (not isinstance(manifest, dict) or manifest.get("format") != "forj-vehicle-model-catalog"
            or manifest.get("version") != 1):
        raise VehicleModelCatalogUnavailable("离线车型目录格式不受支持，请重新导入完整数据包")


def _resolve_scope(connection: sqlite3.Connection, query: VehicleModelSearch) -> tuple | None:
    # DOM 只提供选中名称，先唯一解析到业务下拉使用的 ID，绝不扩大到全库。
    scope = query.pageScope
    scopes = connection.execute(
        "SELECT DISTINCT vm.brand_id,vm.series_id FROM forj_vehicle_model vm "
        "JOIN forj_vehicle_brand vb ON vb.id=vm.brand_id AND vb.del_flag=0 "
        "JOIN forj_vehicle_series vs ON vs.id=vm.s_id AND vs.del_flag=0 "
        "WHERE vm.del_flag=0 AND vm.operate=1 AND vb.brand_name=? "
        "AND vs.series_name=? AND vm.type_level=?",
        (scope.brandName, scope.seriesName, scope.typeLevel),
    ).fetchall()
    if not scopes:
        return None
    if len(scopes) != 1:
        raise VehicleModelCatalogUnavailable(
            "页面品牌、车系名称对应多个目录ID，无法确定查询范围，请联系管理员",
        )
    return (*scopes[0], scope.typeLevel)


def _matching_rows(connection: sqlite3.Connection, query: VehicleModelSearch) -> list[dict]:
    scope_ids = _resolve_scope(connection, query)
    if scope_ids is None:
        return []
    # 缺少配置的记录无法判断发动机是否匹配，不能静默排除后声称查询完整。
    missing = connection.execute(
        "SELECT 1 FROM catalog_keys k LEFT JOIN forj_vehicle_model_config c "
        "ON c.model_id=k.model_id JOIN forj_vehicle_model vm ON vm.model_id=k.model_id "
        "WHERE k.announcement_code=? AND c.model_id IS NULL AND vm.del_flag=0 "
        "AND vm.operate=1 AND vm.brand_id=? AND vm.series_id=? AND vm.type_level=? LIMIT 1",
        (query.announcementModel, *scope_ids),
    ).fetchone()
    if missing:
        raise VehicleModelCatalogUnavailable(
            "该整车型号存在缺少配置的记录，无法完整匹配，请更新目录或联系管理员",
        )
    rows = connection.execute(
        _SQL, (query.announcementModel, query.engineModel, *scope_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def candidates_from_rows(rows: list[dict], query: VehicleModelSearch) -> list[VehicleModelCandidate]:
    if len(rows) > 2000:
        raise VehicleModelCatalogUnavailable("整车型号对应记录过多，查询未完整返回，请联系管理员")
    result = []
    for row in rows:
        if row.get("config_id") is None:
            raise VehicleModelCatalogUnavailable("存在缺少配置的车型，无法完整匹配，请联系管理员")
        if engine_code(row.get("engine_type")) != query.engineModel:
            continue
        warnings = []
        if row.get("config_announcement") and model_code(row["config_announcement"]) != query.announcementModel:
            warnings.append("车型记录与配置中的整车型号不一致")
        drive = re.search(r"\b(\d+)[X×](\d+)\b", row["model_name"], re.I)
        if drive and model_code(f"{drive[1]}X{drive[2]}") != model_code(row.get("drive_form")).replace("×", "X"):
            warnings.append("车型名称与配置中的驱动形式不一致，请人工确认")
        same_names = int(row.get("same_name_count") or 1)
        if same_names > 1:
            warnings.append("存在同名的不同车型记录，请核对变速箱、后桥速比等配置后选择；回填按所选记录ID执行")
        result.append(VehicleModelCandidate(
            modelId=str(row["model_id"]), modelName=row["model_name"],
            brandId=str(row["brand_id"]), brandName=row.get("brand_name") or "",
            seriesId=str(row["series_id"]), seriesName=row.get("series_name") or "",
            typeLevel=row.get("type_level") or "", sameNameCount=same_names,
            config={"modelId": str(row["config_id"]),
                    **{target: row.get(source) for source, target in _CONFIG.items()}},
            warnings=warnings,
        ))
    return result


class VehicleModelCatalog:
    def search(self, query: VehicleModelSearch) -> list[VehicleModelCandidate]:
        try:
            with _open_catalog() as connection:
                return candidates_from_rows(_matching_rows(connection, query), query)
        except VehicleModelCatalogUnavailable:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError):
            raise VehicleModelCatalogUnavailable("离线车型目录读取失败，请检查目录文件、格式和读取权限后重新导入") from None
