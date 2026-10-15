"""One evidence input for capability handlers, field comparison and final facts."""

from collections.abc import Sequence

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.rules.scrap_certificate_qr import (
    append_qr_observations,
    mark_qr_conflicts,
)
from forj_review_assistant.contracts.evidence import EvidenceFact, FieldObservation
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    CapabilityResult,
    FieldComparison,
    QrCheck,
    ReviewRequest,
)
from forj_review_assistant.evidence.comparison.aggregate import EVIDENCE_SOURCE_LABELS
from forj_review_assistant.evidence.comparison.evidence_values import batch_observations
from forj_review_assistant.evidence.fields.normalize import normalize_value


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
    batch: AgentBatchResult,
    results: Sequence[CapabilityResult] = (),
    qr_checks: Sequence[QrCheck] = (),
) -> tuple[list[FieldObservation], list[QrCheck]]:
    """Keep source values intact and never mutate extraction or external results."""
    observations = [item for item in batch_observations(batch) if item.source_type != "capability"]
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
    append_qr_observations(observations, checks)
    mark_qr_conflicts(observations, checks)
    return observations, checks


def collection_issues(request: ReviewRequest, profile: BusinessProfile) -> list[str]:
    issues = []
    if not request.images and (profile.material_policy is not None or profile.required_fields):
        issues.append("未采集到审核图片")
    if not request.page_fields and profile.required_fields:
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
) -> list[EvidenceFact]:
    """Retain intermediate capability facts as well as enriched comparison evidence."""
    facts = [fact for comparison in comparisons for fact in comparison.evidence]
    represented = {(fact.field, fact.source_id) for fact in facts}
    facts.extend(
        EvidenceFact(
            **item.model_dump(exclude={"source_type", "retried"}),
            source=EVIDENCE_SOURCE_LABELS.get(item.source_type, item.source_type),
            normalized_value=normalize_value(item.field, item.value),
        )
        for item in observations
        if (item.field, item.source_id) not in represented
    )
    return facts
