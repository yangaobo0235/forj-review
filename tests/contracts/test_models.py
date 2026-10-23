import pytest
from pydantic import ValidationError

from forj_review_service.config.settings import load_qwen_config
from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.recognition import (
    AgentAdvice,
    CheckResult,
    MaterialCompletenessIssue,
    MaterialCompletenessReport,
    RecognitionBatchResult,
    RecognizedDocument,
    RetryAttempt,
    RetrySummary,
)
from forj_review_service.contracts.review import (
    BusinessType,
    FieldStatus,
    ImageInput,
    Recommendation,
    Region,
    ReviewRequest,
    ReviewResponse,
    SelectionMode,
)


def test_review_request_accepts_collection_diagnostics_aliases() -> None:
    request = ReviewRequest.model_validate(
        {
            "pageUrl": "https://example.test/review/1",
            "collectionDiagnostics": {
                "matchedFields": 2,
                "ambiguousFields": ["transfer.vin"],
                "scannedImages": 12,
                "selectedImages": 10,
                "imageOverflow": True,
                "collectionIssues": ["候选资料超过 10 张，请确认是否漏审"],
            },
        }
    )
    assert request.collection_diagnostics.ambiguous_fields == ["transfer.vin"]
    assert request.collection_diagnostics.image_overflow is True


def test_response_and_batch_preserve_completeness_and_retry_records() -> None:
    report = MaterialCompletenessReport(
        phase="EXTRACTED",
        status="INCOMPLETE",
        enforced=True,
        issues=[
            MaterialCompletenessIssue(
                code="MISSING_REGISTRATION_PAGES",
                missing_pages=[3, 4],
                message="机动车登记证材料不完整",
                suggested_action="请补充登记证第 3、4 页",
            )
        ],
    )
    retry = RetrySummary(
        attempts=[
            RetryAttempt(
                target_id="transfer-02",
                stage="qwen",
                attempt_number=2,
                reason_code="low_confidence",
                strategy="focused_extraction",
                result="succeeded",
                duration_ms=1200,
            )
        ]
    )
    batch = RecognitionBatchResult(
        recognized_documents=[
            RecognizedDocument(
                target_id="transfer-02",
                document_type="registration_certificate",
                business_scope="transfer",
                covered_pages=[1, 2],
            )
        ],
        material_completeness=report,
        retry_summary=retry,
    )
    assert batch.material_completeness.status == "INCOMPLETE"
    assert batch.retry_summary.qwen_retries == 1


def test_upload_presence_is_additive_and_survives_job_serialization():
    from forj_review_service.contracts.recognition import QwenExtraction
    from forj_review_service.contracts.review import ReviewJobSnapshot

    legacy = MaterialCompletenessReport(phase="EXTRACTED", status="INCOMPLETE")
    assert legacy.presence is None
    assert RecognitionBatchResult().document_classifications == []
    assert QwenExtraction(document_type="identity_card", confidence=0.9).identity_sides == []
    assert ImageInput(index=0, src="https://example.test/0.jpg").identity_sides_hint == []
    assert ImageInput(
        index=0, src="https://example.test/0.jpg", identitySidesHint=["BACK"]
    ).identity_sides_hint == ["BACK"]
    snapshot = ReviewJobSnapshot.model_validate(
        {
            "job_id": "synthetic",
            "status": "PARTIAL",
            "created_at": "synthetic",
            "progress": {"total_count": 0},
            "material_completeness": {
                "phase": "EXTRACTED",
                "status": "INCOMPLETE",
                "presence": {"status": "COMPLETE", "groups": []},
            },
        }
    )
    restored = ReviewJobSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.material_completeness.presence.status == "COMPLETE"
    assert restored.material_completeness.status == "INCOMPLETE"


def test_review_request_accepts_page_fields_and_images() -> None:
    request = ReviewRequest(
        page_url="https://example.test/review/1",
        application_id="APP-1",
        page_fields={"old_vehicle.vin": "ABC123"},
        images=[ImageInput(index=0, src="https://example.test/image.jpg", group="旧车资料")],
    )

    assert request.application_id == "APP-1"
    assert request.images[0].group == "旧车资料"


def test_image_input_keeps_asset_metadata_and_collection_error() -> None:
    image = ImageInput(
        index=0,
        src="https://example.test/image.jpg",
        image_id="img-0001",
        category_hint="scrap_certificate",
        collection_error=None,
    )

    assert image.image_id == "img-0001"
    assert image.category_hint == "scrap_certificate"
    assert image.recognition_source == "https://example.test/image.jpg"
    assert image.collection_error is None


