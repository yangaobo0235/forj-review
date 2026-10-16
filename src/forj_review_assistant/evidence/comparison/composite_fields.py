"""组合页面字段的确定性比较策略。

组合字段先校验页面内的两个控件，再与材料证据比较。这个模块只产出
稳定的 CheckResult，不负责任务展示或浏览器写回。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from forj_review_assistant.businesses.context import ContextMapping, ContextSequence
from forj_review_assistant.businesses.packs import BUSINESS_PACKS
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.review import FieldComparison, ReviewRequest
from forj_review_assistant.evidence.comparison.evidence_values import trusted_page_value
from forj_review_assistant.evidence.fields.normalize import normalize_value


@dataclass(frozen=True)
class CompositeFieldSpec:
    check_id: str
    label: str
    primary_field: str
    secondary_field: str
    material_field: str
    primary_label: str
    secondary_label: str


def _declared_composites(packs=None) -> tuple[CompositeFieldSpec, ...]:
    """从业务扩展包合成组合字段表。

    和材料策略、字段证据策略同样的做法：**字段键是业务知识，声明在
    `forj_review_assistant.businesses.packs`**，这里只把它转成运行时结构。写死在这张表里的
    后果是，每来一个带发票代码/号码的业务就要回来改一次引擎，而两个业务的
    字段键本来就不一样（报废置换是 `invoice.*`，过户是 `transfer.*`）。
    """
    return tuple(
        CompositeFieldSpec(
            check_id=item.check_id,
            label=item.label,
            primary_field=item.primary_field,
            secondary_field=item.secondary_field,
            material_field=item.material_field,
            primary_label=item.primary_label,
            secondary_label=item.secondary_label,
        )
        for pack in (BUSINESS_PACKS.values() if packs is None else packs)
        for item in pack.page_field_composites
    )


PAGE_FIELD_COMPOSITES = _declared_composites()

COMPOSITE_FIELDS_BY_MEMBER = {
    field: spec
    for spec in PAGE_FIELD_COMPOSITES
    for field in (spec.primary_field, spec.secondary_field)
}



PAGE_FIELD_COMPOSITES = ContextSequence(PAGE_FIELD_COMPOSITES, lambda p: _declared_composites((p,)))
COMPOSITE_FIELDS_BY_MEMBER = ContextMapping(COMPOSITE_FIELDS_BY_MEMBER,
    lambda p: {f: s for s in _declared_composites((p,)) for f in (s.primary_field, s.secondary_field)})


def build_page_composite_check(
    request: ReviewRequest,
    comparisons: Mapping[str, FieldComparison],
    spec: CompositeFieldSpec,
) -> CheckResult | None:
    """Build one authoritative check when either paired page field is present."""

    primary = request.page_fields.get(spec.primary_field)
    secondary = request.page_fields.get(spec.secondary_field)
    collected_fields = set(request.page_fields) | {
        item.field for item in request.review_fields if item.field
    }
    if (
        not {spec.primary_field, spec.secondary_field} & collected_fields
        and spec.material_field not in comparisons
    ):
        return None

    primary_normalized = normalize_value(spec.material_field, trusted_page_value(request, spec.primary_field))
    secondary_normalized = normalize_value(spec.material_field, trusted_page_value(request, spec.secondary_field))
    values = [
        CheckResultValue(source=spec.primary_field, value=primary),
        CheckResultValue(source=spec.secondary_field, value=secondary),
    ]
    comparison = comparisons.get(spec.material_field)
    material_values = []
    if comparison is not None:
        material_values = [
            CheckResultValue(
                source=item.source,
                differences=item.differences,
                comparison_status=item.comparison_status,
                uncertain=item.uncertain,
                value=item.value,
                source_id=item.source_id,
                image_id=item.image_id,
                image_index=item.image_index,
                document_type=item.document_type,
                detail=item.detail,
                derived_from=item.derived_from,
                evidence_region=item.evidence_region,
            )
            for item in comparison.evidence
            if item.source != "申请页面字段" and item.value not in (None, "")
        ]
        values.extend(material_values)

    if not primary_normalized or not secondary_normalized:
        status = "INSUFFICIENT"
        reason = "页面组合字段缺失，无法进行一致性核验"
    elif primary_normalized != secondary_normalized:
        status = "CONFLICT"
        reason = "页面两个字段原始值不一致，请先核对页面填写内容"
    elif comparison is None:
        status = "INSUFFICIENT"
        reason = "页面字段一致，但未取得材料核验值"
    else:
        status = {
            "MATCH": "MATCH",
            "CONFLICT": "CONFLICT",
            "REVIEW_REQUIRED": "INSUFFICIENT",
        }[comparison.status.value]
        reason = (
            "页面两个字段一致，材料提取值一致"
            if status == "MATCH"
            else "页面两个字段一致，但与材料提取值存在冲突"
            if status == "CONFLICT"
            else "页面两个字段一致，但材料证据不足"
        )

    return CheckResult(
        check_id=spec.check_id,
        label=spec.label,
        status=status,
        reason=reason,
        reason_codes=(comparison.reason_codes if comparison is not None
                      and primary_normalized == secondary_normalized else []),
        values=values,
        evidence=comparison.evidence if comparison is not None else [],
        details={
            "page_fields": [spec.primary_field, spec.secondary_field],
            "material_field": spec.material_field,
            "page_values_match": bool(
                primary_normalized
                and secondary_normalized
                and primary_normalized == secondary_normalized
            ),
        },
    )


def build_page_composite_checks(
    request: ReviewRequest,
    comparisons: Mapping[str, FieldComparison],
) -> list[CheckResult]:
    return [
        check
        for spec in PAGE_FIELD_COMPOSITES
        if (check := build_page_composite_check(request, comparisons, spec)) is not None
    ]
