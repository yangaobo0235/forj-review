"""业务声明的交叉引用校验。

这些引用此前完全没有校验：写错一个字段键不会报错，只会让那条观察值在
`field_policy()` 处被静默丢弃——审核员看到「该字段没有材料证据」，而不是
「配置写错了」。本文件既锁定现有声明是干净的，也用故意写错的声明证明
校验真的会拦下来。
"""

import pytest

from forj_review_service.businesses.packs import BUSINESS_PACKS
from forj_review_service.businesses.packs.model import (
    BusinessExtensionPack,
    CompositeFieldDeclaration,
    FieldDeclaration,
    MaterialDeclaration,
    RouteDeclaration,
    validate_pack_references,
)


def _pack(**overrides) -> BusinessExtensionPack:
    """最小可用声明，供负向用例逐个改坏。"""
    base: dict = {
        "business_type": "test_business",
        "scopes": ("vehicle",),
        "fields": (
            FieldDeclaration(key="vehicle.vin", label="车架号", section="vehicle"),
            FieldDeclaration(key="vehicle.origin", label="产地", section="vehicle"),
        ),
        "materials": (
            MaterialDeclaration(document_type="vehicle_license", display_name="行驶证"),
        ),
    }
    base.update(overrides)
    return BusinessExtensionPack(**base)


@pytest.mark.parametrize("business_type", sorted(item.value for item in BUSINESS_PACKS))
def test_every_production_pack_has_valid_references(business_type: str) -> None:
    """生产声明必须全部通过；这条是新增业务时的第一道门禁。"""
    pack = next(item for item in BUSINESS_PACKS.values() if item.business_type == business_type)
    validate_pack_references(pack)


def test_minimal_pack_is_clean() -> None:
    """先证明基线本身没问题，否则下面的负向用例可能因为别的原因失败。"""
    validate_pack_references(_pack())


def test_route_target_must_be_declared() -> None:
    """路由目标写错一个字母：必须报错，否则观察值会被静默丢弃。"""
    pack = _pack(
        routes=(RouteDeclaration("vehicle_license", "vehicle.vin", ("vehicle.vinn",)),)
    )
    with pytest.raises(ValueError, match="vehicle.vinn"):
        validate_pack_references(pack)


def test_duplicate_route_target_is_rejected() -> None:
    """同一材料字段到同一领域字段的重复路由没有额外语义，应直接拦截。"""
    pack = _pack(
        routes=(
            RouteDeclaration("vehicle_license", "vehicle.vin", ("vehicle.vin",)),
            RouteDeclaration("vehicle_license", "vehicle.vin", ("vehicle.vin",)),
        )
    )
    with pytest.raises(ValueError, match="路由重复声明"):
        validate_pack_references(pack)


def test_scope_placeholder_is_expanded_for_every_declared_scope() -> None:
    """`{scope}` 会按分区展开：只在某个分区存在的键，必须限定 scope。"""
    broken = _pack(
        scopes=("old_vehicle", "new_vehicle"),
        fields=(
            FieldDeclaration(key="old_vehicle.vin", label="旧车车架号"),
            FieldDeclaration(key="new_vehicle.vin", label="新车车架号"),
        ),
        routes=(RouteDeclaration("vehicle_license", "vehicle.vin", ("{scope}.vin",)),),
    )
    # 两个分区都有对应键时是合法的。
    validate_pack_references(broken)

    missing_one_side = _pack(
        scopes=("old_vehicle", "new_vehicle"),
        fields=(FieldDeclaration(key="old_vehicle.vin", label="旧车车架号"),),
        routes=(RouteDeclaration("vehicle_license", "vehicle.vin", ("{scope}.vin",)),),
    )
    with pytest.raises(ValueError, match="new_vehicle.vin"):
        validate_pack_references(missing_one_side)


def test_route_scoped_declaration_only_expands_that_scope() -> None:
    """声明了 scope 的路由只对该分区生效，不得按全部分区展开。"""
    pack = _pack(
        scopes=("old_vehicle", "new_vehicle"),
        fields=(FieldDeclaration(key="new_vehicle.vin", label="新车车架号"),),
        routes=(
            RouteDeclaration(
                "vehicle_license", "vehicle.vin", ("{scope}.vin",), scope="new_vehicle"
            ),
        ),
    )
    validate_pack_references(pack)


def test_identity_anchor_must_be_declared() -> None:
    """指纹锚点写错会让页面写回和原图定位全部被拒绝，且报错不会指向这里。"""
    pack = _pack(identity_anchors=("vehicle.vinn",))
    with pytest.raises(ValueError, match="identity_anchors"):
        validate_pack_references(pack)


