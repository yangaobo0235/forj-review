
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    ReviewRequest,
)
from forj_review_assistant.runtime.review_service import ReviewService


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
