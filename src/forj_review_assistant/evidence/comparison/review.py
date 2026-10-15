"""Deterministic field comparison, independent of API response rendering."""

from forj_review_assistant.businesses.field_policies import (
    MATERIAL_FIELD_BY_PAGE_FIELD,
    date_window_problem,
    field_policy,
    filter_allowed_observations,
)
from forj_review_assistant.businesses.fields import PAGE_FIELD_LABELS
from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.contracts.review import (
    FieldComparison,
    FieldObservation,
    FieldStatus,
    ReviewRequest,
)
from forj_review_assistant.evidence.comparison.aggregate import (
    aggregate_by_authority,
    aggregate_field,
)


def build_field_comparisons(
    request: ReviewRequest,
    profile: BusinessProfile,
    observations: list[FieldObservation],
) -> list[FieldComparison]:
    fields = list(profile.required_fields)
    collected_fields = set(request.page_fields) | {item.field for item in request.review_fields}
    fields.extend(
        field
        for field in MATERIAL_FIELD_BY_PAGE_FIELD
        if field in collected_fields and field not in fields
    )

    def field_observations(field_name: str) -> list[FieldObservation]:
        material_field = MATERIAL_FIELD_BY_PAGE_FIELD.get(field_name, field_name)
        return [
            item
            for item in observations
            if (
                item.field in {field_name, material_field}
                if item.source_type == "capability"
                else item.field == (field_name if item.source_type == "page" else material_field)
            )
        ]

    comparisons = []
    for field_name in fields:
        observations_for_field = filter_allowed_observations(
            field_name,
            field_observations(field_name),
            capability_sources=frozenset(
                spec.capability_id
                for spec in profile.capabilities
                if field_name in spec.output_facts
                or MATERIAL_FIELD_BY_PAGE_FIELD.get(field_name) in spec.output_facts
            ),
        )
        policy = field_policy(field_name)
        uncertain_requires_review = policy.uncertain_requires_review if policy else True
        page_evidence_required = policy.page_evidence_required if policy else True
        # 声明了权威链的字段走声明式裁决：比较基准由业务指定，不靠票数推断。
        # 新增需要权威关系的字段只需在 field_evidence_policies 里声明，
        # 不必再写专用裁决函数。
        if policy is not None and policy.authority:
            comparisons.append(
                aggregate_by_authority(
                    field_name,
                    observations_for_field,
                    policy.authority,
                    uncertain_requires_review=uncertain_requires_review,
                    page_evidence_required=page_evidence_required,
                )
            )
            continue
        if policy and policy.mode == "SYSTEM":
            comparison = aggregate_field(
                field_name,
                observations_for_field,
                uncertain_requires_review=False,
                single_evidence_requires_review=False,
            )
            has_page_value = request.page_fields.get(field_name) not in (None, "")
            comparisons.append(
                comparison.model_copy(
                    update={
                        "status": FieldStatus.MATCH
                        if has_page_value
                        else FieldStatus.REVIEW_REQUIRED,
                        "message": (
                            "以页面填写值为准，不参与材料比对"
                            if has_page_value
                            else "页面字段未填写，请人工确认"
                        ),
                    }
                )
            )
            continue
        comparisons.append(
            aggregate_field(
                field_name,
                observations_for_field,
                uncertain_requires_review=uncertain_requires_review,
                single_evidence_requires_review=(
                    policy.allow_single_evidence is False if policy else True
                ),
                page_evidence_required=page_evidence_required,
                required_document_types=(
                    policy.allowed_document_types
                    if policy
                    and not policy.allow_single_evidence
                    and len(policy.allowed_document_types) > 1
                    else ()
                ),
            )
        )
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
