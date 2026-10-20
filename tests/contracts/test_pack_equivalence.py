"""业务扩展包的完整性。

扩展包现在是字段清单、证据策略和材料策略的**唯一来源**：`review_fields`、
`field_evidence_policies` 和 `document_policies` 都从它生成。所以这组用例
锁定声明本身的关键内容——改字段、改材料或改权威链都会在这里失败。
"""

from forj_review_service.businesses.field_policies import (
    FIELD_EVIDENCE_POLICIES,
    field_policy,
)
from forj_review_service.businesses.materials import DOCUMENT_POLICIES
from forj_review_service.businesses.packs import BUSINESS_PACKS
from forj_review_service.businesses.packs import SCRAP_REPLACEMENT_PACK as PACK
from forj_review_service.businesses.page_catalog import identities_for_path
from forj_review_service.businesses.profiles import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
)
from forj_review_service.contracts.review import BusinessType, Region

SCOPES = ("old_vehicle", "new_vehicle", "transfer", "unknown")


def test_required_fields_keep_their_declared_order() -> None:
    sections = PACK.section_field_keys()
    assert sections["old_vehicle"] == (
        "old_vehicle.type",
        "old_vehicle.recycle_date",
        "scrap_certificate.certificate_no",
        "old_vehicle.vin",
        "old_vehicle.plate_no",
        "old_vehicle.owner",
        "old_vehicle.engine_model",
        "old_vehicle.emission_standard",
        "old_vehicle.fuel_type",
        "old_vehicle.is_jiefang",
    )
    assert sections["new_vehicle"] == (
        "new_vehicle.fuel_type",
        "invoice.code",
        "invoice.invoice_no",
        "invoice.amount",
        "invoice.invoice_date",
        "new_vehicle.vin",
        "new_vehicle.plate_no",
        "new_vehicle.owner",
        "new_vehicle.registration_date",
        "application.terminal_certificate_no",
        "application.customer_name",
        "application.terminal_phone",
    )
    assert PACK.required_keys() == sections["old_vehicle"] + sections["new_vehicle"]


def test_every_field_is_declared_once_and_has_a_label() -> None:
    keys = [item.key for item in PACK.fields]

    assert len(set(keys)) == len(keys), "字段键重复"
    assert all(item.label for item in PACK.fields), "存在没有中文标签的字段"


def test_evidence_policies_cover_exactly_the_declared_fields() -> None:
    """有 `mode` 的字段自带策略；有 `material_field` 的字段共用材料侧策略。

    证据策略表按业务**合成**：各业务的字段键带自己的分区前缀，不会互相覆盖。
    """
    declared = {
        item.key
        for pack in BUSINESS_PACKS.values()
        for item in pack.fields
        if item.mode is not None or item.material_field is not None
    }

    assert declared == set(FIELD_EVIDENCE_POLICIES)


def test_page_control_shares_the_material_evidence_policy() -> None:
    page = field_policy("page_ocr.new_vehicle_vin")
    material = field_policy("new_vehicle.vin")

    assert page.field == "page_ocr.new_vehicle_vin"
    assert (
        page.mode,
        page.allowed_document_types,
        page.normalizer,
        page.authority,
    ) == (
        material.mode,
        material.allowed_document_types,
        material.normalizer,
        material.authority,
    )


def test_old_vehicle_vin_keeps_its_authority_chain() -> None:
    policy = field_policy("old_vehicle.vin")

    assert [(rule.source, rule.match, rule.required) for rule in policy.authority] == [
        ("qr_page", "full", False),
        ("page", "full", True),
        ("image", "suffix8", False),
    ]


def test_every_material_declares_display_name_fields_guidance_and_hints() -> None:
    # 材料策略表按 `document_type` 合成：同一份材料被多个业务共用时合成一条，
    # 各业务的材料类型合集必须与之一一对应，既不能丢也不能多。
    declared = {
        item.document_type for pack in BUSINESS_PACKS.values() for item in pack.materials
    }

    assert declared == set(DOCUMENT_POLICIES)

    for material in PACK.materials:
        assert material.display_name, material.document_type
        assert material.fields, material.document_type
        assert material.guidance, material.document_type
        assert material.hints, material.document_type
        for scope in SCOPES:
            assert material.fields_for_scope(scope), (material.document_type, scope)
            assert material.guidance_for_scope(scope), (material.document_type, scope)


def test_page_groups_map_every_title_to_a_scope() -> None:
    assert PACK.page_groups, "页面分组标题表为空"
    for group in PACK.page_groups:
        assert group.label and group.scope and group.title, group

    scopes = {group.scope for group in PACK.page_groups}
    # 页面分区至少覆盖本业务认定的分区，以及独立的营业执照 / 身份证区域。
    assert set(PACK.scopes) <= scopes
    assert {"business_license", "identity", "other"} <= scopes


