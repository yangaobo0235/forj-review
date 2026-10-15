"""模型提取字段到审核领域字段的安全路由。

页面提供的业务分区决定旧车或新车归属；模型只负责识别材料类型和字段值，
不得自行改变材料所属业务范围。
"""

from collections.abc import Mapping
from typing import Any

from forj_review_assistant.businesses.packs import SCRAP_REPLACEMENT_PACK as _PACK
from forj_review_assistant.businesses.packs import pack_for_scope
from forj_review_assistant.businesses.packs.model import (
    BusinessExtensionPack,
    MaterialDeclaration,
    material_types_for_scope,
)

# 材料上的车辆通用字段可能写作这三种前缀之一，落到哪个分区由页面决定。
VEHICLE_PREFIXES = {"vehicle", "old_vehicle", "new_vehicle"}
# 兼容旧版前端把业务角色当作文档类型提交的请求。
LEGACY_DOCUMENT_TYPES = {
    "old_vehicle": "vehicle_license",
    "new_vehicle": "vehicle_license",
    "id_card": "identity_card",
}


def normalize_document_type(document_type: str) -> str:
    """把旧版车辆角色名称归一为实际材料类型。"""
    return LEGACY_DOCUMENT_TYPES.get(document_type, document_type)


def scope_hint_types(pack: BusinessExtensionPack, scope: str) -> frozenset[str]:
    """「可能表示该分区材料」的类型集合：声明的材料类型 ＋ 指向它们的旧版角色别名。

    这个集合决定分类缺失时哪些图片 URL 可以作为二维码扫描候选，
    不负责保留图片正文；原图仅在专用解码器运行期间存在于内存。

    别名取**名字就等于该分区**的那一个（`old_vehicle` / `new_vehicle`）：旧版前端
    把业务角色当作材料类型提交，角色名本身就是分区名。不能按「别名映射到的材料
    类型是否属于该分区」反查——行驶证两个分区都有，那样查会让 `new_vehicle` 也
    落进旧车集合。

    该分区**没有声明任何材料**时返回空集：此时角色别名指向的是别的分区的材料
    （例如拿车源审核去问旧车分区），带上别名只会凭空多出一个并不存在的候选。
    """
    declared = material_types_for_scope(pack, scope)
    if not declared:
        return frozenset()
    return frozenset(declared | {alias for alias in LEGACY_DOCUMENT_TYPES if alias == scope})


def _vehicle_suffix(material: MaterialDeclaration | None, field_name: str) -> str | None:
    """返回该材料上跟随页面分区落位的车辆字段后缀。"""
    if material is None:
        return None
    prefix, separator, suffix = field_name.partition(".")
    if (
        separator
        and prefix in VEHICLE_PREFIXES
        and suffix in set(material.scoped_vehicle_fields)
    ):
        return suffix
    return None


def route_fields(
    business_scope: str,
    document_type: str,
    fields: Mapping[str, Any],
    pack: BusinessExtensionPack | None = None,
    *,
    group_order: int | None = None,
) -> tuple[dict[str, Any], str | None]:
    """按页面确定的业务范围路由字段，并返回需要人工复核的限制说明。

    分区白名单、材料归属和字段映射都来自业务声明（`forj_review_assistant.businesses.packs`）；
    新增业务时只要在声明里写清这些内容，本函数不需要改动。

    未显式传 `pack` 时按业务分区反查声明；分区只有报废置换的老车/新车时才退回
    报废置换声明，未声明的分区一律不路由。
    """
    resolved_pack = pack or pack_for_scope(business_scope) or _PACK
    normalized_type = normalize_document_type(document_type)
    material = resolved_pack.material(normalized_type)

    # 营业执照、身份证这类材料不属于业务分区，字段按材料自身白名单原样通过。
    if material is not None and material.scope_independent:
        allowed = set(material.fields)
        return {
            field: value
            for field, value in fields.items()
            if field in allowed and value not in (None, "", [], {})
        }, None

    if business_scope not in resolved_pack.scopes:
        return {}, "图片业务归属无法确定，请人工复核"
    # 材料类型与页面分区矛盾时拒绝路由，不能让模型识别结果覆盖页面业务归属。
    if (
        material is not None
        and material.allowed_scopes
        and business_scope not in material.allowed_scopes
    ):
        return {}, f"{material.display_name}位于不属于它的资料区域，请人工复核"

    routes = [
        rule
        for rule in resolved_pack.routes
        if rule.document_type == normalized_type
        and (not rule.scope or rule.scope == business_scope)
        and (rule.group_order is None or rule.group_order == group_order)
    ]

    routed: dict[str, Any] = {}
    for field_name, value in fields.items():
        if not value:
            continue
        matched = False
        for rule in routes:
            if rule.source_field != field_name:
                continue
            for target in rule.targets:
                routed[target.replace("{scope}", business_scope)] = value
            matched = True
        if matched:
            continue
        suffix = _vehicle_suffix(material, field_name)
        if suffix is not None:
            routed[f"{business_scope}.{suffix}"] = value

    # 数电发票票面只有一个号码。模型只提取一次，另一个字段由确定性兼容层
    # 派生，避免模型分别生成两个可能冲突的值。派生关系由材料声明给出——
    # 报废置换的机动车销售发票和过户的二手车销售统一发票字段键并不相同。
    if material is not None and material.derived_field is not None:
        number_field, derived_field = material.derived_field
        if number_field in routed:
            routed[derived_field] = routed[number_field]
    return routed, None
