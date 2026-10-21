"""Page anomalies remain blocking when evidence rules move out of presentation."""

import pytest

from forj_review_service.contracts.review import ReviewTask
from forj_review_service.evidence.decision import evaluate_review


@pytest.mark.parametrize("reason", ["控件未映射", "页面字段存在歧义", "系统输入异常"])
def test_unrepresented_page_anomaly_requires_review_even_without_field_conflicts(reason):
    task = ReviewTask(
        step_id="PAGE-INPUT",
        sequence=1,
        category="FIELD",
        display_target="PAGE_FIELD",
        page_field="vehicle.vin",
        label="车架号",
        result_status="INSUFFICIENT",
        reason=reason,
        requires_reviewer_action=True,
    )
    decision, findings = evaluate_review([], [], [], [], [], review_tasks=[task])
    assert decision == "REVIEW_REQUIRED"
    assert [(item.check_id, item.reason) for item in findings] == [("PAGE-INPUT", reason)]


def test_matching_page_task_does_not_invent_a_new_blocker():
    task = ReviewTask(
        step_id="PAGE-INPUT",
        sequence=1,
        category="FIELD",
        display_target="PAGE_FIELD",
        page_field="vehicle.vin",
        label="车架号",
        result_status="MATCH",
        reason="一致",
        requires_reviewer_action=False,
    )
    assert evaluate_review([], [], [], [], [], review_tasks=[task]) == ("PASS", [])
