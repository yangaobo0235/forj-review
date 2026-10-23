"""Deterministic review policy, independent of HTTP and advice rendering."""

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.recognition import (
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
        evidence=list(comparison.evidence),
    )


def evaluate_review(
    comparisons: list[FieldComparison],
    cross_checks: list[CheckResult],
    qr_checks: list[QrCheck],
    issues: list[str],
    limitations: list[str],
    *,
    completeness: MaterialCompletenessReport | None = None,
    review_tasks: list[ReviewTask] | None = None,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> tuple[str, list[CheckResult]]:
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
    return decision, findings
