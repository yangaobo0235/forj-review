"""分区材料集合的推导结果。

「哪些材料属于旧车」以前手抄在四个地方，各自用于不同的事：决定二维码扫哪些图、
决定哪些图的正文必须留到核验结束、决定旧车能力能不能执行。改一处漏一处的后果
是静默的——该留的图被提前释放，最终二维码核验拿不到正文，审核员只看到
「未识别到二维码」，没有任何报错。

现在统一从业务的材料要求声明（`MaterialPolicy.materials[].business_scope`）推导。
本文件把推导结果钉死：**声明改动导致这个集合变化时必须有人看见**，因为它的下游
是二维码核验和图片正文释放，两者都不可见地失败。
"""

from forj_review_assistant.businesses.packs import BUSINESS_PACKS, pack_for_business
from forj_review_assistant.businesses.packs.model import (
    BusinessExtensionPack,
    material_types_for_scope,
)
from forj_review_assistant.businesses.packs.scrap_replacement import OLD_SECTION
from forj_review_assistant.businesses.routing import scope_hint_types
from forj_review_assistant.contracts.review import BusinessType

# 收敛前的历史值。重构是纯等价替换，所以这里逐字锁定。
HISTORICAL_OLD_VEHICLE_HINTS = frozenset(
    {"old_vehicle", "vehicle_license", "registration_certificate", "scrap_certificate"}
)
# 能力可用性判断用的集合不含角色别名（别名由旧版前端提交，不是材料类型）。
HISTORICAL_OLD_VEHICLE_MATERIALS = frozenset(
    {"vehicle_license", "registration_certificate", "scrap_certificate"}
)


def _scrap():
    pack = pack_for_business(BusinessType.SCRAP_REPLACEMENT)
    assert pack is not None
    return pack


def test_old_vehicle_material_types_match_history() -> None:
    assert material_types_for_scope(_scrap(), OLD_SECTION) == HISTORICAL_OLD_VEHICLE_MATERIALS


def test_old_vehicle_hints_match_history() -> None:
    """提示集合多出或少掉一个类型，都会改变二维码覆盖范围或图片正文的释放时机。"""
    assert scope_hint_types(_scrap(), OLD_SECTION) == HISTORICAL_OLD_VEHICLE_HINTS


def test_alias_is_the_scope_name_itself() -> None:
    """旧版前端把业务角色当材料类型提交，角色名就是分区名。

    不能按「别名映射到的材料类型是否属于该分区」来加别名——行驶证两个分区都有，
    那样查会把 `new_vehicle` 也塞进旧车集合（实测过）。
    """
    old = scope_hint_types(_scrap(), OLD_SECTION)
    new = scope_hint_types(_scrap(), "new_vehicle")

    assert "old_vehicle" in old and "new_vehicle" not in old
    assert "new_vehicle" in new and "old_vehicle" not in new


def test_scope_without_materials_yields_an_empty_set() -> None:
    pack = BusinessExtensionPack(business_type="sample", scopes=("sample",))
    assert material_types_for_scope(pack, OLD_SECTION) == frozenset()
    assert scope_hint_types(pack, OLD_SECTION) == frozenset()


def test_every_declared_scope_yields_a_non_empty_set() -> None:
    """每个业务声明的分区都必须推导出材料——推出空集说明材料要求声明漏了。"""
    for business_type, pack in BUSINESS_PACKS.items():
        for scope in pack.scopes:
            assert material_types_for_scope(pack, scope), f"{business_type.value}/{scope}"


def test_pack_without_material_policy_derives_nothing() -> None:
    """未声明材料要求的业务不能凭空造出材料集合。"""
    pack = BusinessExtensionPack(business_type="unconfigured", scopes=(OLD_SECTION,))
    assert material_types_for_scope(pack, OLD_SECTION) == frozenset()
    assert scope_hint_types(pack, OLD_SECTION) == frozenset()
