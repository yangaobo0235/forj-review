"""后端生成最终工作台任务；前端不合并状态或猜测遗漏能力。"""

from collections.abc import Sequence

from forj_review_assistant.contracts.review import ReviewTask

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
    return {
        f"{_BUSINESS_STEP_PREFIX}{check_id}": field
        for check_id, field in field_check_bindings
    }


def _merge(base: ReviewTask, members: Sequence[ReviewTask]) -> ReviewTask:
    all_tasks = [base, *members]
    severity = {"MATCH": 0, "INSUFFICIENT": 1, "CONFLICT": 2}
    status = max((item.result_status for item in all_tasks), key=severity.__getitem__)
    return base.model_copy(update={
        "result_status": status,
        "requires_reviewer_action": status != "MATCH",
        "reason": "；".join(dict.fromkeys(item.reason for item in all_tasks if item.reason)),
        "values": [value for item in all_tasks for value in item.values],
        "evidence": [value for item in all_tasks for value in item.evidence],
        "check_ids": list(dict.fromkeys(cid for item in all_tasks for cid in item.check_ids)),
    })


def prepare_display_tasks(
    tasks: Sequence[ReviewTask], *, qr_enabled: bool,
    field_check_bindings: Sequence[tuple[str, str]] = (),
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
        for base in by_field.values() if base.step_id in bound_rules
    }
    subject = next((item for item in tasks if item.step_id == "BUSINESS-AFFILIATION-SUBJECT-001"), None)
    if subject:
        auxiliary = [item for item in tasks if item.step_id.startswith("BUSINESS-AFFILIATION-AUX-")]
        replacements[subject.step_id] = _merge(replacements.get(subject.step_id, subject), auxiliary).model_copy(update={
            "details": {**subject.details, "affiliation_subject_status": subject.result_status},
        })
        consumed.update(item.step_id for item in auxiliary)
    result = []
    qr_members = [
        item for item in tasks
        if item.step_id.startswith("QR-") or item.step_id == "EXTERNAL-scrap_certificate_qr"
    ]
    if qr_enabled or qr_members:
        consumed.update(item.step_id for item in qr_members)
        status = "MATCH" if qr_members else "INSUFFICIENT"
        base = ReviewTask(
            step_id="QR-GROUP", sequence=min((item.sequence for item in qr_members), default=10000),
            category="EXTERNAL", display_target="ASSISTANT", label="二维码官网核验",
            result_status=status, requires_reviewer_action=status != "MATCH",
            reason="" if qr_members else "未取得可核验的二维码",
        )
        result.append(_merge(base, qr_members))
    result.extend(replacements.get(item.step_id, item) for item in tasks if item.step_id not in consumed)
    return [item.model_copy(update={"sequence": i}) for i, item in enumerate(sorted(result, key=lambda item: item.sequence), 1)]
