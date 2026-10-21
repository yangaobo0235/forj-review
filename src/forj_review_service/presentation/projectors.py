"""Registered backend task projections selected by field declarations."""

from collections.abc import Sequence
from functools import partial
from types import MappingProxyType

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.businesses.rules.affiliation_controls import (
    check_affiliation_control,
    check_owner_type_input,
)
from forj_review_service.contracts.checks import CheckResult
from forj_review_service.contracts.review import (
    ReviewDisplayTarget,
    ReviewFieldSnapshot,
    ReviewTask,
)
from forj_review_service.presentation.task_factory import make_task


def _owner_type_system_step(
    snapshot: ReviewFieldSnapshot,
    checks: Sequence[CheckResult],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> ReviewTask:
    """Treat the backend-provided owner type as input, not OCR evidence."""

    check = check_owner_type_input(snapshot.field, snapshot.value, business=business)
    return make_task(
        step_id=check.check_id,
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=snapshot.label,
        result_status=check.status,
        reason=check.reason,
        page_value=snapshot.value,
        control_type=snapshot.control_type,
        writable=False,
        values=check.values,
    )


def _affiliation_control_step(
    snapshot: ReviewFieldSnapshot,
    checks: Sequence[CheckResult],
    *,
    type_source: str,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> ReviewTask:
    check = check_affiliation_control(
        snapshot.field,
        snapshot.value,
        checks,
        type_source=type_source,
        business=business,
    )
    return make_task(
        step_id=check.check_id,
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=snapshot.label,
        result_status=check.status,
        reason=check.reason,
        page_target_field=snapshot.field,
        page_value=snapshot.value,
        control_type=snapshot.control_type,
        writable=False,
        values=check.values,
        evidence=check.evidence,
    )


FIELD_TASK_PROJECTORS = MappingProxyType(
    {
        "owner_type": _owner_type_system_step,
        "old_vehicle_affiliation": partial(_affiliation_control_step, type_source="旧车主体类型"),
        "new_vehicle_affiliation": partial(_affiliation_control_step, type_source="新车主体类型"),
    }
)


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
