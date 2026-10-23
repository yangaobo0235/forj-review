import json

from forj_review_service.businesses.recognition_prompts import (
    OUTPUT_RULES,
    READING_RULES,
    build_recognition_prompt,
    material_catalog,
)
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


def policy_prompt(kind, image_index=0, business_scope="unknown"):
    entry = material_catalog(TEST_BUSINESS, business_scope)[kind]
    return json.dumps(entry, ensure_ascii=False) + READING_RULES + OUTPUT_RULES


def test_prompts_restrict_model_scope_and_output_contract() -> None:
    invoice_prompt = policy_prompt("invoice", image_index=3)

    assert "机动车销售发票" in invoice_prompt
    assert "invoice.code" in invoice_prompt
    assert "old_vehicle.vin" not in invoice_prompt
    assert "box=[x1,y1,x2,y2]" in invoice_prompt
    assert "上传张数、组内序号不能证明类型" in invoice_prompt

    classification_prompt = build_recognition_prompt(0, business=TEST_BUSINESS)
    assert "unsupported" in classification_prompt
    assert "在一次响应中判断实际资料类型" in classification_prompt
    assert "identity_card" in classification_prompt
    assert "facts" in classification_prompt

    identity_prompt = policy_prompt(
        "identity_card",
        image_index=4,
        business_scope="identity",
    )
    assert "identity_card.name" in identity_prompt
    assert "identity_card.side" in identity_prompt
    assert "identity_card.name" in identity_prompt


def test_materials_keep_scrap_scopes_isolated() -> None:
    policy = TEST_BUSINESS.document_policies["vehicle_license"]
    assert policy.fields_for_scope("old_vehicle") == (
        "vehicle.type",
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
    )
    assert policy.fields_for_scope("new_vehicle") == (
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
        "vehicle.registration_date",
    )


def test_engine_model_is_extracted_only_from_auxiliary_materials() -> None:
    engine_field = "vehicle.engine_model"

    assert engine_field in TEST_BUSINESS.document_policies["registration_certificate"].fields
    assert all(
        engine_field not in policy.fields
        for document_type, policy in TEST_BUSINESS.document_policies.items()
        if document_type not in {"registration_certificate", "vehicle_nameplate"}
    )
    assert "registration_certificate" in build_recognition_prompt(0, business=TEST_BUSINESS)


def test_registration_certificate_prompt_fields_follow_business_scope() -> None:
    old_prompt = policy_prompt(
        "registration_certificate",
        image_index=2,
        business_scope="old_vehicle",
    )
    new_prompt = policy_prompt(
        "registration_certificate",
        image_index=2,
        business_scope="new_vehicle",
    )

    assert "vehicle.owner" not in old_prompt
    assert "vehicle.vin" in old_prompt
    assert "vehicle.engine_model" in old_prompt
    assert "vehicle.owner" not in new_prompt
    assert "vehicle.vin" in new_prompt
    assert "vehicle.engine_model" not in new_prompt
    assert "registration.covered_pages" in old_prompt
    assert "registration.covered_pages" in new_prompt


def test_new_owner_allowlist_excludes_registration_certificate() -> None:
    from forj_review_service.businesses.field_policies import field_policy

    policy = field_policy("new_vehicle.owner", business=TEST_BUSINESS)
    assert policy is not None
    assert policy.allowed_document_types == ("vehicle_license", "invoice")


def test_scrap_replacement_prompts_use_scope_specific_allowlists_and_confusion_guards() -> None:
    old_license = policy_prompt("vehicle_license", 1, "old_vehicle")
    new_license = policy_prompt("vehicle_license", 1, "new_vehicle")
    old_registration = policy_prompt("registration_certificate", 2, "old_vehicle")
    new_registration = policy_prompt("registration_certificate", 2, "new_vehicle")
    invoice = policy_prompt("invoice", 3, "new_vehicle")

    assert "发动机号码不是发动机型号" in old_license
    assert "注册日期" in new_license
    assert "vehicle.engine_model" in old_registration
    assert "vehicle.engine_model" not in new_registration
    assert "第13项‘燃料种类’" in new_registration
    assert "模型不要输出 invoice.code" in invoice
    assert "invoice.phone" not in invoice
    assert "图片可能横向" in invoice


def test_combined_prompt_restricts_registration_fields_by_business_scope() -> None:
    old_prompt = build_recognition_prompt(2, "old_vehicle", business=TEST_BUSINESS)
    new_prompt = build_recognition_prompt(2, "new_vehicle", business=TEST_BUSINESS)

    assert "vehicle.engine_model" in old_prompt
    assert "vehicle.engine_model" not in new_prompt


def test_combined_prompt_uses_physical_vehicle_type_and_neutral_fields() -> None:
    prompt = build_recognition_prompt(image_index=2, business=TEST_BUSINESS)

    assert "vehicle_license" in prompt
    assert "vehicle.vin" in prompt
    assert "vehicle.plate_no" in prompt
    assert "vehicle.owner" in prompt
    assert '"business_scope":"unknown"' in prompt


def test_retired_transfer_fields_are_not_in_any_material_whitelist() -> None:
    """新版过户不推断初始车主和过户记录；旧版推断字段不能重新混入识别白名单。"""
    retired = {"registration.initial_owner", "registration.transfer_records"}

    for document_type, policy in TEST_BUSINESS.document_policies.items():
        for scope in ("old_vehicle", "new_vehicle", "transfer", "unknown"):
            leaked = retired & set(policy.fields_for_scope(scope))
            assert not leaked, (
                f"{document_type} 的 {scope} 白名单仍包含停用过户字段：{sorted(leaked)}"
            )