def test_collected_fields_all_have_aliases() -> None:
    """有别名才会被前端采集；没有别名的字段是写回目标或纯展示字段。"""
    collected = [item for item in PACK.fields if item.aliases]

    assert len(collected) >= 20
    for item in collected:
        # 第一个别名是页面上的规范写法，应与字段标签一致。
        assert item.aliases[0] == item.label, item.key
        assert len(set(item.aliases)) == len(item.aliases), f"{item.key} 别名重复"


def test_every_route_points_at_a_declared_material_and_scope() -> None:
    document_types = {item.document_type for item in PACK.materials}
    scopes = set(PACK.scopes)

    assert PACK.routes, "路由表为空"
    for rule in PACK.routes:
        assert rule.document_type in document_types, rule
        assert rule.targets, rule
        assert rule.scope == "" or rule.scope in scopes, rule
        for target in rule.targets:
            # 目标要么带分区占位符，要么是本来就完整的领域字段键。
            assert "{scope}" in target or "." in target, rule


def test_every_scope_aware_material_declares_how_fields_are_routed() -> None:
    """不跟随分区的材料（营业执照、身份证）不受此约束；其余材料必须至少
    声明一种落位方式：通用车辆后缀，或逐条显式路由。两者都没有的字段会被
    静默丢弃，这正是不加断言就发现不了的那类问题。"""
    for material in PACK.materials:
        if material.scope_independent:
            continue
        explicit = {
            rule.source_field
            for rule in PACK.routes
            if rule.document_type == material.document_type
        }
        assert material.scoped_vehicle_fields or explicit, (
            f"{material.document_type} 既没有通用车辆字段声明也没有显式路由"
        )

    # 登记证书只认 vehicle. 前缀，靠逐条显式路由。
    assert PACK.material("registration_certificate").scoped_vehicle_fields == ()
    assert PACK.material("vehicle_license").scoped_vehicle_fields


def test_scope_independent_materials_are_not_bound_to_a_scope() -> None:
    for document_type in ("business_license", "identity_card"):
        material = PACK.material(document_type)
        assert material.scope_independent is True, document_type
        assert not material.allowed_scopes, document_type


# --- 从声明生成的 Profile、政策与页面路由 -----------------------------------


def test_regions_declare_their_own_policy_and_page_paths() -> None:
    regions = {item.region: item for item in PACK.regions}

    assert set(regions) == {Region.QINGDAO, Region.CHANGCHUN}
    for declaration in PACK.regions:
        assert declaration.replacement_policy is not None, declaration.region
        assert declaration.admin_paths, declaration.region
        # 政策对象必须与地区一致，否则启动期校验会失败。
        assert declaration.replacement_policy.region is declaration.region
        assert declaration.policy_capability_id() == f"{declaration.region.value}_replacement_policy"


def test_shared_capabilities_are_declared_once_for_all_regions() -> None:
    shared = {spec.capability_id for spec in PACK.capability_specs}

    assert shared == {
        "material_completeness",
        "scrap_certificate_qr",
        "affiliation_subject",
        "verify_invoice",
        "owner_consistency",
        "vehicle_type_light_duty",
        "set_affiliation",
        "emission_standard",
    }
    # 地区政策能力不在共用清单里，由地区推导。
    assert not any(item.endswith("_replacement_policy") for item in shared)

    for declaration in PACK.regions:
        specs = {spec.capability_id for spec in PACK.capabilities_for(declaration)}
        bindings = {binding.capability_id for binding in PACK.bindings_for(declaration)}

        assert specs == shared | {declaration.policy_capability_id()}
        # 每个能力都必须有绑定，否则启动期校验会失败。
        assert bindings == specs


def test_profiles_come_from_the_declaration() -> None:
    for profile in (SCRAP_REPLACEMENT_QINGDAO, SCRAP_REPLACEMENT_CHANGCHUN):
        declaration = PACK.region(profile.region)

        assert profile.required_fields == PACK.required_keys()
        assert profile.material_policy is PACK.material_policy
        assert profile.page_action_ids == PACK.page_action_ids
        assert profile.page_interaction is PACK.page_interaction
        assert profile.replacement_policy is declaration.replacement_policy
        assert {section.id for section in profile.sections} == set(PACK.section_field_keys())


def test_admin_review_routes_cover_every_declared_page_path() -> None:
    declared = {
        path: (BusinessType(PACK.business_type), declaration.region)
        for declaration in PACK.regions
        for path in declaration.admin_paths
    }

    assert declared
    for path, expected in declared.items():
        # 一个地址可能被多个业务共用（一致性审核与过户审核同址），所以这里
        # 只能要求声明的业务/地区在候选集合里，不能要求它唯一。
        allowed = {(item.business_type, item.region) for item in identities_for_path(path)}
        assert expected in allowed, path
