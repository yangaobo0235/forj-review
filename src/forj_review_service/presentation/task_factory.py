"""统一构造审核任务及其证据元数据，不决定业务规则或页面顺序。"""

from collections.abc import Sequence
from typing import Literal

from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.decisions import ReasonCode, default_reason_codes
from forj_review_service.contracts.review import EvidenceFact, ReviewDisplayTarget, ReviewTask

Category = Literal["FIELD", "EXTERNAL", "BUSINESS_RULE"]
ResultStatus = Literal["MATCH", "CONFLICT", "INSUFFICIENT"]


def make_task(
    *,
    step_id: str,
    category: Category,
    display_target: ReviewDisplayTarget,
    label: str,
    result_status: ResultStatus,
    reason: str,
    reason_codes: Sequence[ReasonCode] = (),
    page_field: str | None = None,
    page_target_field: str | None = None,
    page_target_fields: Sequence[str] = (),
    page_value: object | None = None,
    page_values: Sequence[CheckResultValue] = (),
    control_type: str | None = None,
    writable: bool = False,
    values: Sequence[CheckResultValue] = (),
    evidence: Sequence[EvidenceFact] = (),
    details: dict[str, object] | None = None,
    requires_reviewer_action: bool | None = None,
    evidence_mode: str | None = None,
    not_found_sources: Sequence[str] = (),
    normalized_page_value: str | None = None,
) -> ReviewTask:
    """构建单个步骤；MATCH 不需要人工处理，其余状态必须处理。"""
    resolved_details = details or {}
    return ReviewTask(
        step_id=step_id,
        check_ids=[step_id],
        sequence=1,
        category=category,
        display_target=display_target,
        page_field=page_field if display_target is ReviewDisplayTarget.PAGE_FIELD else None,
        page_target_field=page_target_field,
        page_target_fields=list(page_target_fields),
        page_value=page_value,
        page_values=list(page_values),
        control_type=control_type,
        writable=writable,
        requires_reviewer_action=(result_status != "MATCH")
        if requires_reviewer_action is None
        else requires_reviewer_action,
        label=label,
        result_status=result_status,
        reason=reason,
        reason_codes=list(reason_codes) or default_reason_codes(result_status),
        values=[
            item.model_copy(
                update={
                    "comparison_status": item.comparison_status
                    or (
                        "INSUFFICIENT"
                        if item.uncertain
                        else result_status
                        if item.conflicting is not None or item.differences
                        else None
                    ),
                }
            )
            for item in values
        ],
        evidence=list(evidence),
        details=resolved_details,
        evidence_count=len(evidence),
        evidence_sources=list(dict.fromkeys(item.source for item in evidence)),
        evidence_mode=evidence_mode,
        not_found_sources=list(not_found_sources),
        normalized_page_value=normalized_page_value,
    )
