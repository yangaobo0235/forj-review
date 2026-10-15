"""合成 HS/JS 编号覆盖截图中的错配和缺少页面值误判。"""

import pytest

from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    FieldStatus,
    ReviewRequest,
)
from forj_review_assistant.presentation.routing import build_review_tasks
from forj_review_assistant.runtime.review_service import ReviewService

FIELD = "scrap_certificate.certificate_no"
PAGE_NUMBER = "HS-SYNTHETIC-2026-001"


def response(page_value, material_value, *, mapped=True):
    request = ReviewRequest(
        page_url="https://example.test/review", region="changchun",
        page_fields={FIELD: page_value} if mapped and page_value is not None else {},
        review_fields=[{
            "field": FIELD if mapped else None, "label": "报废证明编号", "value": page_value or "",
            "control_type": "text", "editable": True, "order": 1, "section": "old_vehicle",
        }],
    )
    batch = AgentBatchResult(observations=[FieldObservation(
        field=FIELD, value=material_value, source_type="image", source_id="synthetic-proof",
        image_id="synthetic-proof", document_type="scrap_certificate", business_scope="old_vehicle",
    )])
    service = ReviewService()
    profile = service.resolve_profile(request)
    result = service._build_response(request, batch, profile=profile)
    result.review_tasks = build_review_tasks(
        request=request, profile=profile, comparisons=result.comparisons,
        external_checks=[], business_checks=[], completeness=None, limitations=[],
    )
    return result


def test_hs_page_and_js_material_are_a_conflict_in_the_operator_task():
    result = response(PAGE_NUMBER, PAGE_NUMBER.replace("HS-", "JS-"))
    comparison = next(item for item in result.comparisons if item.field == FIELD)
    assert comparison.status is FieldStatus.CONFLICT
    step = next(item for item in result.review_tasks if item.page_target_field == FIELD)
    assert step.result_status == "CONFLICT"
    assert any(item.value == PAGE_NUMBER.replace("HS-", "JS-") for item in step.values)
    assert "未配置" not in step.reason


@pytest.mark.parametrize("page_value", [None, "", "   "])
def test_material_evidence_cannot_pass_when_the_page_value_is_missing(page_value):
    result = response(page_value, PAGE_NUMBER)
    comparison = next(item for item in result.comparisons if item.field == FIELD)
    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert "页面字段未采集或未填写" in comparison.message


def test_unmapped_configured_control_does_not_claim_missing_configuration():
    result = response(PAGE_NUMBER, PAGE_NUMBER, mapped=False)
    step = next(item for item in result.review_tasks if item.label == "报废证明编号")
    assert step.result_status == "INSUFFICIENT"
    assert "重新采集" in step.reason
    assert "未配置" not in step.reason
    assert next(item for item in result.comparisons if item.field == FIELD).status is FieldStatus.REVIEW_REQUIRED


def test_equal_page_and_material_values_still_pass():
    result = response(PAGE_NUMBER, PAGE_NUMBER)
    assert next(item for item in result.comparisons if item.field == FIELD).status is FieldStatus.MATCH
    assert next(item for item in result.review_tasks if item.page_target_field == FIELD).result_status == "MATCH"
