from forj_review_assistant.businesses.materials import (
    DOCUMENT_POLICIES,
    build_classification_prompt,
    build_unknown_extraction_prompt,
)


def test_each_supported_document_has_a_specific_allowlist() -> None:
    assert DOCUMENT_POLICIES["scrap_certificate"].fields == (
        "vehicle.type",
        "old_vehicle.recycle_date",
        "scrap_certificate.certificate_no",
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
    )
    # 行驶证和登记证书被报废置换与车源审核共用，未声明分区的兜底白名单是
    # 两份声明的合集；运行时按业务分区选择白名单，见下一个用例。
    assert DOCUMENT_POLICIES["vehicle_license"].fields == (
        "vehicle.type",
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
        "vehicle.registration_date",
        "vehicle.engine_no",
        "vehicle.brand_model",
        "vehicle.usage_nature",
        "vehicle.issue_date",
        "vehicle.fuel_type",
    )
    # 登记证书被报废置换、车源审核和过户审核共用，三家的白名单**叠加**在同一份
    # 策略上；各业务的分区互不重叠，所以叠加出来的分区表只会命中当前业务那一份。
    assert DOCUMENT_POLICIES["registration_certificate"].fields == (
        "vehicle.owner",
        "vehicle.vin",
        "vehicle.engine_model",
        "vehicle.type",
        "vehicle.fuel_type",
        "vehicle.registration_date",
        "registration.covered_pages",
        "vehicle.model_code",
        "vehicle.brand_model",
        "vehicle.emission_standard",
        "vehicle.power_kw",
        # 过户审核从登记证书读的字段：第 1、2 页的车牌号与车架号，
        # 第 3、4 页「转让登记」里的买家名称与证件号。
        "transfer.plate_no",
        "transfer.vin",
        "transfer.buyer_name",
        "transfer.buyer_id",
    )
    assert DOCUMENT_POLICIES["vehicle_nameplate"].fields == (
        "vehicle.vin",
        "vehicle.model_code",
        "vehicle.brand_model",
        "vehicle.power_kw",
        "vehicle.emission_standard",
    )
    assert DOCUMENT_POLICIES["invoice"].fields == (
        "invoice.invoice_no",
        "invoice.amount",
        "invoice.invoice_date",
        "new_vehicle.origin",
        "invoice.terminal_certificate_no",
        "vehicle.vin",
        "vehicle.owner",
    )
    assert DOCUMENT_POLICIES["business_license"].fields == (
        "business_license.company_name",
        "business_license.legal_representative",
        "business_license.unified_social_credit_code",
    )


def test_prompts_restrict_model_scope_and_output_contract() -> None:
    invoice_prompt = DOCUMENT_POLICIES["invoice"].build_extraction_prompt(image_index=3)

    assert "机动车销售发票" in invoice_prompt
    assert "invoice.code" in invoice_prompt
    assert "old_vehicle.vin" not in invoice_prompt
    assert '"box"' in invoice_prompt
    assert "image_index=3" in invoice_prompt

    classification_prompt = build_classification_prompt()
    assert "unsupported" in classification_prompt
    assert "不要提取业务字段" in classification_prompt
    assert "identity_card" in classification_prompt
    assert "身份证号码" in classification_prompt

    identity_prompt = DOCUMENT_POLICIES["identity_card"].build_extraction_prompt(
        image_index=4,
        business_scope="identity",
    )
    assert "identity_card.name" in identity_prompt
    assert "identity_card.side" in identity_prompt
    assert "不要读取或输出身份证号码" in identity_prompt


def test_shared_materials_keep_each_business_scope_isolated() -> None:
    """共用材料的白名单按业务分区隔离：合并声明不能把另一个业务的字段带进来。

    行驶证被报废置换与车源审核共用。登记证书和车辆铭牌是车源的辅助材料，
    只有车源分区才允许读发动机型号。
    """
    license_policy = DOCUMENT_POLICIES["vehicle_license"]

    assert license_policy.fields_for_scope("old_vehicle") == (
        "vehicle.type",
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
    )
    assert license_policy.fields_for_scope("new_vehicle") == (
        "vehicle.vin",
        "vehicle.plate_no",
        "vehicle.owner",
        "vehicle.registration_date",
    )
    assert "vehicle.engine_no" not in license_policy.fields_for_scope("old_vehicle")
    assert "vehicle.engine_no" not in license_policy.fields_for_scope("new_vehicle")

    vehicle_fields = license_policy.fields_for_scope("vehicle")
    assert "vehicle.engine_no" in vehicle_fields
    # 行驶证上只有“发动机号码”，没有“发动机型号”，也没有独立的“车辆型号”。
    assert "vehicle.engine_model" not in vehicle_fields
    assert "vehicle.model_code" not in vehicle_fields
    assert license_policy.name_for_scope("vehicle") == "机动车行驶证"

    registration_scope = DOCUMENT_POLICIES["registration_certificate"].fields_for_scope("vehicle")
    assert "vehicle.engine_model" in registration_scope
    assert "vehicle.emission_standard" in registration_scope
    # 车源审核不读轴数和排量。
    assert "vehicle.axle_count" not in registration_scope

    nameplate_scope = DOCUMENT_POLICIES["vehicle_nameplate"].fields_for_scope("vehicle")
    assert "vehicle.engine_model" in nameplate_scope
    assert "registration.covered_pages" not in nameplate_scope


