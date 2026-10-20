"""最终审核建议生成。

主要职责：汇总字段、二维码和跨材料检查并形成安全建议。
修改日期：2026-08-26
"""

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.fields import PAGE_FIELD_LABELS
from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.recognition import (
    AgentAdvice,
    CheckResult,
    MaterialCompletenessReport,
)
from forj_review_service.contracts.review import (
    FieldComparison,
    FieldStatus,
    QrCheck,
    ReviewTask,
)
from forj_review_service.evidence.comparison.check_results import (
    qr_review_checks,
    unique_checks,
)

FIELD_LABELS = PAGE_FIELD_LABELS


def _field_finding(
    comparison: FieldComparison, *, business: BusinessContext = DEFAULT_BUSINESS
) -> CheckResult:
    values = [
        CheckResultValue(
            source=item.source,
            value=item.value,
            source_id=item.source_id,
            image_id=item.image_id,
            image_index=item.image_index,
            document_type=item.document_type,
            detail=item.detail,
            derived_from=item.derived_from,
            evidence_region=item.evidence_region,
            uncertain=item.uncertain,
            group_title=item.group_title,
        )
        for item in comparison.evidence
        if item.value not in (None, "")
    ]
    return CheckResult(
        check_id=f"FIELD-{comparison.field}",
        label=business.labels.get(comparison.field, comparison.field),
        status="CONFLICT" if comparison.status is FieldStatus.CONFLICT else "INSUFFICIENT",
        reason=comparison.message,
        reason_codes=comparison.reason_codes,
        values=values,
        evidence=[item.model_dump() for item in comparison.evidence],
    )


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
    composite_check_ids = {item.check_id for item in cross_checks}
    superseded_fields = {
        field
        for spec in business.composites
        if spec.check_id in composite_check_ids
        for field in (spec.primary_field, spec.secondary_field)
    }
    findings = [
        _field_finding(item, business=business)
        for item in comparisons
        if item.status is not FieldStatus.MATCH and item.field not in superseded_fields
    ]
    findings.extend(item for item in cross_checks if item.status != "MATCH")
    if completeness is not None and completeness.enforced:
        findings.extend(
            CheckResult(
                check_id=f"MATERIAL-{item.code}-{index}",
                label="资料完整性",
                status="INSUFFICIENT",
                reason=f"{item.message}；{item.suggested_action}",
            )
            for index, item in enumerate(completeness.issues, start=1)
        )
    # 缺少外部结果由声明 REQUIRED 的能力生成。
    findings.extend(item for item in qr_review_checks(qr_checks) if item.status != "MATCH")
    findings.extend(
        CheckResult(
            check_id=f"ISSUE-{index}",
            label="资料完整性",
            status="INSUFFICIENT",
            reason=issue,
        )
        for index, issue in enumerate(dict.fromkeys(issues), start=1)
    )
    findings.extend(
        CheckResult(
            check_id=f"LIMITATION-{index}",
            label="识别限制",
            status="INSUFFICIENT",
            reason=limitation,
        )
        for index, limitation in enumerate(dict.fromkeys(limitations), start=1)
    )

    represented = {item.check_id for item in findings}
    represented.update(
        f"FIELD-{item.field}" for item in comparisons if item.status is not FieldStatus.MATCH
    )
    for task in review_tasks or []:
        if task.result_status == "MATCH":
            continue
        ids = {task.step_id, *task.check_ids, *task.details.get("check_ids", [])}
        if task.step_id.startswith("BUSINESS-"):
            ids.add(task.step_id.removeprefix("BUSINESS-"))
        if ids & represented:
            continue
        # 控件未映射、页面歧义或系统字段异常也必须影响整单建议。
        findings.append(
            CheckResult(
                check_id=task.step_id,
                label=task.label,
                status=task.result_status,
                reason=task.reason,
                values=task.values,
                evidence=task.evidence,
            )
        )
        represented.update(ids)
    findings = unique_checks(findings)
    decision = "REVIEW_REQUIRED" if findings else "PASS"
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
