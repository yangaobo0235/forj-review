"""Owner-type and affiliation control checks, independent of task presentation."""

from collections.abc import Sequence
from typing import Any, Literal

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.rules.affiliation import normalize_subject_type
from forj_review_service.contracts.checks import CheckResult, CheckResultValue


def check_affiliation_control(
    field: str,
    value: Any,
    checks: Sequence[CheckResult],
    *,
    type_source: str,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> CheckResult:
    subject = next((c for c in checks if c.check_id == "AFFILIATION-SUBJECT-001"), None)
    expected_raw = next(
        (item.value for item in (subject.values if subject else []) if item.source == type_source),
        None,
    )
    expected_type = str(expected_raw) if expected_raw in {"PERSONAL", "COMPANY"} else None
    expected_label = {"PERSONAL": "个人", "COMPANY": "公司"}.get(expected_type)
    actual_type = normalize_subject_type(value, business=business)
    values = [CheckResultValue(source="申请页面字段", value=value)]
    if expected_label:
        values.append(CheckResultValue(source="主体关系核验", value=expected_label))

    if subject is None or subject.status != "MATCH":
        status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"] = (
            subject.status if subject else "INSUFFICIENT"
        )
        reason = "主体关系尚未通过，不能确定挂靠字段的正确取值"
    elif not expected_type:
        status = "INSUFFICIENT"
        reason = "主体关系已核验，但未得到明确的个人/公司类型"
    elif not actual_type:
        # 挂靠字段本身是主体关系规则的派生填写目标，不要求材料再次
        # 提供同名证据。主体关系已通过且类型明确时，空控件也视为规则通过，
        # 由页面写入链路负责填写“个人/公司”。
        status = "MATCH"
        reason = f"主体关系核验已通过，将自动填写{expected_label}"
    elif actual_type == expected_type:
        status = "MATCH"
        reason = "页面挂靠类型与主体关系核验结果一致"
    else:
        status = "CONFLICT"
        reason = "页面挂靠类型与主体关系核验结果不一致"

    return CheckResult(
        check_id=f"FIELD-{field}",
        label="挂靠类型",
        status=status,
        reason=reason,
        values=values,
        evidence=subject.evidence if subject else [],
    )
