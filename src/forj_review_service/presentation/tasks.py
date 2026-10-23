"""后端生成最终工作台任务；前端不合并状态或猜测遗漏能力。"""

from collections.abc import Sequence

from forj_review_service.businesses.packs.model import TaskGroupDeclaration
from forj_review_service.contracts.review import ReviewTask

# Business rule steps are namespaced by review_step_routing as
# ``BUSINESS-{check_id}``; keep the merge keys aligned with that public ID.
_BUSINESS_STEP_PREFIX = "BUSINESS-"


def rule_fields_for_merge(
    field_check_bindings: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """规则检查项 → 它要并进去的页面字段。

    合并关系**由业务声明给出**（`BusinessExtensionPack.field_check_bindings`）。
    写死一张表时，每来一个带日期类规则的业务都要回引擎里补一行；漏了不会报错，
    只会让结论单独成卡或者干脆看不见——审核员在字段下面找不到它。
    """
    return {f"{_BUSINESS_STEP_PREFIX}{check_id}": field for check_id, field in field_check_bindings}


def _merge(base: ReviewTask, members: Sequence[ReviewTask]) -> ReviewTask:
    all_tasks = [base, *members]
    severity = {"MATCH": 0, "INSUFFICIENT": 1, "CONFLICT": 2}
    status = max((item.result_status for item in all_tasks), key=severity.__getitem__)
    return base.model_copy(
        update={
            "result_status": status,
            "requires_reviewer_action": status != "MATCH",
            "reason_codes": list(
                dict.fromkeys(code for item in all_tasks for code in item.reason_codes)
            ),
            "reason": "；".join(dict.fromkeys(item.reason for item in all_tasks if item.reason)),
            "values": [value for item in all_tasks for value in item.values],
            "evidence": [value for item in all_tasks for value in item.evidence],
            "check_ids": list(dict.fromkeys(cid for item in all_tasks for cid in item.check_ids)),
        }
    )


def prepare_display_tasks(
    tasks: Sequence[ReviewTask],
    *,
    field_check_bindings: Sequence[tuple[str, str]] = (),
    task_groups: Sequence[TaskGroupDeclaration] = (),
    enabled_capabilities: Sequence[str] = (),
) -> list[ReviewTask]:
    # Field-first profiles keep the task in the assistant while exposing the
    # canonical DOM target through page_target_field. Date policy results must
    # merge against either representation so they never become duplicate
    # standalone assistant cards.
    by_field = {
        field: item
        for item in tasks
        if item.category == "FIELD"
        for field in (item.page_field or item.page_target_field,)
        if field
    }
    consumed: set[str] = set()
    bound_rules: dict[str, list[ReviewTask]] = {}
    rule_fields = rule_fields_for_merge(field_check_bindings)
    for task in tasks:
        field = rule_fields.get(task.step_id)
        if field and field in by_field:
            base = by_field[field]
            bound_rules.setdefault(base.step_id, []).append(task)
            consumed.add(task.step_id)
    replacements = {
        base.step_id: _merge(base, bound_rules[base.step_id])
        for base in by_field.values()
        if base.step_id in bound_rules
    }
    result = [
        replacements.get(item.step_id, item) for item in tasks if item.step_id not in consumed
    ]
    for group in task_groups:
        parent = next((item for item in result if item.step_id == group.parent_step_id), None)
        if group.parent_step_id and parent is None:
            continue
        members = [
            item
            for item in result
            if item is not parent
            and (
                item.step_id in group.member_step_ids
                or bool(group.member_prefixes)
                and item.step_id.startswith(group.member_prefixes)
            )
        ]
        if parent is None and not members and group.capability_id not in enabled_capabilities:
            continue
        if parent is not None:
            base = parent
        else:
            status = "MATCH" if members else "INSUFFICIENT"
            base = ReviewTask(
                step_id=group.step_id,
                sequence=min((item.sequence for item in members), default=10000),
                category="EXTERNAL",
                display_target="ASSISTANT",
                label=group.label,
                result_status=status,
                requires_reviewer_action=status != "MATCH",
                reason="" if members else f"未取得{group.label}结果",
            )
        merged = _merge(base, members)
        if group.original_status_detail:
            merged = merged.model_copy(
                update={
                    "details": {**merged.details, group.original_status_detail: base.result_status},
                }
            )
        consumed_ids = {item.step_id for item in members}
        if parent:
            consumed_ids.add(parent.step_id)
        result = [item for item in result if item.step_id not in consumed_ids]
        result.append(merged)
    return [
        item.model_copy(update={"sequence": i})
        for i, item in enumerate(sorted(result, key=lambda item: item.sequence), 1)
    ]
