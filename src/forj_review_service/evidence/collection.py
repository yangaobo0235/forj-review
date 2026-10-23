"""One evidence input for capability handlers, field comparison and final facts."""

from collections.abc import Sequence

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.contracts.evidence import EvidenceFact, FieldObservation
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import (
    CapabilityResult,
    FieldComparison,
    QrCheck,
    ReviewRequest,
)
from forj_review_service.evidence.comparison.aggregate import EVIDENCE_SOURCE_LABELS
from forj_review_service.evidence.comparison.evidence_values import batch_observations
from forj_review_service.evidence.fields.normalize import normalize_value
from forj_review_service.evidence.qr import append_qr_observations


def capability_observations(
    profile: BusinessProfile,
    results: Sequence[CapabilityResult],
) -> list[FieldObservation]:
    """Only successful, declared outputs enter evidence; provenance is assigned here."""
    outputs = {spec.capability_id: set(spec.output_facts) for spec in profile.capabilities}
    return [
        FieldObservation.model_validate(
            {
                **fact.model_dump(),
                "source_type": "capability",
                "source_id": result.capability_id,
            }
        )
        for result in results
        if result.status == "SUCCEEDED"
        for fact in result.evidence
        if fact.field in outputs.get(result.capability_id, ())
    ]


def collect_observations(
    request: ReviewRequest,
    profile: BusinessProfile,
    batch: RecognitionBatchResult,
    results: Sequence[CapabilityResult] = (),
    qr_checks: Sequence[QrCheck] = (),
) -> tuple[list[FieldObservation], list[QrCheck]]:
    """Keep source values intact and never mutate extraction or external results."""
    business = BusinessContext.from_profile(profile)
    observations = [
        item
        for item in batch_observations(batch, business=business)
        if item.source_type != "capability"
    ]
    observations.extend(capability_observations(profile, results))
    field_keys = (
        tuple(field.key for field in profile.pack_declaration.fields)
        if profile.pack_declaration
        else profile.required_fields
    )
    observations.extend(
        FieldObservation(
            field=key, source_type="page", source_id="review_page", value=request.page_fields[key]
        )
        for key in field_keys
        if request.page_fields.get(key) not in (None, "")
    )
    checks = [check.model_copy(deep=True) for check in qr_checks]
    append_qr_observations(observations, checks, profile)
    return observations, checks


def collection_issues(request: ReviewRequest, profile: BusinessProfile) -> list[str]:
    issues = []
    if not request.images and (profile.material_policy is not None or profile.required_fields):
        issues.append("未采集到审核图片")
    required_page_fields = profile.required_fields
    if profile.pack_declaration is not None:
        required_page_fields = tuple(
            field.key
            for field in profile.pack_declaration.fields
            if field.key in profile.required_fields
            and field.page_evidence_required
            and field.mode != "DERIVED"
        )
    if not request.page_fields and required_page_fields:
        issues.append("未采集到右侧申请字段")
    issues.extend(
        f"图片 {image.image_id or image.index} 采集失败：{image.collection_error}"
        for image in request.images
        if image.collection_error
    )
    return issues


def assemble_evidence_facts(
    observations: Sequence[FieldObservation],
    comparisons: Sequence[FieldComparison],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> list[EvidenceFact]:
    """Retain intermediate capability facts as well as enriched comparison evidence."""
    facts = [fact for comparison in comparisons for fact in comparison.evidence]
    represented = {(fact.field, fact.source_id) for fact in facts}
    facts.extend(
        EvidenceFact(
            **item.model_dump(exclude={"source_type", "retried"}),
            source=EVIDENCE_SOURCE_LABELS.get(item.source_type, item.source_type),
            normalized_value=normalize_value(item.field, item.value, business=business),
        )
        for item in observations
        if (item.field, item.source_id) not in represented
    )
    return facts
