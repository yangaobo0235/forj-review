import pytest

from forj_review_service.businesses.field_policies import field_policy
from forj_review_service.contracts.recognition import (
    RecognitionBatchResult,
    RecognizedDocument,
    RetryAttempt,
    RetrySummary,
)
from forj_review_service.contracts.review import FieldObservation, FieldStatus
from forj_review_service.evidence.comparison.aggregate import (
    aggregate_by_authority,
    aggregate_field,
)
from forj_review_service.evidence.comparison.evidence_values import batch_observations
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


def observation(
    field: str,
    value: str,
    source_id: str,
    source_type: str = "image",
) -> FieldObservation:
    return FieldObservation(
        field=field,
        source_type=source_type,
        source_id=source_id,
        value=value,
    )


def test_aggregate_preserves_evidence_and_uses_page_format_for_equivalent_values() -> None:
    observations = [
        observation("old_vehicle.vin", "ab c", "img-1"),
        observation("old_vehicle.vin", "ABC", "review_page", "page"),
    ]

    comparison = aggregate_field("old_vehicle.vin", observations, business=TEST_BUSINESS)

    assert comparison.status is FieldStatus.MATCH
    assert comparison.left_value == "ABC"
    assert comparison.right_value == "ABC"
    assert [item.detail for item in comparison.evidence] == ["img-1", "review_page"]


def test_aggregate_reports_conflict_without_overwriting_sources() -> None:
    observations = [
        observation("new_vehicle.owner", "张三", "img-4"),
        observation("new_vehicle.owner", "李四", "img-5"),
        observation("new_vehicle.owner", "张三", "review_page", "page"),
    ]

    comparison = aggregate_field("new_vehicle.owner", observations, business=TEST_BUSINESS)

    assert comparison.status is FieldStatus.CONFLICT
    assert len(comparison.evidence) == 3
    assert [(item.detail, item.value) for item in comparison.evidence] == [
        ("img-4", "张三"),
        ("img-5", "李四"),
        ("review_page", "张三"),
    ]
    assert "不同值" in comparison.message


