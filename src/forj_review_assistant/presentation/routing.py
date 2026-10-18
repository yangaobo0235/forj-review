"""合并字段、外部核验与业务规则任务，并应用统一的展示和写回策略。"""

from collections.abc import Sequence

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.recognition import MaterialCompletenessReport
from forj_review_assistant.contracts.review import (
    FieldComparison,
    ReviewDisplayTarget,
    ReviewRequest,
    ReviewTask,
)
from forj_review_assistant.evidence.comparison.composite_fields import (
    PAGE_FIELD_COMPOSITES,
    build_page_composite_checks,
)
from forj_review_assistant.presentation.field_tasks import field_task, field_tasks
from forj_review_assistant.presentation.task_factory import make_task
from forj_review_assistant.presentation.tasks import prepare_display_tasks

PAGE_VALUES_DETAIL_KEY = "page_values"

def _external_step(check: CheckResult) -> ReviewTask:
    return make_task(
        step_id=check.check_id,
        category="EXTERNAL",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=check.label,
        result_status=check.status,
        reason=check.reason,
        reason_codes=check.reason_codes,
        values=check.values,
        evidence=check.evidence,
    )


def _business_step(
    request: ReviewRequest,
    check: CheckResult,
    *,
    page_interaction: bool,
) -> ReviewTask:
    # 规则可以把自己比对的**页面侧取值**放在 `details["page_values"]` 里，
    # 工作台据此多摆一个「页面原始值」区块（新旧车所有人一致性要审核员先看到
    # 页面比出了什么，再看材料）。放在 details 里而不是 values 里，是为了不
    # 让页面值混进「材料提取值」列表。取走之后 details 不再保留这一项。
    details = dict(check.details)
    page_values = [
        CheckResultValue.model_validate(item)
        for item in details.pop(PAGE_VALUES_DETAIL_KEY, [])
    ]
    return make_task(
        step_id=f"BUSINESS-{check.check_id}",
        category="BUSINESS_RULE",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=check.label,
        result_status=check.status,
        reason=check.reason,
        reason_codes=check.reason_codes,
        page_values=page_values,
        values=check.values,
        evidence=check.evidence,
        details=details,
    )


def _deduplicate_steps(steps: Sequence[ReviewTask]) -> list[ReviewTask]:
    seen: set[str] = set()
    result: list[ReviewTask] = []
    for step in steps:
        if step.step_id not in seen:
            seen.add(step.step_id)
            result.append(step)
    return result


def _resequence(steps: Sequence[ReviewTask]) -> list[ReviewTask]:
    return [
        step.model_copy(update={"sequence": sequence})
        for sequence, step in enumerate(steps, start=1)
    ]


def build_review_tasks(
    *,
    request: ReviewRequest,
    profile: BusinessProfile,
    comparisons: Sequence[FieldComparison],
    external_checks: Sequence[CheckResult],
    business_checks: Sequence[CheckResult],
    completeness: MaterialCompletenessReport | None,
    limitations: Sequence[str],
) -> list[ReviewTask]:
    """一次返回完整、有序、带展示目标的审核步骤列表。"""
    page_interaction = profile.page_interaction
    # 组合字段是页面原始值与材料值之间的公开协议。工作流会提前生成
    # 这些检查，但路由函数也必须自洽，以保证重放、测试和其他调用方不会
    # 因遗漏 synthetic check 而退化成两个独立字段任务。
    comparison_by_field = {item.field: item for item in comparisons}
    generated_composites = build_page_composite_checks(request, comparison_by_field)
    business_checks_by_id = {item.check_id: item for item in business_checks}
    business_checks_ordered = list(business_checks)
    for generated in generated_composites:
        if generated.check_id in business_checks_by_id:
            index = next(
                index
                for index, item in enumerate(business_checks_ordered)
                if item.check_id == generated.check_id
            )
            business_checks_ordered[index] = generated
        else:
            business_checks_ordered.append(generated)
        business_checks_by_id[generated.check_id] = generated
    effective_business_checks = tuple(business_checks_ordered)
    steps: list[ReviewTask] = []
    # PAGE_FIELD 只用于目标 Profile 且请求中存在该规范字段；
    # 缺失字段转为 ASSISTANT + INSUFFICIENT。
    # 外部规则和派生规则使用 ASSISTANT。
    # 材料完整性属于输入诊断，不生成展示任务；报告和原始检查仍由
    # 工作流及最终建议保留。completeness / limitations 参数兼容旧调用方。
    if page_interaction:
        # 没有注册页面动作的业务只展示结论，不提供回填按钮：写在未注册动作上
        # 的回填必然失败，把按钮禁掉比让审核员点出一个错误提示更诚实。
        steps.extend(field_tasks(
            request,
            profile,
            comparisons,
            effective_business_checks,
            writable=bool(profile.enabled_page_actions),
        ))
    else:
        steps.extend(
            field_task(request, comparison, page_interaction=False)
            for comparison in comparisons
        )
    # A capability may already emit a synthetic insufficiency check while a
    # legacy external registry also returns one. Keep one reviewer task per
    # external capability/check identity to avoid duplicate panel entries.
    seen_external: set[tuple[str, str]] = set()
    for check in external_checks:
        key = (check.check_id.removeprefix("EXTERNAL-"), check.label)
        if key in seen_external:
            continue
        seen_external.add(key)
        steps.append(_external_step(check))
    # 已经投影进字段条目的结论不再单出自己的任务：同一句话在字段行里已经
    # 完整展示（含页面侧、材料侧和逐条说明），再出一张卡片就是让审核员在
    # 「待处理」里看到同一条结论两遍。只有投影**成功**时才抑制——字段控件
    # 不在页面上时字段条目根本出不来，那几条结论就只剩这张卡片可看。
    projected_check_ids = {
        check_id
        for step in steps
        if step.category == "FIELD"
        for check_id in step.details.get("check_ids", ())
    }
    steps.extend(
        _business_step(request, check, page_interaction=page_interaction)
        for check in effective_business_checks
        if check.check_id not in {
            spec.check_id for spec in PAGE_FIELD_COMPOSITES
        }
        and check.check_id not in projected_check_ids
        and check.check_id != "MATERIAL-COMPLETENESS"
    )
    tasks = prepare_display_tasks(
        _resequence(_deduplicate_steps(steps)),
        task_groups=profile.pack_declaration.presentation.groups if profile.pack_declaration else (),
        enabled_capabilities=tuple(spec.capability_id for spec in profile.capabilities),
        field_check_bindings=profile.field_check_bindings,
    ) if page_interaction else _resequence(_deduplicate_steps(steps))
    pack = profile.pack_declaration
    if pack is None:
        return tasks
    policy = pack.presentation
    writable_fields = set(pack.writable_field_keys())
    result = []
    for task in tasks:
        targets = task.page_target_fields or [task.page_target_field or task.page_field]
        result.append(task.model_copy(update={
            "writable": task.writable and all(target in writable_fields for target in targets),
            "visible": not (
                (task.page_field or task.page_target_field) in policy.hidden_fields
                or (task.category != "FIELD" and task.step_id.removeprefix("BUSINESS-").startswith(policy.hidden_check_prefixes))
            ),
        }))
    return result
