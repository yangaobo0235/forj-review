"""Registered backend task projections selected by field declarations."""

from collections.abc import Sequence
from functools import partial
from types import MappingProxyType
from typing import Literal

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.rules.affiliation import normalize_page_owner_type
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.review import (
    ReviewDisplayTarget,
    ReviewFieldSnapshot,
    ReviewTask,
)
from forj_review_assistant.presentation.task_factory import make_task


def _owner_type_system_step(
    snapshot: ReviewFieldSnapshot, checks: Sequence[CheckResult],
) -> ReviewTask:
    """Treat the backend-provided owner type as input, not OCR evidence."""

    valid = normalize_page_owner_type(snapshot.value) is not None
    return make_task(
        step_id=f"FIELD-{snapshot.field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=snapshot.label,
        result_status="MATCH" if valid else "INSUFFICIENT",
        reason=(
            "系统业务字段，无需材料比对"
            if valid
            else "车辆所有人类型为空或不是个人/企业，请人工确认"
        ),
        page_value=snapshot.value,
        control_type=snapshot.control_type,
        writable=False,
        values=[CheckResultValue(source="申请页面字段", value=snapshot.value)],
    )


def _affiliation_control_step(
    snapshot: ReviewFieldSnapshot,
    checks: Sequence[CheckResult],
    *,
    type_source: str,
) -> ReviewTask:
    subject = next((c for c in checks if c.check_id == "AFFILIATION-SUBJECT-001"), None)
    expected_raw = next(
        (
            item.value
            for item in (subject.values if subject else [])
            if item.source == type_source
        ),
        None,
    )
    expected_type = str(expected_raw) if expected_raw in {"PERSONAL", "COMPANY"} else None
    expected_label = {"PERSONAL": "个人", "COMPANY": "公司"}.get(expected_type)
    actual_type = normalize_page_owner_type(snapshot.value)
    values = [CheckResultValue(source="申请页面字段", value=snapshot.value)]
    if expected_label:
        values.append(CheckResultValue(source="主体关系核验", value=expected_label))

    if subject is None or subject.status != "MATCH":
        status: Literal["MATCH", "CONFLICT", "INSUFFICIENT"] = subject.status if subject else "INSUFFICIENT"
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

    return make_task(
        step_id=f"FIELD-{snapshot.field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=snapshot.label,
        result_status=status,
        reason=reason,
        page_target_field=snapshot.field,
        page_value=snapshot.value,
        control_type=snapshot.control_type,
        writable=False,
        values=values,
        evidence=subject.evidence if subject else [],
    )


FIELD_TASK_PROJECTORS = MappingProxyType({
    "owner_type": _owner_type_system_step,
    "old_vehicle_affiliation": partial(_affiliation_control_step, type_source="旧车主体类型"),
    "new_vehicle_affiliation": partial(_affiliation_control_step, type_source="新车主体类型"),
})


def project_field_task(
    profile: BusinessProfile,
    snapshot: ReviewFieldSnapshot,
    checks: Sequence[CheckResult],
) -> ReviewTask | None:
    pack = profile.pack_declaration
    field = next((f for f in pack.fields if f.key == snapshot.field), None) if pack else None
    projector_id = field.task_projector if field else ""
    if not projector_id:
        return None
    if projector_id not in FIELD_TASK_PROJECTORS:
        raise ValueError(f"未注册的字段任务投影：{projector_id}")
    return FIELD_TASK_PROJECTORS[projector_id](snapshot, checks)
