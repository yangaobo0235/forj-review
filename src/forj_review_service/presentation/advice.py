"""Render deterministic review decisions as human-readable advice."""

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.contracts.recognition import (
    AgentAdvice,
    CheckResult,
    MaterialCompletenessReport,
)
from forj_review_service.contracts.review import FieldComparison, QrCheck, ReviewTask
from forj_review_service.evidence.decision import evaluate_review


def build_final_advice(
    comparisons: list[FieldComparison],
    cross_checks: list[CheckResult],
    qr_checks: list[QrCheck],
    issues: list[str],
    limitations: list[str],
    confidences: list[float],
    *,
    completeness: MaterialCompletenessReport | None = None,
    review_tasks: list[ReviewTask] | None = None,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> tuple[str, AgentAdvice]:
    decision, findings = evaluate_review(
        comparisons,
        cross_checks,
        qr_checks,
        issues,
        limitations,
        completeness=completeness,
        review_tasks=review_tasks,
        business=business,
    )
    confidence = sum(confidences) / len(confidences) if confidences else None
    basis = {"确定性审核规则"}
    if any(
        item.source == "申请页面字段" for comparison in comparisons for item in comparison.evidence
    ):
        basis.add("页面申请信息")
    if any(item.source == "图片识别" for comparison in comparisons for item in comparison.evidence):
        basis.add("资料图片识别")
    if qr_checks:
        basis.add("二维码官网核验")
    if any(item.source == "能力核验" for comparison in comparisons for item in comparison.evidence):
        basis.add("注册能力核验")
    advice = AgentAdvice(
        decision=decision,
        title="建议人工复核" if findings else "建议通过",
        summary=f"发现 {len(findings)} 项需要审核人员确认" if findings else "全部必检项目满足要求",
        findings=findings,
        recognition_confidence=confidence,
        confidence=confidence,
        basis=[
            item
            for item in (
                "页面申请信息",
                "资料图片识别",
                "二维码官网核验",
                "注册能力核验",
                "确定性审核规则",
            )
            if item in basis
        ],
        limitations=list(dict.fromkeys(limitations)),
    )
    return decision, advice