def test_engine_model_is_extracted_only_from_registration_certificate() -> None:
    engine_field = "vehicle.engine_model"

    assert engine_field in DOCUMENT_POLICIES["registration_certificate"].fields
    assert all(
        engine_field not in policy.fields
        for document_type, policy in DOCUMENT_POLICIES.items()
        if document_type != "registration_certificate"
    )
    assert "registration_certificate" in build_classification_prompt()


def test_registration_certificate_prompt_fields_follow_business_scope() -> None:
    policy = DOCUMENT_POLICIES["registration_certificate"]

    old_prompt = policy.build_extraction_prompt(
        image_index=2,
        business_scope="old_vehicle",
    )
    new_prompt = policy.build_extraction_prompt(
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
    from forj_review_assistant.businesses.field_policies import field_policy

    policy = field_policy("new_vehicle.owner")
    assert policy is not None
    assert policy.allowed_document_types == ("vehicle_license", "invoice")


def test_scrap_replacement_prompts_use_scope_specific_allowlists_and_confusion_guards() -> None:
    old_license = DOCUMENT_POLICIES["vehicle_license"].build_extraction_prompt(1, "old_vehicle")
    new_license = DOCUMENT_POLICIES["vehicle_license"].build_extraction_prompt(1, "new_vehicle")
    old_registration = DOCUMENT_POLICIES["registration_certificate"].build_extraction_prompt(2, "old_vehicle")
    new_registration = DOCUMENT_POLICIES["registration_certificate"].build_extraction_prompt(2, "new_vehicle")
    invoice = DOCUMENT_POLICIES["invoice"].build_extraction_prompt(3, "new_vehicle")

    assert "发动机号码不是发动机型号" in old_license
    assert "注册日期" in new_license
    assert "vehicle.engine_model" in old_registration
    assert "vehicle.engine_model" not in new_registration
    assert "第13项‘燃料种类’" in new_registration
    assert "模型不要输出 invoice.code" in invoice
    assert "invoice.phone" not in invoice
    assert "图片可能横向" in invoice


def test_combined_prompt_restricts_registration_fields_by_business_scope() -> None:
    old_prompt = build_unknown_extraction_prompt(2, "old_vehicle")
    new_prompt = build_unknown_extraction_prompt(2, "new_vehicle")

    assert "vehicle.engine_model" in old_prompt
    assert "vehicle.engine_model" not in new_prompt


def test_combined_prompt_uses_physical_vehicle_type_and_neutral_fields() -> None:
    prompt = build_unknown_extraction_prompt(image_index=2)

    assert "vehicle_license" in prompt
    assert "vehicle.vin" in prompt
    assert "vehicle.plate_no" in prompt
    assert "vehicle.owner" in prompt
    assert '"old_vehicle"' not in prompt
    assert '"new_vehicle"' not in prompt


def test_retired_transfer_fields_are_not_in_any_material_whitelist() -> None:
    """过户业务已停用，其专有字段不得重新出现在任何材料白名单中。

    如果本测试失败，说明有停用业务的字段被重新引入。引入前必须先在
    ``docs/business-rules/`` 建立对应业务文档并明确其生产状态。
    """
    retired = {"registration.initial_owner", "registration.transfer_records"}

    for document_type, policy in DOCUMENT_POLICIES.items():
        for scope in ("old_vehicle", "new_vehicle", "transfer", "unknown"):
            leaked = retired & set(policy.fields_for_scope(scope))
            assert not leaked, (
                f"{document_type} 的 {scope} 白名单仍包含停用过户字段：{sorted(leaked)}"
            )