def test_review_request_accepts_extension_camel_case_payload() -> None:
    request = ReviewRequest(
        pageUrl="https://example.test/review/1",
        pageTitle="审核页面",
        pageFields={"old_vehicle.vin": "ABC123"},
        reviewFields=[
            {
                "field": "old_vehicle.vin",
                "label": "报废车辆车架号",
                "value": "ABC123",
                "controlType": "text",
                "editable": True,
                "order": 1,
                "section": "old_vehicle",
            }
        ],
        collectionDiagnostics={"reviewFieldCount": 1},
        images=[{"index": 0, "src": "image", "pagePosition": "10,20"}],
    )

    assert str(request.page_url) == "https://example.test/review/1"
    assert request.page_title == "审核页面"
    assert request.page_fields["old_vehicle.vin"] == "ABC123"
    assert request.review_fields[0].control_type == "text"
    assert request.collection_diagnostics.review_field_count == 1
    assert request.images[0].page_position == "10,20"


def test_review_request_accepts_business_identity_from_extension() -> None:
    request = ReviewRequest(
        pageUrl="https://example.test/vehicle-source",
        businessType="vehicle_source",
        region="default",
        profileVersion="1.0",
        selectionMode="MANUAL",
        workflowStage="vehicle_source",
    )

    assert request.business_type is BusinessType.VEHICLE_SOURCE
    assert request.region is Region.DEFAULT
    assert request.profile_version == "1.0"
    assert request.selection_mode is SelectionMode.MANUAL
    assert request.workflow_stage == "vehicle_source"


def test_legacy_request_keeps_business_but_requires_an_explicit_scrap_region() -> None:
    request = ReviewRequest(page_url="https://example.test/scrap-replace-qingdao")

    assert request.business_type is BusinessType.SCRAP_REPLACEMENT
    assert request.region is Region.DEFAULT
    assert request.profile_version == "1.0"
    assert request.selection_mode is SelectionMode.AUTO


def test_image_input_requires_scope_metadata_instead_of_guessing_from_material() -> None:
    explicit = ImageInput(
        index=5,
        src="image",
        imageId="new_vehicle-02",
        businessScope="new_vehicle",
        groupTitle="新车资料",
        groupOrder=2,
        documentTypeHint="vehicle_license",
    )
    legacy = ImageInput(index=1, src="image", categoryHint="scrap_certificate")

    assert explicit.business_scope == "new_vehicle"
    assert explicit.group_title == "新车资料"
    assert explicit.group_order == 2
    assert explicit.document_type_hint == "vehicle_license"
    assert legacy.business_scope == "unknown"
    assert (
        ImageInput(index=2, src="image", categoryHint="old_vehicle").business_scope == "old_vehicle"
    )


def test_field_status_and_recommendation_are_restricted() -> None:
    response = ReviewResponse(
        recommendation=Recommendation.REVIEW_REQUIRED,
        risk_level="MEDIUM",
        summary="需要人工复核",
        comparisons=[],
        qr_checks=[],
    )

    assert response.recommendation is Recommendation.REVIEW_REQUIRED
    assert response.region is Region.DEFAULT
    assert FieldStatus.MATCH.value == "MATCH"

    with pytest.raises(ValidationError):
        ReviewResponse(
            recommendation="UNKNOWN",
            risk_level="LOW",
            summary="无",
            comparisons=[],
            qr_checks=[],
        )


def test_review_check_restricts_status_and_preserves_values() -> None:
    check = CheckResult(
        check_id="CROSS-OWNER-001",
        label="新旧车所有人一致性",
        status="CONFLICT",
        reason="旧车所有人与新车所有人不一致",
        values=[CheckResultValue(source="旧车资料", value="张三")],
    )

    assert check.values[0].value == "张三"
    with pytest.raises(ValidationError):
        check.model_copy(update={"status": "UNKNOWN"}).model_validate(
            {**check.model_dump(), "status": "UNKNOWN"}
        )


def test_review_response_defaults_cross_checks_and_advice_exposes_findings() -> None:
    finding = CheckResult(
        check_id="FIELD-old_vehicle.vin",
        label="报废车辆车架号",
        status="CONFLICT",
        reason="多个来源存在不同值",
    )
    advice = AgentAdvice(
        decision="REVIEW_REQUIRED",
        title="建议人工复核",
        summary="发现 1 项需要审核人员确认",
        findings=[finding],
        recognition_confidence=0.97,
    )
    response = ReviewResponse(
        recommendation="REVIEW_REQUIRED",
        risk_level="MEDIUM",
        summary=advice.summary,
        agent_advice=advice,
    )

    assert response.cross_checks == []
    assert response.agent_advice.findings == [finding]
    assert response.agent_advice.recognition_confidence == 0.97


def test_qwen_config_loads_dotenv_without_overriding_environment(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DASHSCOPE_API_KEY=file-key\n"
        "DASHSCOPE_MODEL=file-model\n"
        "DASHSCOPE_BASE_URL=https://file.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHSCOPE_MODEL", "system-model")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    config = load_qwen_config(env_file)

    assert config.api_key == "file-key"
    assert config.model == "system-model"
    assert config.base_url == "https://file.example/v1"
