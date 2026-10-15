"""审核响应组装。

主要职责：把观察、检查、风险和建议转换为 API 响应。
修改日期：2026-08-26
修改人：wuyi
"""

import logging

from forj_review_assistant.businesses.field_policies import (
    MATERIAL_FIELD_BY_PAGE_FIELD,
    date_window_problem,
    field_policy,
    filter_allowed_observations,
)
from forj_review_assistant.businesses.fields import PAGE_FIELD_LABELS
from forj_review_assistant.businesses.profiles import BusinessProfile
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
from forj_review_assistant.evidence.comparison.aggregate import (
    aggregate_by_authority,
    aggregate_field,
)
from forj_review_assistant.evidence.comparison.check_results import unique_checks
from forj_review_assistant.evidence.fields.normalize import normalize_value
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
        + "+".join(sorted({fact.document_type or fact.source for fact in item.evidence if fact.uncertain}))
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


def _append_qr_observations(
    observations: list[FieldObservation],
    qr_checks: list[QrCheck],
) -> None:
    for check in qr_checks:
        for field, value in check.page_fields.items():
            mapped_field = (
                "old_vehicle.vin"
                if field == "vin"
                else "scrap_certificate.certificate_no"
            )
            observations.append(
                FieldObservation(
                    field=mapped_field,
                    source_type="qr_page",
                    source_id=f"qr-{check.image_index}",
                    image_index=check.image_index,
                    value=value,
                )
            )


def _comparable_qr_value(field_name: str, value: object) -> str:
    normalized = normalize_value(field_name, value) or ""
    # Official certificate numbers may omit the printed separators.
    if field_name == "scrap_certificate.certificate_no":
        return "".join(ch for ch in normalized.upper() if ch.isalnum())
    return normalized


def _mark_qr_conflicts(
    observations: list[FieldObservation],
    qr_checks: list[QrCheck],
) -> bool:
    has_conflict = False
    for check in qr_checks:
        for page_field, page_value in check.page_fields.items():
            candidates = [
                item.value
                for item in observations
                if item.image_index == check.image_index
                and item.source_type == "image"
                and (
                    (page_field == "vin" and item.field.endswith(".vin"))
                    or (
                        page_field == "certificate_no"
                        and item.field.endswith("certificate_no")
                    )
                )
                and item.value not in (None, "")
            ]
            field_name = (
                "old_vehicle.vin"
                if page_field == "vin"
                else "scrap_certificate.certificate_no"
            )
            if any(_comparable_qr_value(field_name, value) != _comparable_qr_value(field_name, page_value) for value in candidates):
                check.status = FieldStatus.CONFLICT
                check.message = "二维码网页字段与图片识别结果冲突"
                has_conflict = True
    return has_conflict


def _build_comparisons(
    request: ReviewRequest,
    profile: BusinessProfile,
    observations: list[FieldObservation],
) -> list[FieldComparison]:
    fields = list(profile.required_fields)
    collected_fields = set(request.page_fields) | {item.field for item in request.review_fields}
    fields.extend(field for field in MATERIAL_FIELD_BY_PAGE_FIELD if field in collected_fields and field not in fields)

    def field_observations(field_name: str) -> list[FieldObservation]:
        material_field = MATERIAL_FIELD_BY_PAGE_FIELD.get(field_name, field_name)
        return [item for item in observations if item.field == (field_name if item.source_type == "page" else material_field)]

    comparisons = []
    for field_name in fields:
        observations_for_field = filter_allowed_observations(
            field_name, field_observations(field_name)
        )
        policy = field_policy(field_name)
        uncertain_requires_review = policy.uncertain_requires_review if policy else True
        page_evidence_required = policy.page_evidence_required if policy else True
        # 声明了权威链的字段走声明式裁决：比较基准由业务指定，不靠票数推断。
        # 新增需要权威关系的字段只需在 field_evidence_policies 里声明，
        # 不必再写专用裁决函数。
        if policy is not None and policy.authority:
            comparisons.append(aggregate_by_authority(
                field_name,
                observations_for_field,
                policy.authority,
                uncertain_requires_review=uncertain_requires_review,
                page_evidence_required=page_evidence_required,
            ))
            continue
        if policy and policy.mode == "SYSTEM":
            comparison = aggregate_field(
                field_name,
                observations_for_field,
                uncertain_requires_review=False,
                single_evidence_requires_review=False,
            )
            has_page_value = request.page_fields.get(field_name) not in (None, "")
            comparisons.append(comparison.model_copy(update={
                "status": FieldStatus.MATCH if has_page_value else FieldStatus.REVIEW_REQUIRED,
                "message": (
                    "以页面填写值为准，不参与材料比对"
                    if has_page_value
                    else "页面字段未填写，请人工确认"
                ),
            }))
            continue
        comparisons.append(aggregate_field(
            field_name,
            observations_for_field,
            uncertain_requires_review=uncertain_requires_review,
            single_evidence_requires_review=(
                policy.allow_single_evidence is False if policy else True
            ),
            page_evidence_required=page_evidence_required,
            required_document_types=(
                policy.allowed_document_types
                if policy and not policy.allow_single_evidence and len(policy.allowed_document_types) > 1
                else ()
            ),
        ))
    for comparison in comparisons:
        problem = date_window_problem(
            PAGE_FIELD_LABELS.get(comparison.field, comparison.field),
            comparison.field,
            request.page_fields.get(comparison.field),
        )
        if problem:
            if comparison.status == FieldStatus.MATCH:
                comparison.status = FieldStatus.REVIEW_REQUIRED
            comparison.message = f"{comparison.message}；{problem}"
    return comparisons


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
    _append_qr_observations(observations, resolved_qr_checks)
    _mark_qr_conflicts(observations, resolved_qr_checks)
    comparisons = _build_comparisons(request, profile, observations)
    cross_checks = unique_checks(business_checks or [])
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
        qr_checks=resolved_qr_checks,
        cross_checks=cross_checks,
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
            "image_failed_count": sum(
                bool(image.collection_error) for image in request.images
            ),
            "completed_count": batch.completed_count,
            "failed_count": batch.failed_count,
            "timed_out_count": batch.timed_out_count,
        },
        material_completeness=batch.material_completeness,
        retry_summary=batch.retry_summary,
        page_fill_intent=page_fill_intent or [],
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
            "image_failed_count": sum(
                bool(image.collection_error) for image in request.images
            ),
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