def test_aggregate_marks_only_the_minority_value_as_conflicting() -> None:
    comparison = aggregate_field(
        "new_vehicle.vin",
        [
            observation("new_vehicle.vin", "VIN-A", "img-1"),
            observation("new_vehicle.vin", "VIN-A", "img-2"),
            observation("new_vehicle.vin", "VIN-B", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.CONFLICT
    assert [item.conflicting for item in comparison.evidence] == [False, False, True]


def test_aggregate_gives_verified_qr_value_authority_over_a_majority() -> None:
    comparison = aggregate_field(
        "old_vehicle.vin",
        [
            observation("old_vehicle.vin", "VIN-IMAGE", "img-1"),
            observation("old_vehicle.vin", "VIN-IMAGE", "review_page", "page"),
            observation("old_vehicle.vin", "VIN-OFFICIAL", "qr-1", "qr_page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.CONFLICT
    assert [item.conflicting for item in comparison.evidence] == [True, True, False]


def test_aggregate_marks_every_value_when_official_values_conflict() -> None:
    comparison = aggregate_field(
        "old_vehicle.vin",
        [
            observation("old_vehicle.vin", "VIN-A", "qr-1", "qr_page"),
            observation("old_vehicle.vin", "VIN-B", "qr-2", "qr_page"),
            observation("old_vehicle.vin", "VIN-A", "img-1"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.CONFLICT
    assert [item.conflicting for item in comparison.evidence] == [True, True, True]


def test_aggregate_marks_every_value_when_non_official_votes_tie() -> None:
    comparison = aggregate_field(
        "new_vehicle.vin",
        [
            observation("new_vehicle.vin", "VIN-A", "img-1"),
            observation("new_vehicle.vin", "VIN-B", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.CONFLICT
    assert [item.conflicting for item in comparison.evidence] == [True, True]


def test_aggregate_does_not_mark_normalized_equivalent_values() -> None:
    comparison = aggregate_field(
        "old_vehicle.vin",
        [
            observation("old_vehicle.vin", "vin a", "img-1"),
            observation("old_vehicle.vin", "VINA", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.MATCH
    assert [item.conflicting for item in comparison.evidence] == [False, False]


def test_invoice_code_matches_the_same_numeric_invoice_number() -> None:
    comparison = aggregate_field(
        "invoice.code",
        [
            observation("invoice.code", "26320000000801433801", "invoice-1"),
            observation("invoice.code", "26320000000801433801", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.MATCH


def test_invoice_number_ignores_label_and_hidden_formatting_noise() -> None:
    comparison = aggregate_field(
        "invoice.invoice_no",
        [
            observation("invoice.invoice_no", "数电号码：2632 0000 0008 0143 3801", "invoice-1"),
            observation("invoice.invoice_no", "26320000000801433801", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.MATCH


def test_aggregate_owner_display_removes_appended_social_credit_code() -> None:
    comparison = aggregate_field(
        "old_vehicle.owner",
        [
            observation(
                "old_vehicle.owner",
                "丹阳市双跃危险货物运输有限公司/统一社会信用代码/91321181730111164",
                "old_vehicle-02",
            ),
            observation("old_vehicle.owner", "丹阳市双跃危险品运输有限公司", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.left_value == "丹阳市双跃危险货物运输有限公司"
    assert comparison.right_value == "丹阳市双跃危险品运输有限公司"
    assert comparison.status is FieldStatus.CONFLICT


def test_aggregate_requires_review_for_one_source() -> None:
    comparison = aggregate_field(
        "invoice.code",
        [observation("invoice.code", "123", "review_page", "page")],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert comparison.message == "页面字段已采集，但未从材料中取得可核验值"
    assert [item.conflicting for item in comparison.evidence] == [False]


def test_uncertain_evidence_requires_review() -> None:
    """页面与不确定材料相同，也不能证明字段一致。"""
    observations = [
        FieldObservation(
            field="new_vehicle.vin",
            source_type="image",
            source_id="img-1",
            value="VIN-1",
            uncertain=True,
        ),
        observation("new_vehicle.vin", "VIN-1", "review_page", "page"),
    ]

    target = aggregate_field("new_vehicle.vin", observations, business=TEST_BUSINESS)
    assert target.status is FieldStatus.REVIEW_REQUIRED
    assert target.reason_codes == ["RECOGNITION_UNCERTAIN"]
    assert target.message == "图片识别结果不确定，请核对原图"


def test_aggregate_requires_review_when_all_sources_are_empty() -> None:
    comparison = aggregate_field(
        "invoice.code",
        [observation("invoice.code", " ", "review_page", "page")],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert comparison.message == "页面与图片均未取得有效值"
    assert comparison.evidence == []


def test_aggregate_equivalent_date_uses_page_format_for_display() -> None:
    comparison = aggregate_field(
        "invoice.invoice_date",
        [
            observation("invoice.invoice_date", "2026年07月29日", "img-1"),
            observation("invoice.invoice_date", "2026-07-29", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.MATCH
    assert comparison.left_value == "2026-07-29"
    assert comparison.right_value == "2026-07-29"


def test_aggregate_preserves_structured_image_identity() -> None:
    item = FieldObservation(
        field="new_vehicle.vin",
        source_type="image",
        source_id="new_vehicle-02",
        value="NEW-VIN",
        document_type="vehicle_license",
        image_index=6,
        image_id="new_vehicle-02",
        business_scope="new_vehicle",
        group_title="新车资料",
        group_order=2,
    )

    comparison = aggregate_field("new_vehicle.vin", [item], business=TEST_BUSINESS)
    EvidenceFact = comparison.evidence[0]

    assert EvidenceFact.image_id == "new_vehicle-02"
    assert EvidenceFact.business_scope == "new_vehicle"
    assert EvidenceFact.group_title == "新车资料"
    assert EvidenceFact.group_order == 2
    assert EvidenceFact.document_type == "vehicle_license"


def test_aggregate_distinguishes_application_page_and_qr_official_evidence() -> None:
    comparison = aggregate_field(
        "old_vehicle.vin",
        [
            observation("old_vehicle.vin", "VIN-1", "review_page", "page"),
            observation("old_vehicle.vin", "VIN-1", "qr-2", "qr_page"),
        ],
        business=TEST_BUSINESS,
    )

    assert [item.source for item in comparison.evidence] == [
        "申请页面字段",
        "二维码官网字段",
    ]


def vehicle_vin_observation(
    value: str, source_id: str, source_type: str, document_type: str | None = None
) -> FieldObservation:
    return FieldObservation(
        field="old_vehicle.vin",
        value=value,
        source_id=source_id,
        source_type=source_type,
        document_type=document_type,
    )


def test_old_vehicle_vin_uses_qr_as_authority_and_only_document_suffixes() -> None:
    comparison = vin_by_authority(
        [
            vehicle_vin_observation("VIN-0000ABCDEF12", "page", "page"),
            vehicle_vin_observation("VIN-0000ABCDEF12", "qr", "qr_page"),
            vehicle_vin_observation("OTHER-9999ABCDEF12", "license", "image", "vehicle_license"),
            vehicle_vin_observation(
                "REG-8888ABCDEF12", "registration", "image", "registration_certificate"
            ),
            vehicle_vin_observation("WRONG-000000999999", "scrap", "image", "scrap_certificate"),
        ]
    )

    assert comparison.status is FieldStatus.MATCH
    assert "后 8 位一致" in comparison.message
    assert all(item.source_id != "scrap" or not item.conflicting for item in comparison.evidence)
    assert all(not item.differences for item in comparison.evidence)


def test_old_vehicle_vin_rejects_page_or_document_suffix_conflicts() -> None:
    page_conflict = vin_by_authority(
        [
            vehicle_vin_observation("PAGE-WRONG-ABCDEF", "page", "page"),
            vehicle_vin_observation("VIN-000000ABCDEF", "qr", "qr_page"),
        ]
    )
    suffix_conflict = vin_by_authority(
        [
            vehicle_vin_observation("VIN-000000ABCDEF", "page", "page"),
            vehicle_vin_observation("VIN-000000ABCDEF", "qr", "qr_page"),
            vehicle_vin_observation("DOC-000000999999", "license", "image", "vehicle_license"),
        ]
    )

    assert page_conflict.status is FieldStatus.CONFLICT
    assert suffix_conflict.status is FieldStatus.CONFLICT
    page_evidence = next(item for item in page_conflict.evidence if item.source_id == "page")
    qr_evidence = next(item for item in page_conflict.evidence if item.source_id == "qr")
    suffix_evidence = next(item for item in suffix_conflict.evidence if item.source_id == "license")
    assert page_evidence.conflicting is True
    assert page_evidence.differences == []
    assert qr_evidence.differences
    assert suffix_evidence.conflicting is True
    assert suffix_evidence.differences
    assert all(item.start >= len("DOC-000000999999") - 8 for item in suffix_evidence.differences)


def test_old_vehicle_vin_suffix_differences_preserve_matching_document_prefixes() -> None:
    comparison = vin_by_authority(
        [
            vehicle_vin_observation("LFWSRXSJ7G1E22467", "page", "page"),
            vehicle_vin_observation("LFWSRXSJ7G1E22467", "qr", "qr_page"),
            vehicle_vin_observation(
                "CA4250P66K24T1A1E4", "registration", "image", "registration_certificate"
            ),
            vehicle_vin_observation("OTHER-99997G1E22467", "license", "image", "vehicle_license"),
        ]
    )

    registration = next(item for item in comparison.evidence if item.source_id == "registration")
    license_evidence = next(item for item in comparison.evidence if item.source_id == "license")
    assert comparison.status is FieldStatus.CONFLICT
    assert registration.conflicting is True
    assert registration.differences
    assert all(item.start >= len("CA4250P66K24T1A1E4") - 8 for item in registration.differences)
    assert license_evidence.conflicting is False
    assert license_evidence.differences == []


def test_old_vehicle_vin_marks_material_values_that_differ_from_the_page() -> None:
    comparison = vin_by_authority(
        [
            vehicle_vin_observation("LFNAFRJM6BAK00025", "page", "page"),
            vehicle_vin_observation("LFNAFRJM6BAK00024", "qr", "qr_page"),
            vehicle_vin_observation("LFNAFRJM6BAK00024", "license", "image", "vehicle_license"),
            vehicle_vin_observation(
                "LFNAFRJM6BAK00024", "registration", "image", "registration_certificate"
            ),
        ]
    )

    page = next(item for item in comparison.evidence if item.source_id == "page")
    materials = [
        item for item in comparison.evidence if item.source_id in {"qr", "license", "registration"}
    ]
    assert comparison.status is FieldStatus.CONFLICT
    assert page.differences == []
    assert len(materials) == 3
    assert all(item.differences for item in materials)
    assert all(
        difference.start == len(str(item.value)) - 1
        for item in materials
        for difference in item.differences
    )


def test_old_vehicle_vin_requires_one_unique_qr_value() -> None:
    missing = vin_by_authority(
        [
            vehicle_vin_observation("VIN-000000ABCDEF", "page", "page"),
        ]
    )
    multiple = vin_by_authority(
        [
            vehicle_vin_observation("VIN-000000ABCDEF", "qr-1", "qr_page"),
            vehicle_vin_observation("VIN-000000999999", "qr-2", "qr_page"),
        ]
    )

    assert missing.status is FieldStatus.REVIEW_REQUIRED
    assert multiple.status is FieldStatus.CONFLICT


# 以下断言原先针对已删除的 compare.compare_values（二元比对）。
# 生产者入口是 aggregate_field（多源聚合），断言随之迁移到这里。


def test_aggregate_exposes_character_difference_positions() -> None:
    """字符级差异位置由后端计算，供工作台标红使用。"""
    comparison = aggregate_field(
        "new_vehicle.vin",
        [
            observation("new_vehicle.vin", "ABC123", "img-1"),
            observation("new_vehicle.vin", "ABC223", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.CONFLICT
    assert comparison.differences == [3]


def test_aggregate_marks_missing_tail_as_different() -> None:
    comparison = aggregate_field(
        "scrap_certificate.certificate_no",
        [
            observation("scrap_certificate.certificate_no", "AB-123", "img-1"),
            observation("scrap_certificate.certificate_no", "AB-12", "review_page", "page"),
        ],
        business=TEST_BUSINESS,
    )

    assert comparison.differences


def test_aggregate_requires_review_when_only_image_evidence_exists() -> None:
    """页面字段缺失但材料有值：单证据默认需要人工确认。"""
    comparison = aggregate_field(
        "old_vehicle.owner",
        [observation("old_vehicle.owner", "李四", "img-1")],
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert comparison.message == "仅有一个有效来源，证据不足"


def test_aggregate_accepts_single_image_evidence_when_policy_allows() -> None:
    comparison = aggregate_field(
        "old_vehicle.owner",
        [observation("old_vehicle.owner", "李四", "img-1")],
        single_evidence_requires_review=False,
        business=TEST_BUSINESS,
    )

    assert comparison.status is FieldStatus.MATCH
    assert comparison.message == "已发现 1 份有效证据，按实际证据核验"


# --- 声明式权威链 -------------------------------------------------------
#
# `old_vehicle.vin` 原先由一个约 130 行的专用函数裁决，现已改为在
# `field_evidence_policies` 中声明权威链。下面这组用例锁定声明式实现的行为。

VIN_AUTHORITY = field_policy("old_vehicle.vin", business=TEST_BUSINESS).authority


def vin_by_authority(observations: list[FieldObservation]):
    return aggregate_by_authority(
        "old_vehicle.vin",
        observations,
        VIN_AUTHORITY,
        business=TEST_BUSINESS,
    )


def _vin_cases() -> list[list[FieldObservation]]:
    page = "LFWSRXSJ7G1E22467"
    return [
        # 全部一致（页面全串 + 两证后 8 位）
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation(page, "qr", "qr_page"),
            vehicle_vin_observation("OTHER-999" + page[-8:], "license", "image", "vehicle_license"),
            vehicle_vin_observation(
                "REG-888" + page[-8:], "registration", "image", "registration_certificate"
            ),
        ],
        # 报废证明的 OCR 车架号不在权威链上，必须被排除
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation(page, "qr", "qr_page"),
            vehicle_vin_observation("WRONG-000000999999", "scrap", "image", "scrap_certificate"),
        ],
        # 页面与官网不一致
        [
            vehicle_vin_observation("PAGE-WRONG-ABCDEF", "page", "page"),
            vehicle_vin_observation("VIN-000000ABCDEF", "qr", "qr_page"),
        ],
        # 证件后 8 位与官网不一致
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation(page, "qr", "qr_page"),
            vehicle_vin_observation("DOC-000000999999", "license", "image", "vehicle_license"),
        ],
        # 证件前缀不同但后 8 位一致 → 不算冲突
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation(page, "qr", "qr_page"),
            vehicle_vin_observation(
                "CA4250P66K24T1A1E4", "registration", "image", "registration_certificate"
            ),
        ],
        # 缺二维码官网值
        [vehicle_vin_observation(page, "page", "page")],
        # 官网返回多个不同值
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation("VIN-000000ABCDEF", "qr-1", "qr_page"),
            vehicle_vin_observation("VIN-000000999999", "qr-2", "qr_page"),
        ],
        # 只有官网，缺页面值
        [vehicle_vin_observation(page, "qr", "qr_page")],
        # 官网与证件都不覆盖该字段的未知材料
        [
            vehicle_vin_observation(page, "page", "page"),
            vehicle_vin_observation(page, "qr", "qr_page"),
            vehicle_vin_observation(page, "other", "image", "business_license"),
        ],
    ]


# 每个场景的期望状态与逐条证据的冲突标记。这些值取自专用函数被删除前的
# 实际输出，因此这条用例是切换前后的行为等价快照。
VIN_BEHAVIOUR = [
    ("MATCH", [("page", False), ("qr", False), ("license", False), ("registration", False)]),
    ("MATCH", [("page", False), ("qr", False)]),
    ("CONFLICT", [("page", True), ("qr", False)]),
    ("CONFLICT", [("page", False), ("qr", False), ("license", True)]),
    ("CONFLICT", [("page", False), ("qr", False), ("registration", True)]),
    ("REVIEW_REQUIRED", [("page", False)]),
    ("CONFLICT", [("page", False), ("qr-1", True), ("qr-2", True)]),
    ("REVIEW_REQUIRED", [("qr", False)]),
    ("MATCH", [("page", False), ("qr", False)]),
]


def test_declared_vin_authority_behaviour_snapshot() -> None:
    for index, (observations, expected) in enumerate(zip(_vin_cases(), VIN_BEHAVIOUR, strict=True)):
        comparison = vin_by_authority(list(observations))
        status, flags = expected

        assert comparison.status.value == status, f"case {index}: 状态不一致"
        assert [(item.source_id, item.conflicting) for item in comparison.evidence] == flags, (
            f"case {index}: 证据冲突标记不一致"
        )


def test_authority_aggregator_requires_a_declared_chain() -> None:
    with pytest.raises(ValueError, match="未声明权威链"):
        aggregate_by_authority("old_vehicle.vin", [], (), business=TEST_BUSINESS)


# --- 重读状态：区分"从未重读"和"已重读仍不确定" --------------------------


def test_uncertain_message_distinguishes_retried_evidence() -> None:
    uncertain_item = FieldObservation(
        field="old_vehicle.vin",
        source_type="image",
        source_id="img-1",
        value="VIN-1",
        uncertain=True,
    )
    page = observation("old_vehicle.vin", "VIN-1", "review_page", "page")

    fresh = aggregate_field(
        "old_vehicle.vin",
        [uncertain_item, page],
        business=TEST_BUSINESS,
    )
    retried = aggregate_field(
        "old_vehicle.vin",
        [uncertain_item.model_copy(update={"retried": True}), page],
        business=TEST_BUSINESS,
    )

    assert fresh.message == "图片识别结果不确定，请核对原图"
    assert retried.message == "图片识别不确定，已重读仍无法确认，请核对原图"


def test_batch_observations_marks_evidence_from_a_retried_image() -> None:
    batch = RecognitionBatchResult(
        observations=[
            FieldObservation(
                field="old_vehicle.vin",
                source_type="image",
                source_id="old_vehicle-01",
                image_id="old_vehicle-01",
                value="VIN-1",
                uncertain=True,
            )
        ],
        recognized_documents=[
            RecognizedDocument(
                target_id="old_vehicle-01",
                document_type="vehicle_license",
                business_scope="old_vehicle",
            )
        ],
        retry_summary=RetrySummary(
            attempts=[
                RetryAttempt(
                    target_id="old_vehicle-01",
                    stage="qwen",
                    attempt_number=2,
                    reason_code="uncertain_field:vehicle.vin",
                    strategy="focused_extraction",
                    result="succeeded",
                    duration_ms=12,
                )
            ]
        ),
    )

    resolved = batch_observations(batch, business=TEST_BUSINESS)[0]

    assert resolved.uncertain is True
    assert resolved.retried is True


def test_batch_observations_does_not_mark_untouched_images_as_retried() -> None:
    batch = RecognitionBatchResult(
        observations=[
            FieldObservation(
                field="old_vehicle.vin",
                source_type="image",
                source_id="old_vehicle-01",
                value="VIN-1",
                uncertain=True,
            )
        ],
        recognized_documents=[
            RecognizedDocument(target_id="old_vehicle-01", document_type="vehicle_license")
        ],
    )

    assert batch_observations(batch, business=TEST_BUSINESS)[0].retried is False


def test_one_material_read_from_several_images_stays_a_single_evidence_source() -> None:
    """一份材料的多次读取合并成一条证据。

    行驶证正反面、登记证书第 1、2 页与第 3、4 页，以及用户重复上传的同一张图，
    都是**同一份材料**的多次读取。它们不是互相独立的来源，审核员不应该在
    材料提取值里看到两条一模一样的“行驶证”。
    """
    observations = [
        FieldObservation(
            field="old_vehicle.type",
            source_type="image",
            source_id="license-front",
            image_index=1,
            image_id="license-front",
            value="重型半挂牵引车",
            document_type="vehicle_license",
            business_scope="old_vehicle",
        ),
        FieldObservation(
            field="old_vehicle.type",
            source_type="image",
            source_id="license-back",
            image_index=2,
            image_id="license-back",
            value="重型半挂牵引车",
            document_type="vehicle_license",
            business_scope="old_vehicle",
        ),
        FieldObservation(
            field="old_vehicle.type",
            source_type="image",
            source_id="registration",
            image_index=3,
            image_id="registration",
            value="重型半挂牵引车",
            document_type="registration_certificate",
            business_scope="old_vehicle",
        ),
        FieldObservation(
            field="old_vehicle.type",
            source_type="page",
            source_id="review_page",
            value="牵引车",
        ),
    ]

    comparison = aggregate_field("old_vehicle.type", observations, business=TEST_BUSINESS)

    # 两类材料 + 页面 = 三条证据；行驶证的两张图合并成一条。
    assert [(item.document_type, item.value) for item in comparison.evidence] == [
        ("vehicle_license", "重型半挂牵引车"),
        ("registration_certificate", "重型半挂牵引车"),
        (None, "牵引车"),
    ]
    assert comparison.status is FieldStatus.MATCH


def test_material_merge_preserves_conflicting_reads_even_when_one_matches_the_page() -> None:
    """页面值不是挑选证据的依据，不同读值都必须留给审核员。"""
    observations = [
        FieldObservation(
            field="vehicle.brand_model",
            source_type="image",
            source_id="license-a",
            image_index=1,
            image_id="license-a",
            value="解放牌CA4250P66K25T1A1E",
            document_type="vehicle_license",
            business_scope="old_vehicle",
        ),
        FieldObservation(
            field="vehicle.brand_model",
            source_type="image",
            source_id="license-b",
            image_index=2,
            image_id="license-b",
            value="解放牌CA4250P66M25T1A1E6",
            document_type="vehicle_license",
            business_scope="old_vehicle",
        ),
        FieldObservation(
            field="vehicle.brand_model",
            source_type="page",
            source_id="review_page",
            value="解放牌CA4250P66M25T1A1E6",
        ),
    ]

    comparison = aggregate_field("vehicle.brand_model", observations, business=TEST_BUSINESS)

    material = [item for item in comparison.evidence if item.document_type == "vehicle_license"]
    assert [item.value for item in material] == [
        "解放牌CA4250P66K25T1A1E",
        "解放牌CA4250P66M25T1A1E6",
    ]
    assert comparison.status is FieldStatus.CONFLICT


@pytest.mark.parametrize("uncertain_value", ["VIN-A", "VIN-B", "无法识别"])
def test_uncertain_only_difference_is_review_not_conflict(uncertain_value):
    items = [
        observation("new_vehicle.vin", "VIN-A", "page", "page"),
        observation("new_vehicle.vin", uncertain_value, "img").model_copy(
            update={"uncertain": True}
        ),
    ]
    result = aggregate_field("new_vehicle.vin", items, business=TEST_BUSINESS)
    assert result.status is FieldStatus.REVIEW_REQUIRED
    assert result.evidence[1].comparison_status == "INSUFFICIENT"
    assert not result.evidence[1].conflicting


def test_confirmed_conflict_survives_an_unrelated_uncertain_source():
    result = aggregate_field(
        "new_vehicle.vin",
        [
            observation("new_vehicle.vin", "VIN-A", "page", "page"),
            observation("new_vehicle.vin", "VIN-B", "img1"),
            observation("new_vehicle.vin", "VIN-C", "img2").model_copy(update={"uncertain": True}),
        ],
        required_document_types=("missing_document",),
        business=TEST_BUSINESS,
    )
    assert result.status is FieldStatus.CONFLICT
    assert result.reason_codes == ["VALUE_MISMATCH", "RECOGNITION_UNCERTAIN"]
    assert result.evidence[1].comparison_status == "CONFLICT"
    assert result.evidence[2].comparison_status == "INSUFFICIENT"


def test_unreadable_placeholder_is_not_a_conflicting_value():
    result = aggregate_field(
        "new_vehicle.vin",
        [
            observation("new_vehicle.vin", "VIN-A", "page", "page"),
            observation("new_vehicle.vin", "无法识别", "img"),
        ],
        business=TEST_BUSINESS,
    )
    assert result.status is FieldStatus.REVIEW_REQUIRED
    assert result.reason_codes == ["RECOGNITION_UNCERTAIN"]


def test_authority_ignores_uncertain_head_when_proving_a_conflict():
    page = vehicle_vin_observation("VIN-000000ABCDEF", "page", "page")
    reliable = vehicle_vin_observation("VIN-000000ABCDEF", "qr1", "qr_page")
    uncertain = vehicle_vin_observation("VIN-000000999999", "qr2", "qr_page").model_copy(
        update={"uncertain": True}
    )
    result = vin_by_authority([page, reliable, uncertain])
    assert result.status is FieldStatus.REVIEW_REQUIRED
    assert not any(item.conflicting for item in result.evidence)
    assert result.evidence[-1].comparison_status == "INSUFFICIENT"
    result = vin_by_authority(
        [page.model_copy(update={"value": "PAGE-WRONG"}), reliable, uncertain]
    )
    assert result.status is FieldStatus.CONFLICT
    assert result.evidence[-1].comparison_status == "INSUFFICIENT"


def test_missing_authority_never_creates_a_confirmed_material_conflict():
    result = vin_by_authority(
        [
            vehicle_vin_observation("VIN-000000ABCDEF", "page", "page"),
            vehicle_vin_observation("VIN-000000999999", "img", "image", "vehicle_license"),
        ]
    )
    assert result.status is FieldStatus.REVIEW_REQUIRED
    assert result.reason_codes == ["AUTHORITY_MISSING"]
    assert all(not item.conflicting for item in result.evidence)
    assert result.evidence[-1].comparison_status == "INSUFFICIENT"
