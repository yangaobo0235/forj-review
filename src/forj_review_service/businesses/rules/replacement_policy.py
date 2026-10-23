"""报废置换地区政策确定性检查。"""

from datetime import date

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.replacement_policies import ReplacementPolicy
from forj_review_service.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.evidence import EvidenceFact
from forj_review_service.contracts.recognition import CheckResult
from forj_review_service.contracts.review import FieldObservation
from forj_review_service.evidence.comparison.evidence_values import (
    observation_evidence,
    readable_value,
)
from forj_review_service.evidence.fields.normalize import normalize_value


def _image_value(
    observations: list[FieldObservation],
    field: str,
    document_type: str,
    business_scope: str,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> tuple[object | None, list[EvidenceFact]]:
    sources = [
        item
        for item in observations
        if item.field == field
        and item.source_type == "image"
        and item.document_type == document_type
        and item.business_scope == business_scope
    ]
    values = {
        normalize_value(field, item.value, business=business): item.value
        for item in sources
        if item.value not in (None, "")
    }
    evidence = observation_evidence(sources)
    if len(values) != 1 or any(
        item.uncertain or not readable_value(item.value) for item in sources
    ):
        return None, evidence
    return next(iter(values.values())), evidence


def _parse_date(
    field: str, value: object | None, *, business: BusinessContext = DEFAULT_BUSINESS
) -> date | None:
    normalized = normalize_value(field, value, business=business)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _date_check(
    check_id: str,
    label: str,
    field: str,
    raw_value: object | None,
    lower: date | None,
    upper: date,
    evidence: list[EvidenceFact],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> CheckResult:
    parsed = _parse_date(field, raw_value, business=business)
    values = [CheckResultValue(source=label, value=raw_value)]
    if parsed is None:
        return CheckResult(
            check_id=check_id,
            label=label,
            status="INSUFFICIENT",
            reason_codes=["RECOGNITION_UNCERTAIN"]
            if any(item.uncertain for item in evidence)
            else ["EVIDENCE_MISSING"],
            reason=f"未从指定图片材料取得唯一有效的{label}",
            values=values,
            evidence=evidence,
        )
    matches = (lower is None or parsed >= lower) and parsed <= upper
    range_text = (
        f"{lower.isoformat()} 至 {upper.isoformat()}" if lower else f"不晚于 {upper.isoformat()}"
    )
    # 超期不判"不通过"，而是交人工复核：日期窗口是政策口径，窗口边上的一天
    # 之差通常要靠人工判断（材料出具时间、节假日顺延），自动否决会误伤。
    return CheckResult(
        check_id=check_id,
        label=label,
        status="MATCH" if matches else "INSUFFICIENT",
        reason_codes=[] if matches else ["POLICY_REVIEW"],
        reason=(
            f"{label}符合政策范围：{range_text}"
            if matches
            else f"{label} {parsed.isoformat()} 超出政策范围（{range_text}），请人工复核"
        ),
        values=values,
        evidence=evidence,
    )


def build_replacement_policy_checks(
    policy: ReplacementPolicy,
    observations: list[FieldObservation],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> list[CheckResult]:
    invoice_date, invoice_evidence = _image_value(
        observations, "invoice.invoice_date", "invoice", "new_vehicle", business=business
    )
    recycle_date, recycle_evidence = _image_value(
        observations,
        "old_vehicle.recycle_date",
        "scrap_certificate",
        "old_vehicle",
        business=business,
    )
    checks = [
        _date_check(
            "POLICY-INVOICE-DATE",
            "新车发票日期",
            "invoice.invoice_date",
            invoice_date,
            policy.invoice_date_from,
            policy.invoice_date_to,
            invoice_evidence,
            business=business,
        ),
        _date_check(
            "POLICY-DISPOSAL-DEADLINE",
            "回收证明交车日期",
            "old_vehicle.recycle_date",
            recycle_date,
            policy.disposal_date_from,
            policy.disposal_deadline,
            recycle_evidence,
            business=business,
        ),
    ]
    origin, origin_evidence = _image_value(
        observations, "new_vehicle.origin", "invoice", "new_vehicle", business=business
    )
    normalized_origin = normalize_value("new_vehicle.origin", origin, business=business)
    allowed = {
        normalize_value("new_vehicle.origin", item, business=business)
        for item in policy.allowed_origins
    }
    keywords = tuple(
        filter(
            None,
            (
                normalize_value("new_vehicle.origin", item, business=business)
                for item in policy.origin_keywords
            ),
        )
    )
    rule_description = (
        f"包含：{'、'.join(policy.origin_keywords)}"
        if keywords
        else f"允许：{'、'.join(policy.allowed_origins)}"
    )
    if not allowed and not keywords:
        status = "INSUFFICIENT"
        reason = "当前地区未配置新车发票产地规则"
    elif not normalized_origin:
        status = "INSUFFICIENT"
        reason = "未从新车销售发票取得唯一有效的产地"
    elif normalized_origin in allowed or any(keyword in normalized_origin for keyword in keywords):
        status = "MATCH"
        reason = f"新车销售发票产地符合当前地区规则（{rule_description}）"
    else:
        status = "CONFLICT"
        reason = f"新车销售发票产地不符合当前地区规则（{rule_description}）"
    checks.append(
        CheckResult(
            check_id="POLICY-NEW-ORIGIN",
            label="新车发票产地",
            status=status,
            reason=reason,
            reason_codes=["RECOGNITION_UNCERTAIN"]
            if any(item.uncertain for item in origin_evidence)
            else [],
            values=[
                CheckResultValue.model_validate({**item.model_dump(), "source": "新车销售发票"})
                for item in origin_evidence
            ]
            or [CheckResultValue(source="新车销售发票", value=origin)],
            evidence=origin_evidence,
        )
    )
    return checks


def run_replacement_policy(context: ReviewExecutionContext) -> RuleExecutionResult:
    """按 Profile 绑定的地区政策执行确定性检查。"""
    business = BusinessContext.from_profile(context.profile)
    policy = context.profile.replacement_policy
    if policy is None:
        raise ValueError("地区政策规则缺少 replacement_policy 配置")
    return RuleExecutionResult(
        checks=tuple(
            build_replacement_policy_checks(policy, list(context.observations), business=business)
        )
    )
