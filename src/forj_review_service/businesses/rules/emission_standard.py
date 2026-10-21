"""报废车辆排放标准核验：明确材料值优先，型号先精确查表、未命中再按系列推导。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from forj_review_service.businesses.rules.emission_patterns import infer_engine_emission
from forj_review_service.capabilities.specs import ReviewExecutionContext, RuleExecutionResult
from forj_review_service.contracts.checks import CheckResult, CheckResultValue
from forj_review_service.contracts.engine_emissions import (
    AsyncEngineEmissionLookup,
    EngineEmissionLookup,
    EngineEmissionLookupUnavailable,
    engine_model_key,
)
from forj_review_service.contracts.review import FieldObservation
from forj_review_service.evidence.comparison.evidence_values import (
    observation_evidence,
    trusted_page_value,
)
from forj_review_service.evidence.fields.normalize import canonical_emission_standard
from forj_review_service.evidence.fields.readability import readable_value

EMISSION_STANDARD_CHECK_ID = "EMISSION-STANDARD-001"
EMISSION_STANDARD_LABEL = "排放标准"
EMISSION_FIELD = "old_vehicle.emission_standard"
ENGINE_FIELD = "old_vehicle.engine_model"
FUEL_FIELD = "old_vehicle.fuel_type"
_EMPTY_PAGE_VALUES = frozenset({"", "未填写", "未采集", "请选择", "请输入"})


def _image_values(context: ReviewExecutionContext, field: str):
    return [
        item
        for item in context.observations
        if item.source_type == "image"
        and item.field == field
        and item.document_type == "registration_certificate"
        and str(item.value or "").strip()
    ]


def _page_value(context: ReviewExecutionContext) -> tuple[object | None, str | None]:
    value = trusted_page_value(context.request, EMISSION_FIELD)
    if value is None or str(value).strip() in _EMPTY_PAGE_VALUES or not readable_value(value):
        return value, None
    return value, canonical_emission_standard(value)


def _values_for_observations(items: Iterable, *, source_label: str | None = None):
    return [
        CheckResultValue(
            source=source_label or (item.document_type or "登记证书"),
            fill_role="EVIDENCE",
            value=item.value,
            uncertain=item.uncertain,
            source_id=item.source_id,
            image_id=item.image_id,
            image_index=item.image_index,
            document_type=item.document_type,
        )
        for item in items
    ]


def _check(
    status: str,
    reason: str,
    *,
    values: list[CheckResultValue],
    evidence: list,
    reason_codes: list | None = None,
    details: dict[str, object] | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=EMISSION_STANDARD_CHECK_ID,
        label=EMISSION_STANDARD_LABEL,
        status=status,
        reason=reason,
        reason_codes=reason_codes or [],
        values=values,
        evidence=observation_evidence(evidence),
        details=details or {},
    )


@dataclass(frozen=True)
class _EmissionReference:
    models: list[str]
    page_standard: str | None
    page_values: list[CheckResultValue]
    engine_items: list[FieldObservation]
    fuel_items: list[FieldObservation]
    evidence: list[FieldObservation]

    def unavailable(self, error: EngineEmissionLookupUnavailable) -> RuleExecutionResult:
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    str(error),
                    values=self.page_values + _values_for_observations(self.engine_items),
                    evidence=self.evidence,
                    reason_codes=["EVIDENCE_MISSING"],
                    details={
                        "source": "engine_model",
                        "lookup_table": "ref_engine_emission",
                        "engine_models": self.models,
                    },
                ),
            )
        )


def _prepare_emission(
    context: ReviewExecutionContext,
) -> RuleExecutionResult | _EmissionReference:
    """核验旧车排放标准，登记证无明确值时先查表，再按已有系列规则兜底。"""

    if context.profile.business_type != "scrap_replacement":
        return RuleExecutionResult()

    page_raw, page_standard = _page_value(context)
    emission_items = _image_values(context, EMISSION_FIELD)
    engine_items = _image_values(context, ENGINE_FIELD)
    fuel_items = _image_values(context, FUEL_FIELD)
    evidence = [*emission_items, *engine_items, *fuel_items]
    page_values = (
        [CheckResultValue(source="申请页面字段", value=page_raw)]
        if page_raw not in (None, "")
        else []
    )

    reliable_emission = [
        item
        for item in emission_items
        if not item.uncertain
        and readable_value(item.value)
        and canonical_emission_standard(item.value)
    ]
    uncertain_emission = [item for item in emission_items if item not in reliable_emission]
    explicit_standards = {canonical_emission_standard(item.value) for item in reliable_emission}
    if len(explicit_standards) > 1:
        return RuleExecutionResult(
            checks=(
                _check(
                    "CONFLICT",
                    "登记证书上的排放标准存在不同读值，请核对原图",
                    values=page_values + _values_for_observations(emission_items),
                    evidence=evidence,
                    reason_codes=["VALUE_MISMATCH"],
                ),
            )
        )
    if uncertain_emission:
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    "登记证书排放标准识别不确定，请核对原图",
                    values=page_values + _values_for_observations(emission_items),
                    evidence=evidence,
                    reason_codes=["RECOGNITION_UNCERTAIN"],
                ),
            )
        )
    if explicit_standards:
        expected = next(iter(explicit_standards))
        if page_standard is None:
            return RuleExecutionResult(
                checks=(
                    _check(
                        "INSUFFICIENT",
                        f"登记证书排放标准识别为 {expected}，页面尚未填写，请核对后回填",
                        values=page_values
                        + _values_for_observations(emission_items)
                        + [
                            CheckResultValue(
                                source="登记证书排放标准推导",
                                value=expected,
                                fill_role="RECOMMENDATION",
                                derived_from="登记证书排放标准",
                            )
                        ],
                        evidence=evidence,
                        reason_codes=["PAGE_VALUE_MISSING"],
                        details={
                            "source": "registration_certificate",
                            "standard": expected,
                            "permitted_fill_values": [expected],
                        },
                    ),
                )
            )
        status = "MATCH" if expected == page_standard else "CONFLICT"
        values = page_values + _values_for_observations(emission_items)
        if status == "CONFLICT":
            values.append(
                CheckResultValue(
                    source="登记证书排放标准推导",
                    value=expected,
                    fill_role="RECOMMENDATION",
                    derived_from="登记证书排放标准",
                )
            )
        return RuleExecutionResult(
            checks=(
                _check(
                    status,
                    "登记证书排放标准与页面一致"
                    if status == "MATCH"
                    else f"登记证书排放标准为 {expected}，与页面填写的 {page_standard} 不一致",
                    values=values,
                    evidence=evidence,
                    reason_codes=[] if status == "MATCH" else ["VALUE_MISMATCH"],
                    details={"source": "registration_certificate", "standard": expected},
                ),
            )
        )

    if not engine_items or any(
        item.uncertain or not readable_value(item.value) for item in engine_items
    ):
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    "登记证书未明确记载排放标准，发动机型号识别不完整，无法查询排放对应表",
                    values=page_values + _values_for_observations(engine_items),
                    evidence=evidence,
                    reason_codes=["EVIDENCE_MISSING", "RECOGNITION_UNCERTAIN"],
                ),
            )
        )

    models = sorted({engine_model_key(item.value) for item in engine_items})
    if any(len(model) < 2 for model in models):
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    "发动机型号不完整，无法查询排放对应表",
                    values=page_values + _values_for_observations(engine_items),
                    evidence=evidence,
                    reason_codes=["RECOGNITION_UNCERTAIN"],
                ),
            )
        )
    return _EmissionReference(
        models, page_standard, page_values, engine_items, fuel_items, evidence
    )


def _evaluate_reference(
    reference: _EmissionReference, standards: dict[str, str]
) -> RuleExecutionResult:
    models, page_standard = reference.models, reference.page_standard
    page_values, engine_items = reference.page_values, reference.engine_items
    fuel_items, evidence = reference.fuel_items, reference.evidence
    details = {
        "source": "engine_model",
        "lookup_table": "ref_engine_emission",
        "engine_models": models,
    }
    # Inference augments a local copy; callers may reuse their reference fixture.
    standards = dict(standards)
    lookup_results = dict(standards)
    rule_results = {}
    # 不新增燃料证据要求；已有证据明确非柴油或燃料读值不确定时，不能套柴油规则。
    fuel_allows_inference = all(
        not item.uncertain and str(item.value).strip() == "柴油" for item in fuel_items
    )
    details["fuel_scope"] = (
        "diesel"
        if fuel_items and fuel_allows_inference
        else ("unknown" if not fuel_items else "not_eligible")
    )
    for model in models:
        if model in standards:
            continue
        candidate = infer_engine_emission(model) if fuel_allows_inference else None
        if candidate is not None:
            standards[model] = candidate.standard
            rule_results[model] = {
                "standard": candidate.standard,
                "rule_id": candidate.rule_id,
                "family": candidate.family,
                "marker": candidate.marker,
            }
    method = "mixed" if lookup_results and rule_results else "pattern" if rule_results else "lookup"
    details.update(
        {"method": method, "lookup_results": lookup_results, "rule_results": rule_results}
    )
    missing = [model for model in models if model not in standards]
    if missing:
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    "发动机排放对应表未收录完整型号且无法按已配置系列规则推导："
                    + "、".join(missing)
                    + "，请人工复核",
                    values=page_values + _values_for_observations(engine_items),
                    evidence=evidence,
                    reason_codes=["EVIDENCE_MISSING"],
                    details={**details, "missing_models": missing},
                ),
            )
        )
    resolved = {standards[model] for model in models}
    if len(resolved) != 1:
        return RuleExecutionResult(
            checks=(
                _check(
                    "CONFLICT",
                    "不同发动机型号查表或规则推导得到的排放标准不一致，请人工核对登记证书",
                    values=page_values + _values_for_observations(engine_items),
                    evidence=evidence,
                    reason_codes=["VALUE_MISMATCH"],
                    details=details,
                ),
            )
        )
    expected = next(iter(resolved))
    result_label = {
        "lookup": "发动机型号查表",
        "pattern": "发动机型号规则推导",
        "mixed": "发动机型号查表与规则推导",
    }[method]
    values = page_values + _values_for_observations(engine_items)
    if page_standard != expected:
        values.append(
            CheckResultValue(
                source=result_label + "参考值",
                value=expected,
                fill_role="RECOMMENDATION",
                derived_from="；".join(models),
            )
        )
    if page_standard is None:
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    f"页面排放标准未填写，{result_label}为 {expected}，请核对后回填",
                    values=values,
                    evidence=evidence,
                    reason_codes=["PAGE_VALUE_MISSING"],
                    details={**details, "standard": expected, "permitted_fill_values": [expected]},
                ),
            )
        )
    status = "MATCH" if expected == page_standard else "CONFLICT"
    return RuleExecutionResult(
        checks=(
            _check(
                status,
                f"{result_label}结果与页面排放标准一致"
                if status == "MATCH"
                else f"{result_label}为 {expected}，与页面填写的 {page_standard} 不一致",
                values=values,
                evidence=evidence,
                reason_codes=[] if status == "MATCH" else ["VALUE_MISMATCH"],
                details={**details, "standard": expected},
            ),
        )
    )


def run_emission_standard(
    context: ReviewExecutionContext,
    *,
    lookup: EngineEmissionLookup | None = None,
) -> RuleExecutionResult:
    """Evaluate with a synchronous reference provider for standalone callers."""
    prepared = _prepare_emission(context)
    if isinstance(prepared, RuleExecutionResult):
        return prepared
    try:
        if lookup is None:
            raise EngineEmissionLookupUnavailable("发动机排放对应表查询未配置，请检查服务装配")
        standards = lookup(prepared.models)
    except EngineEmissionLookupUnavailable as error:
        return prepared.unavailable(error)
    return _evaluate_reference(prepared, standards)


async def run_emission_standard_async(
    context: ReviewExecutionContext,
    *,
    lookup: AsyncEngineEmissionLookup | None = None,
) -> RuleExecutionResult:
    """Await reference data only when material evidence cannot resolve the check."""
    prepared = _prepare_emission(context)
    if isinstance(prepared, RuleExecutionResult):
        return prepared
    try:
        if lookup is None:
            raise EngineEmissionLookupUnavailable("发动机排放对应表查询未配置，请检查服务装配")
        standards = await lookup(prepared.models)
    except EngineEmissionLookupUnavailable as error:
        return prepared.unavailable(error)
    return _evaluate_reference(prepared, standards)
