"""业务扩展包：一个审核业务的声明式描述。"""

from forj_review_assistant.businesses.packs.model import (
    AuthorityRule,
    BusinessExtensionPack,
    EvidenceMode,
    FieldDeclaration,
    MaterialDeclaration,
    PageGroupDeclaration,
    RegionDeclaration,
    SectionDeclaration,
    build_collect_manifest,
    validate_pack_references,
)
from forj_review_assistant.businesses.packs.scrap_replacement import (
    SCRAP_REPLACEMENT_PACK,
)
from forj_review_assistant.businesses.packs.transfer import TRANSFER_PACK
from forj_review_assistant.businesses.packs.vehicle_source import VEHICLE_SOURCE_PACK
from forj_review_assistant.contracts.review import BusinessType

# 业务类型 → 声明。同一业务的各地区共用一份声明，地区差异在 Profile 里。
BUSINESS_PACKS: dict[BusinessType, BusinessExtensionPack] = {
    BusinessType.SCRAP_REPLACEMENT: SCRAP_REPLACEMENT_PACK,
    BusinessType.VEHICLE_SOURCE: VEHICLE_SOURCE_PACK,
    BusinessType.TRANSFER: TRANSFER_PACK,
}

# 业务分区 → 声明。各业务的分区互不相同，
# 因此按分区就能唯一确定声明，字段路由不必依赖调用方传业务类型。
PACK_BY_SCOPE: dict[str, BusinessExtensionPack] = {
    scope: pack for pack in BUSINESS_PACKS.values() for scope in pack.scopes
}


def pack_for_business(business_type: BusinessType) -> BusinessExtensionPack | None:
    """返回该业务的声明；尚未声明的业务返回 None。"""
    from forj_review_assistant.businesses.context import current_pack
    pack = current_pack()
    if pack is not None and pack.business_type == business_type:
        return pack
    return BUSINESS_PACKS.get(business_type)


def pack_for_scope(business_scope: str) -> BusinessExtensionPack | None:
    """按图片业务分区反查声明；未知分区返回 None。

    分区在业务之间不重叠（由启动期校验保证），所以这里不需要业务类型参数。
    字段路由只有分区可用时靠它选中正确声明，未知分区不得借用其他业务的路由。
    """
    from forj_review_assistant.businesses.context import current_pack
    pack = current_pack()
    if pack is not None:
        return pack if business_scope in pack.scopes else None
    return PACK_BY_SCOPE.get(business_scope)


def _validate_scope_ownership() -> None:
    """分区归属于唯一业务；重叠会让按分区反查变成看声明顺序。"""
    duplicated: dict[str, list[str]] = {}
    for business_type, pack in BUSINESS_PACKS.items():
        for scope in pack.scopes:
            duplicated.setdefault(scope, []).append(str(business_type))
    conflicts = {scope: owners for scope, owners in duplicated.items() if len(owners) > 1}
    if conflicts:
        detail = "；".join(f"{scope}：{'、'.join(owners)}" for scope, owners in conflicts.items())
        raise ValueError(f"业务分区在多个业务之间重复：{detail}")


_validate_scope_ownership()


def _validate_all_pack_references() -> None:
    """每个业务的声明内部交叉引用都必须指向真实存在的字段键。

    配错的后果是静默的——那条观察值被丢掉，审核员看到的是「该字段没有材料
    证据」而不是「配置写错了」。所以放在 import 期直接失败，和上面的分区
    归属校验同一个口径：宁可服务起不来，也不带着静默错误去审单子。
    """
    for pack in BUSINESS_PACKS.values():
        validate_pack_references(pack)


_validate_all_pack_references()


__all__ = [
    "BUSINESS_PACKS",
    "PACK_BY_SCOPE",
    "SCRAP_REPLACEMENT_PACK",
    "AuthorityRule",
    "BusinessExtensionPack",
    "EvidenceMode",
    "FieldDeclaration",
    "MaterialDeclaration",
    "PageGroupDeclaration",
    "RegionDeclaration",
    "SectionDeclaration",
    "build_collect_manifest",
    "pack_for_business",
    "pack_for_scope",
]
