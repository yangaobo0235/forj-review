import pytest

from forj_review_service.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
from forj_review_service.businesses.rules.affiliation import run_affiliation_subject
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import (
    FieldComparison,
    FieldObservation,
    FieldStatus,
    ReviewRequest,
)
from forj_review_service.runtime.workflow.capability_execution import execution_context


def EvidenceFact(field: str, value: str, source_id: str, document_type: str) -> FieldObservation:
    return FieldObservation(
        field=field,
        value=value,
        source_type="image",
        source_id=source_id,
        document_type=document_type,
    )


def comparison(field: str, value: str) -> FieldComparison:
    return FieldComparison(
        field=field,
        left_value=value,
        right_value=value,
        status=FieldStatus.MATCH,
        message="多个来源字段一致",
    )


def test_affiliation_actions_follow_confirmed_subject_types_even_if_auxiliary_customer_check_differs() -> (
    None
):
    request = ReviewRequest(
        page_url="https://example.test/scrap",
        page_fields={
            "application.owner_type": "个人",
            "page_ocr.new_vehicle_vin": "VIN-NEW",
            "application.customer_name": "李四",
        },
    )
    state = {
        "request": request,
        "profile": SCRAP_REPLACEMENT_QINGDAO,
        "batch": RecognitionBatchResult(
            observations=[
                EvidenceFact("identity_card.name", "张三", "id-front", "identity_card"),
                EvidenceFact("identity_card.side", "FRONT", "id-front", "identity_card"),
                EvidenceFact("identity_card.side", "BACK", "id-back", "identity_card"),
            ]
        ),
        "comparisons": [
            comparison("old_vehicle.owner", "张三"),
            comparison("new_vehicle.owner", "张三"),
            comparison("new_vehicle.vin", "VIN-NEW"),
        ],
        "qr_checks": [],
    }

    result = run_affiliation_subject(
        execution_context(state)
    )

    assert result.checks[0].check_id == "AFFILIATION-SUBJECT-001"
    assert result.checks[0].status == "MATCH"
    assert {item.check_id: item.status for item in result.checks[1:]} == {
        "AFFILIATION-AUX-CUSTOMER-NAME": "CONFLICT",
    }
    assert result.page_action_intents == ()


@pytest.mark.parametrize("ocr_vin", ["vin-new", "WRONG-VIN", "", None])
def test_affiliation_actions_ignore_ocr_vin_and_accept_company_labels(ocr_vin) -> None:
    request = ReviewRequest(
        page_url="https://example.test/scrap",
        page_fields={
            "application.owner_type": "公司",
            **({"page_ocr.new_vehicle_vin": ocr_vin} if ocr_vin is not None else {}),
            "application.customer_name": "甲运输有限公司",
        },
    )
    state = {
        "request": request,
        "profile": SCRAP_REPLACEMENT_QINGDAO,
        "batch": RecognitionBatchResult(
            observations=[
                EvidenceFact(
                    "business_license.company_name",
                    "甲运输有限公司",
                    "license-a",
                    "business_license",
                ),
                EvidenceFact(
                    "business_license.legal_representative", "张三", "license-a", "business_license"
                ),
            ]
        ),
        "comparisons": [
            comparison("old_vehicle.owner", "甲运输有限公司"),
            comparison("new_vehicle.owner", "甲运输有限公司"),
            comparison("new_vehicle.vin", "VIN-NEW"),
        ],
        "qr_checks": [],
    }

    result = run_affiliation_subject(
        execution_context(state)
    )

    assert [item.status for item in result.checks] == ["MATCH", "MATCH"]
    assert all("VIN" not in item.check_id for item in result.checks)
    assert result.page_action_intents == ()
