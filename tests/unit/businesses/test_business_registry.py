from dataclasses import replace

import pytest

from forj_review_assistant.businesses.registry import (
    BusinessProfileNotFound,
    BusinessRegistry,
    build_business_registry,
)
from forj_review_assistant.contracts.review import BusinessType, Region, ReviewRequest


def test_registry_resolves_scrap_profile_by_identity() -> None:
    profile = build_business_registry().resolve(
        BusinessType.SCRAP_REPLACEMENT,
        Region.QINGDAO,
        "1.0",
    )

    assert profile.business_type is BusinessType.SCRAP_REPLACEMENT
    assert "old_vehicle.vin" in profile.required_fields
    assert profile.rules_configured is True
    # 扩展包收口后，Profile 的能力用规范字段声明；external_checks / rule_groups
    # 是迁移期的旧选择器，从这里构造出来的 Profile 不再使用它们。
    assert {spec.capability_id for spec in profile.capabilities} == {
        "material_completeness",
        "scrap_certificate_qr",
        "qingdao_replacement_policy",
        "affiliation_subject",
        "verify_invoice",
        "owner_consistency",
        "vehicle_type_light_duty",
    }
    assert profile.page_action_ids == ("fill_affiliation_fields",)


def test_registry_resolves_changchun_profiles_without_fallback() -> None:
    registry = build_business_registry()

    replacement = registry.resolve(
        BusinessType.SCRAP_REPLACEMENT,
        Region.CHANGCHUN,
        "1.0",
    )
    assert replacement.region is Region.CHANGCHUN
    assert "changchun_replacement_policy" in {spec.capability_id for spec in replacement.capabilities}

    # 一致性审核不分地区：青岛和长春两个页面地址同一套规则，取默认地区。
    # 拿 changchun 去取它会走近似匹配，取到的仍然是默认地区那一份。
    consistency = registry.resolve(
        BusinessType.CONSISTENCY,
        Region.CHANGCHUN,
        "1.0",
    )
    assert consistency.region is Region.DEFAULT
    assert consistency.rules_configured is False




def test_registry_keeps_unconfigured_businesses_out_of_scrap_rules() -> None:
    profile = build_business_registry().resolve(
        BusinessType.CONSISTENCY,
        Region.QINGDAO,
        "1.0",
    )

    assert profile.required_fields == ()
    assert profile.rules_configured is False


def test_registry_uses_only_explicit_default_region_fallback() -> None:
    profile = build_business_registry().resolve(
        BusinessType.VEHICLE_SOURCE,
        Region.QINGDAO,
        "1.0",
    )

    assert profile.region is Region.DEFAULT

    with pytest.raises(BusinessProfileNotFound):
        build_business_registry().resolve(
            BusinessType.SCRAP_REPLACEMENT,
            Region.DEFAULT,
            "1.0",
        )


def test_region_scoped_request_without_region_cannot_resolve_a_scrap_profile() -> None:
    request = ReviewRequest(page_url="https://example.test/scrap-replace")

    assert request.region is Region.DEFAULT
    with pytest.raises(BusinessProfileNotFound):
        build_business_registry().resolve(
            request.business_type,
            request.region,
            request.profile_version,
        )


def test_registry_rejects_unknown_profile_version() -> None:
    with pytest.raises(BusinessProfileNotFound):
        build_business_registry().resolve(
            BusinessType.CONSISTENCY,
            Region.QINGDAO,
            "9.9",
        )


def test_registry_keeps_same_business_region_profiles_separate_by_page_id() -> None:
    base = build_business_registry().resolve(BusinessType.VEHICLE_SOURCE, Region.DEFAULT, "1.0")
    registry = BusinessRegistry(
        (replace(base, page_id="vehicle-source-a"), replace(base, page_id="vehicle-source-b"))
    )

    assert registry.resolve(BusinessType.VEHICLE_SOURCE, Region.DEFAULT, "1.0", page_id="vehicle-source-a").page_id == "vehicle-source-a"
    assert registry.resolve(BusinessType.VEHICLE_SOURCE, Region.DEFAULT, "1.0", page_id="vehicle-source-b").page_id == "vehicle-source-b"
    with pytest.raises(BusinessProfileNotFound, match="page_id is required"):
        registry.resolve(BusinessType.VEHICLE_SOURCE, Region.DEFAULT, "1.0")
