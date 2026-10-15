
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    ImageInput,
    ReviewRequest,
)
from forj_review_assistant.runtime.review_service import ReviewService


def transfer_batch() -> AgentBatchResult:
    return AgentBatchResult(
        observations=[
            FieldObservation(
                field="transfer.plate_no",
                source_type="image",
                source_id="invoice",
                value="冀A34870",
                document_type="invoice",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.vin",
                source_type="image",
                source_id="invoice",
                value="VIN-1",
                document_type="invoice",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.vin",
                source_type="image",
                source_id="reg",
                value="VIN-1",
                document_type="registration_certificate",
                business_scope="transfer",
                group_order=2,
            ),
            FieldObservation(
                field="transfer.buyer_name",
                source_type="image",
                source_id="invoice",
                value="丙公司",
                document_type="invoice",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.seller_name",
                source_type="image",
                source_id="invoice",
                value="乙公司",
                document_type="invoice",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.invoice_date",
                source_type="image",
                source_id="invoice",
                value="2026-08-15",
                document_type="invoice",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.registration.covered_pages",
                source_type="image",
                source_id="reg",
                value=[1, 2, 3, 4],
                document_type="registration_certificate",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.registration.initial_owner",
                source_type="image",
                source_id="reg",
                value="甲公司",
                document_type="registration_certificate",
                business_scope="transfer",
            ),
            FieldObservation(
                field="transfer.registration.transfer_records",
                source_type="image",
                source_id="reg",
                value=[
                    {"owner": "乙公司", "date": "2024-01-01", "page": 2, "order": 1},
                    {"owner": "丙公司", "date": "2026-08-16", "page": 4, "order": 2},
                ],
                document_type="registration_certificate",
                business_scope="transfer",
            ),
        ],
        confidences=[0.95, 0.96],
    )


def transfer_request(page_fields: dict[str, str] | None = None) -> ReviewRequest:
    return ReviewRequest(
        page_url="https://example.test/transfer/1",
        business_type="transfer",
        region="default",
        page_fields=page_fields or {},
        images=[
            ImageInput(
                index=0,
                src="https://images.example.test/synthetic.jpg",
                business_scope="transfer",
                document_type_hint="invoice",
                group_title="过户资料",
                group_order=1,
            ),
            ImageInput(
                index=1,
                src="https://images.example.test/synthetic.jpg",
                business_scope="transfer",
                document_type_hint="registration_certificate",
                group_title="过户资料",
                group_order=2,
            ),
        ],
    )


def test_scrap_target_profile_uncertain_observation_forces_review(client) -> None:
    """目标 Profile 保留一票否决：图片 uncertain 时同字段比较强制人工复核。"""
    batch = AgentBatchResult(
        observations=[
            FieldObservation(
                field="new_vehicle.vin",
                source_type="image",
                source_id="new",
                value="VIN-1",
                uncertain=True,
            )
        ]
    )
    result = ReviewService()._build_response(
        ReviewRequest(
            page_url="https://example.test/review/1",
            business_type="scrap_replacement",
            region="qingdao",
            page_fields={"new_vehicle.vin": "VIN-1"},
        ),
        batch,
    )

    vin = next(item for item in result.comparisons if item.field == "new_vehicle.vin")
    assert vin.status.value == "REVIEW_REQUIRED"
    assert vin.message == "图片识别结果不确定，请核对原图"


def test_scrap_request_uses_scrap_fields_and_sections(client) -> None:
    result = ReviewService().assist(
        ReviewRequest(
            page_url="https://example.test/scrap-replace-qingdao",
            business_type="scrap_replacement",
            region="qingdao",
            page_fields={"old_vehicle.vin": "VIN-1"},
            images=[],
        )
    )

    assert any(item.field == "old_vehicle.vin" for item in result.comparisons)
    assert [item.id for item in result.sections] == ["old_vehicle", "new_vehicle"]


def test_vehicle_source_keeps_scrap_fields_out_of_its_own_review(client) -> None:
    """车源审核只核对自己的 13 个字段，页面上的报废置换字段必须被完全忽略。"""
    result = ReviewService().assist(
        ReviewRequest(
            page_url="https://example.test/vehicle-source",
            business_type="vehicle_source",
            region="default",
            workflow_stage="vehicle_source",
            page_fields={"old_vehicle.vin": "MUST-NOT-BE-COMPARED"},
            images=[],
        )
    )

    fields = {item.field for item in result.comparisons}
    assert fields == {
        "vehicle.type",
        "vehicle.plate_no",
        "vehicle.vin",
        "vehicle.engine_no",
        "vehicle.license_vehicle_type",
        "vehicle.brand_model",
        "vehicle.usage_nature",
        "vehicle.registration_date",
        "vehicle.issue_date",
        "vehicle.owner",
        "vehicle.fuel_type",
        "vehicle.engine_model",
    }
    assert not any(field.startswith("old_vehicle") for field in fields)
    assert [item.id for item in result.sections] == ["vehicle"]
    assert result.business_type.value == "vehicle_source"


def test_unknown_profile_version_returns_422(client) -> None:
    response = client.post(
        "/api/review/assist",
        json={
            "pageUrl": "https://example.test/vehicle-source",
            "businessType": "vehicle_source",
            "region": "default",
            "profileVersion": "9.9",
            "images": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "审核业务配置不存在或版本不兼容"


def test_unknown_profile_version_is_rejected_before_job_creation(client) -> None:
    response = client.post(
        "/api/review/jobs",
        json={
            "pageUrl": "https://example.test/vehicle-source",
            "businessType": "vehicle_source",
            "region": "default",
            "profileVersion": "9.9",
            "images": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "审核业务配置不存在或版本不兼容"
