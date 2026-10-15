"""审核响应组装。

主要职责：把观察、检查、风险和建议转换为 API 响应。
修改日期：2026-08-26
"""

import logging

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.rules.scrap_certificate_qr import (
    append_qr_observations,
    mark_qr_conflicts,
)
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldComparison,
    FieldObservation,
    FieldStatus,
    QrCheck,
    Recommendation,
    ResultSection,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_assistant.evidence.comparison.check_results import unique_checks
from forj_review_assistant.evidence.comparison.review import build_field_comparisons
from forj_review_assistant.presentation.assembly import assemble_review_response

logger = logging.getLogger("uvicorn.error")


def _log_decision(response: ReviewResponse, note: str = "") -> None:
    """结论摘要：配置回测与影响面对账的唯一数据源。

    只记字段键、状态与来源材料类型，**绝不记值**。没有这一行，"新配置跑出的结论与
    当时人工判断是否一致"根本无法核对，agent 的映射质量就只能靠人看一眼——那不是判卷。
    """
    statuses = ",".join(f"{item.field}:{item.status.value}" for item in response.comparisons)
    conflicts = ",".join(
        f"{item.field}:"
        + "+".join(sorted({fact.document_type or fact.source for fact in item.evidence}))
        for item in response.comparisons
        if item.status == FieldStatus.CONFLICT
    )
    uncertain = ",".join(
        f"{item.field}:"
        + "+".join(
            sorted({fact.document_type or fact.source for fact in item.evidence if fact.uncertain})
        )
        for item in response.comparisons
        if any(fact.uncertain for fact in item.evidence)
    )
    logger.info(
        "review decision trace_id=%s business=%s region=%s profile=%s recommendation=%s"
        " note=%s fields=%s conflicts=%s uncertain=%s",
        response.trace_id,
        response.business_type,
        response.region,
        response.profile_version,
        response.recommendation.value,
        note or "-",
        statuses or "-",
        conflicts or "-",
        uncertain or "-",
    )


def response_from_results(
    request: ReviewRequest,
    batch: AgentBatchResult,
    profile: BusinessProfile,
    comparisons: list[FieldComparison],
    issues: list[str],
    *,
    qr_checks: list[QrCheck] | None = None,
    business_checks: list | None = None,
    page_fill_intent: list | None = None,
) -> ReviewResponse:
    """Render typed execution results without extracting evidence or running rules."""
    response = ReviewResponse(
        presentation="FIELD_WORKBENCH" if profile.rules_configured else "MANUAL_REVIEW",
        trace_id=request.trace_id or "",
        business_type=request.business_type,
        region=request.region,
        profile_version=profile.version,
        recommendation=Recommendation.REVIEW_REQUIRED,
        risk_level="MEDIUM",
        summary="审核检查正在进行",
        comparisons=comparisons,
        qr_checks=qr_checks or [],
        cross_checks=unique_checks(business_checks or []),
        issues=issues,
        sections=[
            ResultSection(
                id=section.id,
                title=section.title,
                fields=list(section.fields),
            )
            for section in profile.sections
        ],
        context_summary={
            "field_count": len(request.review_fields) or len(request.page_fields),
            "image_count": len(request.images),
            "image_failed_count": sum(bool(image.collection_error) for image in request.images),
            "completed_count": batch.completed_count,
            "failed_count": batch.failed_count,
            "timed_out_count": batch.timed_out_count,
        },
        material_completeness=batch.material_completeness,
        retry_summary=batch.retry_summary,
        page_fill_intent=page_fill_intent or [],
    )
    return response


def build_review_response(
    request: ReviewRequest,
    batch: AgentBatchResult,
    profile: BusinessProfile,
    observations: list[FieldObservation],
    issues: list[str],
    qr_checks: list[QrCheck] | None = None,
    business_checks: list | None = None,
    page_fill_intent: list | None = None,
    defer_advice: bool = False,
) -> ReviewResponse:
    """把提取结果和确定性检查组装为稳定的审核响应。"""

    resolved_qr_checks = qr_checks or []
    append_qr_observations(observations, resolved_qr_checks)
    mark_qr_conflicts(observations, resolved_qr_checks)
    comparisons = build_field_comparisons(request, profile, observations)
    cross_checks = unique_checks(business_checks or [])
    response = response_from_results(
        request,
        batch,
        profile,
        comparisons,
        issues,
        qr_checks=resolved_qr_checks,
        business_checks=cross_checks,
        page_fill_intent=page_fill_intent,
    )
    decision = (
        response
        if defer_advice
        else assemble_review_response(
            response,
            batch,
            business_checks=cross_checks,
            page_actions=page_fill_intent,
        )
    )
    if not defer_advice:
        _log_decision(decision, "assembled")
    return decision


def build_unconfigured_response(
    request: ReviewRequest,
    profile: BusinessProfile,
) -> ReviewResponse:
    """Build the stable manual-review response for an unconfigured profile."""

    message = profile.unconfigured_message or "当前审核业务规则尚未配置，请人工复核"
    response = ReviewResponse(
        presentation="FIELD_WORKBENCH" if profile.rules_configured else "MANUAL_REVIEW",
        business_type=request.business_type,
        region=request.region,
        profile_version=profile.version,
        recommendation=Recommendation.REVIEW_REQUIRED,
        risk_level="MEDIUM",
        summary=message,
        issues=[message],
        sections=[],
        context_summary={
            "field_count": len(request.review_fields) or len(request.page_fields),
            "image_count": len(request.images),
            "image_failed_count": sum(bool(image.collection_error) for image in request.images),
            "completed_count": 0,
            "failed_count": 0,
            "timed_out_count": 0,
        },
    )
    decision = assemble_review_response(
        response, AgentBatchResult(), business_checks=[]
    ).model_copy(update={"summary": message})
    _log_decision(decision, "unconfigured")
    return decision
