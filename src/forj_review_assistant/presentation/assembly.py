"""部分与最终响应共用的唯一建议、风险和检查组装入口。"""

from forj_review_assistant.contracts.recognition import AgentBatchResult, CheckResult
from forj_review_assistant.contracts.review import (
    FieldStatus,
    PageFillAction,
    Recommendation,
    ReviewResponse,
    ReviewTask,
)
from forj_review_assistant.evidence.comparison.check_results import unique_checks
from forj_review_assistant.presentation.advice import build_final_advice


def _legacy_risk_and_summary(
    response: ReviewResponse,
    batch: AgentBatchResult,
) -> tuple[str, str]:
    """非目标 Profile 保持分支前（0edf622）的风险级别与摘要语义。

    HIGH 只保留给同字段比较冲突和二维码官网冲突；业务规则检查结果
    只影响 recommendation 与 agent_advice，不再抬升 risk_level。
    """
    has_conflict = any(
        check.status is FieldStatus.CONFLICT for check in response.qr_checks
    ) or any(item.status is FieldStatus.CONFLICT for item in response.comparisons)
    has_review = (
        any(
            item.status is FieldStatus.REVIEW_REQUIRED
            for item in response.comparisons
        )
        or bool(response.issues)
        or bool(batch.limitations)
        or batch.failed_count > 0
        or batch.timed_out_count > 0
        or any(check.status is not FieldStatus.MATCH for check in response.qr_checks)
    )
    risk_level = "HIGH" if has_conflict else "MEDIUM" if has_review else "LOW"
    summary = (
        "发现明确字段冲突"
        if has_conflict
        else "审核辅助结果需要人工复核"
        if has_review
        else "字段检查通过"
    )
    return risk_level, summary


def assemble_review_response(
    response: ReviewResponse,
    batch: AgentBatchResult,
    *,
    business_checks: list[CheckResult],
    external_checks: list[CheckResult] | None = None,
    page_actions: list[PageFillAction] | None = None,
    review_tasks: list[ReviewTask] | None = None,
) -> ReviewResponse:
    checks = unique_checks(business_checks)
    decision, advice = build_final_advice(
        response.comparisons,
        unique_checks([*checks, *(external_checks or [])]),
        response.qr_checks,
        response.issues,
        batch.limitations,
        batch.confidences,
        completeness=batch.material_completeness,
        review_tasks=review_tasks,
    )
    if response.presentation == "FIELD_WORKBENCH":
        risk_level = (
            "LOW"
            if decision == "PASS"
            else "HIGH"
            if any(item.status == "CONFLICT" for item in advice.findings)
            else "MEDIUM"
        )
        summary = advice.summary
    else:
        risk_level, summary = _legacy_risk_and_summary(response, batch)
    return response.model_copy(
        update={
            "recommendation": Recommendation(decision),
            "risk_level": risk_level,
            "summary": summary,
            "cross_checks": checks,
            "agent_advice": advice,
            "page_fill_intent": page_actions or [],
            "review_tasks": review_tasks or [],
        }
    )
