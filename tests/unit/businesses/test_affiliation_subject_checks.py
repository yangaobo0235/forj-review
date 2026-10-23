import pytest

from forj_review_service.businesses.rules.affiliation import (
    build_affiliation_subject_check,
)
from forj_review_service.contracts.review import FieldObservation
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


def item(field: str, value: str, source_id: str = "source") -> FieldObservation:
    return FieldObservation(
        field=field,
        source_type="image",
        source_id=source_id,
        document_type=(
            "business_license"
            if field.startswith("business_license")
            else "identity_card"
            if field.startswith("identity_card")
            else "vehicle_license"
        ),
        value=value,
    )


def identity(name: str, source_prefix: str) -> list[FieldObservation]:
    return [
        item("identity_card.name", name, f"{source_prefix}-front"),
        item("identity_card.side", "FRONT", f"{source_prefix}-front"),
        item("identity_card.side", "BACK", f"{source_prefix}-back"),
    ]


def test_same_person_requires_identity_and_different_people_conflict() -> None:
    missing = build_affiliation_subject_check("张三", "张三", [], business=TEST_BUSINESS)
    same = build_affiliation_subject_check(
        "张三", "张三", identity("张三", "id-a"), business=TEST_BUSINESS
    )
    different = build_affiliation_subject_check(
        "张三",
        "李四",
        [*identity("张三", "id-a"), *identity("李四", "id-b")],
        business=TEST_BUSINESS,
    )

    assert missing.check.status == "INSUFFICIENT"
    assert same.check.status == "MATCH"
    assert same.owner_types == ("PERSONAL", "PERSONAL")
    assert different.check.status == "CONFLICT"


def test_different_companies_match_only_with_separate_licenses_and_common_legal_representative() -> (
    None
):
    observations = [
        item("business_license.company_name", "甲运输有限公司", "license-a"),
        item("business_license.legal_representative", "张三", "license-a"),
        item("business_license.company_name", "乙运输有限公司", "license-b"),
        item("business_license.legal_representative", "张三", "license-b"),
    ]

    result = build_affiliation_subject_check(
        "甲运输有限公司", "乙运输有限公司", observations, business=TEST_BUSINESS
    )

    assert result.check.status == "MATCH"
    assert result.owner_types == ("COMPANY", "COMPANY")


def test_mixed_person_company_requires_matching_legal_representative() -> None:
    matched = build_affiliation_subject_check(
        "张三",
        "甲运输有限公司",
        [
            item("business_license.company_name", "甲运输有限公司", "license-a"),
            item("business_license.legal_representative", "张三", "license-a"),
            *identity("张三", "id-a"),
        ],
        business=TEST_BUSINESS,
    )
    missing = build_affiliation_subject_check("张三", "甲运输有限公司", [], business=TEST_BUSINESS)

    assert matched.check.status == "MATCH"
    assert matched.owner_types == ("PERSONAL", "COMPANY")
    assert missing.check.status == "INSUFFICIENT"


def test_different_companies_require_readable_legal_representatives() -> None:
    result = build_affiliation_subject_check(
        "甲运输有限公司",
        "乙运输有限公司",
        [
            item("business_license.company_name", "甲运输有限公司", "license-a"),
            item("business_license.legal_representative", "无法识别", "license-a"),
            item("business_license.company_name", "乙运输有限公司", "license-b"),
            item("business_license.legal_representative", "无法识别", "license-b"),
        ],
        business=TEST_BUSINESS,
    )

    assert result.check.status == "INSUFFICIENT"


def test_conflicting_values_from_one_license_image_are_insufficient_evidence() -> None:
    result = build_affiliation_subject_check(
        "甲运输有限公司",
        "乙运输有限公司",
        [
            item("business_license.company_name", "甲运输有限公司", "license-a"),
            item("business_license.legal_representative", "张三", "license-a"),
            item("business_license.legal_representative", "李四", "license-a"),
            item("business_license.company_name", "乙运输有限公司", "license-b"),
            item("business_license.legal_representative", "张三", "license-b"),
        ],
        business=TEST_BUSINESS,
    )

    assert result.check.status == "INSUFFICIENT"


def test_one_license_source_cannot_support_two_different_companies() -> None:
    result = build_affiliation_subject_check(
        "甲运输有限公司",
        "乙运输有限公司",
        [
            item("business_license.company_name", "甲运输有限公司", "license-a"),
            item("business_license.company_name", "乙运输有限公司", "license-a"),
            item("business_license.legal_representative", "张三", "license-a"),
        ],
        business=TEST_BUSINESS,
    )

    assert result.check.status == "INSUFFICIENT"


@pytest.mark.parametrize("name", ["张*", "???", "?张", "法人未知", "甲公司"])
def test_unreadable_or_non_person_representatives_never_authorize_actions(name):
    observations = [
        item("business_license.company_name", "甲运输有限公司", "license-a"),
        item("business_license.legal_representative", name, "license-a"),
        item("business_license.company_name", "乙运输有限公司", "license-b"),
        item("business_license.legal_representative", name, "license-b"),
    ]
    result = build_affiliation_subject_check(
        "甲运输有限公司", "乙运输有限公司", observations, business=TEST_BUSINESS
    )
    assert result.check.status == "INSUFFICIENT"


