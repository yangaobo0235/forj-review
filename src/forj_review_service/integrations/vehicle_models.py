"""在 PostgreSQL 的同一只读快照中查询车型目录。"""

import re

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from forj_review_database.models import (
    DatasetImport,
    VehicleBrand,
    VehicleModel,
    VehicleModelConfig,
    VehicleModelKey,
    VehicleSeries,
)
from forj_review_service.contracts.errors import VehicleModelCatalogUnavailable
from forj_review_service.contracts.vehicle_models import (
    VehicleModelCandidate,
    VehicleModelSearch,
    engine_code,
    model_code,
)
from forj_review_service.integrations.database import AsyncDatabase, review_database

_CONFIG = {
    "drive_form": "driveForm",
    "fuel_type": "fuelType",
    "emission_standard": "emissionStandard",
    "max_hp": "maxHp",
    "engine_brand": "engineBrand",
    "engine_type": "engineType",
    "packing_case_length": "packingCaseLength",
    "gearbox_model": "gearboxModel",
    "shift_way": "shiftWay",
    "max_output": "maxOutput",
    "weight_kg": "weightKg",
    "total_traction_mass": "totalTractionMass",
    "config_announcement": "announcementModel",
    "rear_axle_ratio": "rearAxleRatio",
}


async def matching_rows(session: AsyncSession, query: VehicleModelSearch) -> list[dict]:
    """Resolve the exact page scope and query all variants in a single snapshot."""
    vm, vb, vs, c, k = (
        VehicleModel,
        VehicleBrand,
        VehicleSeries,
        VehicleModelConfig,
        VehicleModelKey,
    )
    scope = query.pageScope
    active = (vm.del_flag == 0, vm.operate == 1)
    scopes = (
        await session.execute(
            select(vm.brand_id, vm.series_id)
            .distinct()
            .join(vb, (vb.id == vm.brand_id) & (vb.del_flag == 0))
            .join(vs, (vs.id == vm.s_id) & (vs.del_flag == 0))
            .where(
                *active,
                vb.brand_name == scope.brandName,
                vs.series_name == scope.seriesName,
                vm.type_level == scope.typeLevel,
            )
        )
    ).all()
    if not scopes:
        return []
    if len(scopes) != 1:
        raise VehicleModelCatalogUnavailable(
            "页面品牌、车系名称对应多个目录ID，无法确定查询范围，请联系管理员"
        )
    scope_filter = (
        vm.brand_id == scopes[0][0],
        vm.series_id == scopes[0][1],
        vm.type_level == scope.typeLevel,
    )
    missing = await session.scalar(
        select(k.model_id)
        .outerjoin(c, c.model_id == k.model_id)
        .join(vm, vm.model_id == k.model_id)
        .where(
            k.announcement_code == query.announcementModel,
            c.model_id.is_(None),
            *active,
            *scope_filter,
        )
        .limit(1)
    )
    if missing is not None:
        raise VehicleModelCatalogUnavailable(
            "该整车型号存在缺少配置的记录，无法完整匹配，请更新目录或联系管理员"
        )
    other = aliased(vm)
    same_names = (
        select(func.count())
        .select_from(other)
        .where(
            other.del_flag == 0,
            other.operate == 1,
            other.brand_id == vm.brand_id,
            other.series_id == vm.series_id,
            other.type_level == vm.type_level,
            other.model_name == vm.model_name,
        )
        .correlate(vm)
        .scalar_subquery()
    )
    statement = (
        select(
            vm.model_id,
            vm.model_name,
            vm.brand_id,
            vm.series_id,
            vm.type_level,
            vm.announcement_model,
            vb.brand_name,
            vs.series_name,
            same_names.label("same_name_count"),
            c.model_id.label("config_id"),
            c.announcement_model.label("config_announcement"),
            *(getattr(c, name) for name in _CONFIG if name != "config_announcement"),
        )
        .outerjoin(c, c.model_id == vm.model_id)
        .outerjoin(vb, (vb.id == vm.brand_id) & (vb.del_flag == 0))
        .outerjoin(vs, (vs.id == vm.s_id) & (vs.del_flag == 0))
        .where(
            *active,
            *scope_filter,
            vm.model_id.in_(
                select(k.model_id).where(
                    k.announcement_code == query.announcementModel,
                    k.engine_code == query.engineModel,
                )
            ),
        )
        .order_by(vm.model_id)
        .limit(2001)
    )
    rows = (await session.execute(statement)).mappings().all()
    return [dict(row) for row in rows]


def candidates_from_rows(
    rows: list[dict], query: VehicleModelSearch
) -> list[VehicleModelCandidate]:
    if len(rows) > 2000:
        raise VehicleModelCatalogUnavailable("整车型号对应记录过多，查询未完整返回，请联系管理员")
    result = []
    for row in rows:
        if row.get("config_id") is None:
            raise VehicleModelCatalogUnavailable("存在缺少配置的车型，无法完整匹配，请联系管理员")
        if engine_code(row.get("engine_type")) != query.engineModel:
            continue
        warnings = []
        if (
            row.get("config_announcement")
            and model_code(row["config_announcement"]) != query.announcementModel
        ):
            warnings.append("车型记录与配置中的整车型号不一致")
        drive = re.search(r"\b(\d+)[X×](\d+)\b", row["model_name"], re.I)
        if drive and model_code(f"{drive[1]}X{drive[2]}") != model_code(
            row.get("drive_form")
        ).replace("×", "X"):
            warnings.append("车型名称与配置中的驱动形式不一致，请人工确认")
        same_names = int(row.get("same_name_count") or 1)
        if same_names > 1:
            warnings.append(
                "存在同名的不同车型记录，请核对变速箱、后桥速比等配置后选择；回填按所选记录ID执行"
            )
        result.append(
            VehicleModelCandidate(
                modelId=str(row["model_id"]),
                modelName=row["model_name"],
                brandId=str(row["brand_id"]),
                brandName=row.get("brand_name") or "",
                seriesId=str(row["series_id"]),
                seriesName=row.get("series_name") or "",
                typeLevel=row.get("type_level") or "",
                sameNameCount=same_names,
                config={
                    "modelId": str(row["config_id"]),
                    **{target: row.get(source) for source, target in _CONFIG.items()},
                },
                warnings=warnings,
            )
        )
    return result


class VehicleModelCatalog:
    def __init__(self, database: AsyncDatabase | None = None):
        self.database = database

    async def search(self, query: VehicleModelSearch) -> list[VehicleModelCandidate]:
        try:
            if self.database is None:
                async with AsyncDatabase(review_database()) as database:
                    return await self._search(database, query)
            return await self._search(self.database, query)
        except VehicleModelCatalogUnavailable:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError):
            raise VehicleModelCatalogUnavailable(
                "车型目录读取失败，请检查 PostgreSQL 连接、数据格式和读取权限"
            ) from None

    async def _search(self, database, query):
        async with database.session(readonly=True, repeatable=True, timeout_ms=8000) as session:
            manifest = await session.scalar(
                select(DatasetImport.format_version)
                .where(DatasetImport.dataset_code == "vehicle_model")
                .order_by(DatasetImport.id.desc())
                .limit(1)
            )
            if manifest is None:
                raise VehicleModelCatalogUnavailable("车型目录读取失败，缺少参考数据导入记录")
            if manifest != "1":
                raise VehicleModelCatalogUnavailable("PostgreSQL 车型目录格式不受支持")
            return candidates_from_rows(await matching_rows(session, query), query)