def test_identity_anchor_pointing_at_a_real_field_passes() -> None:
    validate_pack_references(_pack(identity_anchors=("vehicle.vin",)))


def test_composite_field_must_reference_declared_fields() -> None:
    """组合字段的键写错会让它永久返回「未取得材料核验值」。"""
    pack = _pack(
        page_field_composites=(
            CompositeFieldDeclaration(
                check_id="FIELD-TEST",
                label="组合",
                primary_field="vehicle.vin",
                secondary_field="vehicle.vinn",
                material_field="vehicle.vin",
                primary_label="甲",
                secondary_label="乙",
            ),
        )
    )
    with pytest.raises(ValueError, match="secondary_field"):
        validate_pack_references(pack)


def test_derived_field_must_reference_declared_fields() -> None:
    """派生关系写错会让派生字段永远补不出来。"""
    pack = _pack(
        materials=(
            MaterialDeclaration(
                document_type="vehicle_license",
                display_name="行驶证",
                derived_field=("vehicle.vin", "vehicle.vinn"),
            ),
        )
    )
    with pytest.raises(ValueError, match="vehicle.vinn"):
        validate_pack_references(pack)


def test_field_check_binding_must_reference_a_declared_field() -> None:
    """检查项绑错字段会让结论变成一张看不见的孤立卡片。"""
    pack = _pack(field_check_bindings=(("SOME-CHECK", "vehicle.vinn"),))
    with pytest.raises(ValueError, match="SOME-CHECK"):
        validate_pack_references(pack)


def test_invoice_verification_field_must_be_declared() -> None:
    pack = _pack(invoice_verification_field="vehicle.vinn")
    with pytest.raises(ValueError, match="invoice_verification_field"):
        validate_pack_references(pack)


def test_material_field_shared_policy_must_be_declared() -> None:
    pack = _pack(
        fields=(
            FieldDeclaration(key="vehicle.vin", label="车架号", material_field="vehicle.vinn"),
        )
    )
    with pytest.raises(ValueError, match="material_field"):
        validate_pack_references(pack)


def test_duplicate_field_key_is_rejected() -> None:
    """重复的字段键会互相覆盖，后声明的静默吃掉前一个。"""
    pack = _pack(
        fields=(
            FieldDeclaration(key="vehicle.vin", label="车架号"),
            FieldDeclaration(key="vehicle.vin", label="另一个车架号"),
        )
    )
    with pytest.raises(ValueError, match="重复声明"):
        validate_pack_references(pack)


@pytest.mark.parametrize("options", [(), ((),)])
def test_presence_never_accepts_an_empty_alternative(options):
    from forj_review_service.businesses.material_policies import (
        MaterialPolicy,
        MaterialPresenceGroup,
    )

    pack = _pack(material_policy=MaterialPolicy(
        mode="enforce", presence_groups=(MaterialPresenceGroup("subject", "主体", options),),
    ))
    with pytest.raises(ValueError, match="空方案"):
        validate_pack_references(pack)


def test_presence_rejects_an_undeclared_document_type():
    from forj_review_service.businesses.material_policies import (
        MaterialPolicy,
        MaterialPresenceGroup,
        MaterialRequirement,
    )

    pack = _pack(material_policy=MaterialPolicy(
        mode="enforce", presence_groups=(MaterialPresenceGroup("subject", "主体", (
            (MaterialRequirement("synthetic_unknown", "vehicle"),),
        )),),
    ))
    with pytest.raises(ValueError, match="未声明的材料"):
        validate_pack_references(pack)


def test_slot_scope_must_be_a_declared_scope() -> None:
    pack = _pack(
        materials=(
            MaterialDeclaration(
                document_type="vehicle_license",
                display_name="行驶证",
                slots=(("old_vehicle", 1),),
            ),
        )
    )
    with pytest.raises(ValueError, match="上传槽位"):
        validate_pack_references(pack)


def test_all_problems_are_reported_at_once() -> None:
    """一次报全部问题：逐个改会变成「修一处、启动一次」的来回。"""
    pack = _pack(
        identity_anchors=("vehicle.anchor_typo",),
        invoice_verification_field="vehicle.invoice_typo",
    )
    with pytest.raises(ValueError) as excinfo:
        validate_pack_references(pack)
    message = str(excinfo.value)
    assert "vehicle.anchor_typo" in message
    assert "vehicle.invoice_typo" in message