@pytest.mark.parametrize(
    "old,new,legal,expected",
    [
        ("甲运输有限公司", "张三", "张三", "MATCH"),
        ("甲运输有限公司", "张三", "李四", "CONFLICT"),
        ("张三", "甲运输有限公司", "李四", "CONFLICT"),
        ("甲运输有限公司", "乙运输有限公司", "李四", "CONFLICT"),
        ("甲运输主体", "张三", "张三", "INSUFFICIENT"),
        ("甲运输有限公司", "甲运输有限责任公司", "张三", "INSUFFICIENT"),
    ],
)
def test_subject_direction_and_name_matrix(old, new, legal, expected):
    observations = [
        item("business_license.company_name", "甲运输有限公司", "license-a"),
        item("business_license.legal_representative", legal, "license-a"),
        item("business_license.company_name", "乙运输有限公司", "license-b"),
        item("business_license.legal_representative", "张三", "license-b"),
    ]
    for index, person in enumerate(
        name for name in (old, new) if len(name) in {2, 3, 4} and not name.endswith("公司")
    ):
        observations.extend(identity(person, f"id-{index}"))
    result = build_affiliation_subject_check(old, new, observations, business=TEST_BUSINESS)
    assert result.check.status == expected


def test_subject_evidence_keeps_owners_and_relevant_license_sources_only():
    observations = [
        item("old_vehicle.owner", "甲运输有限公司", "old-image"),
        item("new_vehicle.owner", "乙运输有限公司", "new-image"),
        item("business_license.company_name", "甲运输有限公司", "license-a"),
        item("business_license.legal_representative", "张三", "license-a"),
        item("business_license.company_name", "乙运输有限公司", "license-b"),
        item("business_license.legal_representative", "张三", "license-b"),
        item("business_license.company_name", "无关有限公司", "unrelated"),
        item("business_license.legal_representative", "李四", "unrelated"),
        item("invoice.amount", "80000", "invoice"),
    ]
    result = build_affiliation_subject_check(
        "甲运输有限公司", "乙运输有限公司", observations, business=TEST_BUSINESS
    )
    assert {e.source_id for e in result.check.evidence} == {
        "old-image",
        "new-image",
        "license-a",
        "license-b",
    }
    assert {value.source for value in result.check.values} >= {
        "旧车主体类型",
        "新车主体类型",
        "旧车所有人",
        "新车所有人",
    }


def test_duplicate_matching_license_images_are_insufficient():
    observations = [
        item("business_license.company_name", "甲运输有限公司", source)
        for source in ("license-a", "license-copy")
    ] + [
        item("business_license.legal_representative", "张三", source)
        for source in ("license-a", "license-copy")
    ]
    result = build_affiliation_subject_check(
        "甲运输有限公司", "张三", observations, business=TEST_BUSINESS
    )
    assert result.check.status == "INSUFFICIENT"


def test_company_subject_type_comes_from_matching_materials():
    result = build_affiliation_subject_check(
        "甲运输有限公司",
        "甲运输有限公司",
        [
            item("business_license.company_name", "甲运输有限公司", "license-a"),
            item("business_license.legal_representative", "张三", "license-a"),
        ],
        business=TEST_BUSINESS,
    )

    assert result.check.status == "MATCH"
    assert result.owner_types == ("COMPANY", "COMPANY")


def test_subject_check_returns_exact_dynamic_identity_requirements():
    result = build_affiliation_subject_check(
        "张三",
        "张三",
        [
            item("identity_card.name", "张三", "id-front"),
            item("identity_card.side", "FRONT", "id-front"),
        ],
        business=TEST_BUSINESS,
    )

    requirements = result.check.details["subject_requirements"]
    assert [(item["document"], item["status"]) for item in requirements] == [
        ("identity_card_front", "PRESENT"),
        ("identity_card_back", "MISSING"),
    ]
    assert result.check.status == "INSUFFICIENT"


def test_same_person_uses_one_shared_identity_requirement() -> None:
    result = build_affiliation_subject_check(
        "张三", "张三", identity("张三", "id-a"), business=TEST_BUSINESS
    )

    assert result.check.status == "MATCH"
    assert {item["party"] for item in result.check.details["subject_requirements"]} == {"SHARED"}


def test_identity_back_cannot_be_reused_for_two_different_people() -> None:
    result = build_affiliation_subject_check(
        "张三",
        "李四",
        [
            item("identity_card.name", "张三", "id-a-front"),
            item("identity_card.side", "FRONT", "id-a-front"),
            item("identity_card.name", "李四", "id-b-front"),
            item("identity_card.side", "FRONT", "id-b-front"),
            item("identity_card.side", "BACK", "id-back"),
        ],
        business=TEST_BUSINESS,
    )

    back_requirements = [
        item
        for item in result.check.details["subject_requirements"]
        if item["document"] == "identity_card_back"
    ]
    assert result.check.status == "INSUFFICIENT"
    assert [item["status"] for item in back_requirements] == [
        "UNCERTAIN",
        "UNCERTAIN",
    ]
    assert all("无法确认对应关系" in item["reason"] for item in back_requirements)


@pytest.mark.parametrize(
    "actual,subject_status,expected_status",
    [
        ("", "MATCH", "MATCH"),
        ("个人", "MATCH", "MATCH"),
        ("公司", "MATCH", "CONFLICT"),
        ("", "INSUFFICIENT", "INSUFFICIENT"),
    ],
)
def test_affiliation_control_check_preserves_subject_gating(
    actual, subject_status, expected_status
):
    from forj_review_service.businesses.rules.affiliation_controls import check_affiliation_control
    from forj_review_service.contracts.checks import CheckResult, CheckResultValue

    subject = CheckResult(
        check_id="AFFILIATION-SUBJECT-001",
        label="主体关系",
        status=subject_status,
        reason="合成主体检查",
        values=[CheckResultValue(source="旧车主体类型", value="PERSONAL")],
    )
    check = check_affiliation_control(
        "old_vehicle.affiliation", actual, [subject], type_source="旧车主体类型"
    )
    assert check.status == expected_status
    assert check.values[0].value == actual
