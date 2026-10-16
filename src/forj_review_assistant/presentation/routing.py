"""字段优先的审核步骤集中路由。

主要职责：一次运行内决定每个审核步骤展示在宿主页面字段还是助手面板，
并完成规则投影、任务去重和步骤排序。展示目标只来自本模块的显式映射表，
不根据中文 label 或 reason 猜测；页内交互只对目标 Profile 启用，
未配置业务继续使用结果界面。
"""

from collections.abc import Sequence
from typing import Literal

from forj_review_assistant.businesses.field_policies import field_policy
from forj_review_assistant.businesses.fields import PAGE_FIELD_LABELS
from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.decisions import ReasonCode, default_reason_codes
from forj_review_assistant.contracts.recognition import MaterialCompletenessReport
from forj_review_assistant.contracts.review import (
    EvidenceFact,
    FieldComparison,
    FieldStatus,
    ReviewDisplayTarget,
    ReviewFieldSnapshot,
    ReviewRequest,
    ReviewTask,
)
from forj_review_assistant.evidence.comparison.aggregate import PAGE_VALUE_SOURCE
from forj_review_assistant.evidence.comparison.composite_fields import (
    COMPOSITE_FIELDS_BY_MEMBER,
    PAGE_FIELD_COMPOSITES,
    build_page_composite_checks,
)
from forj_review_assistant.evidence.fields.normalize import normalize_value
from forj_review_assistant.presentation.advice import FIELD_LABELS
from forj_review_assistant.presentation.projectors import project_field_task


def profile_uses_page_interaction(profile: BusinessProfile) -> bool:
    """是否启用页内交互，由 Profile 自己声明。"""
    return profile.page_interaction


MISSING_PAGE_FIELD_REASON = "页面字段缺失或存在歧义，请人工核对该字段"

Category = Literal["FIELD", "EXTERNAL", "BUSINESS_RULE"]
ResultStatus = Literal["MATCH", "CONFLICT", "INSUFFICIENT"]

# 规则把页面侧取值交给任务装配时使用的 `details` 键，见 `_business_step`。
PAGE_VALUES_DETAIL_KEY = "page_values"
# 合并多个来源的状态时的严重度顺序：冲突最重，一致最轻。
SEVERITY = {"MATCH": 0, "INSUFFICIENT": 1, "CONFLICT": 2}
# 规则结论投影成字段条目时的结论文字；详细过程在 reason 和页面外核验里。
CONCLUSION_TEXT = {"MATCH": "一致", "CONFLICT": "不一致", "INSUFFICIENT": "待复核"}


def _evidence_source(item: EvidenceFact | dict[str, object]) -> str:
    """兼容历史业务规则返回 dict evidence 和新 EvidenceFact 模型。"""
    if isinstance(item, dict):
        return str(item.get("source") or "未知来源")
    return item.source


def _coerce_evidence(item: EvidenceFact | dict[str, object]) -> EvidenceFact:
    """Normalize legacy business-rule evidence dictionaries for the API model."""
    return item if isinstance(item, EvidenceFact) else EvidenceFact.model_validate(item)


def _step(
    *,
    step_id: str,
    category: Category,
    display_target: ReviewDisplayTarget,
    label: str,
    result_status: ResultStatus,
    reason: str,
    reason_codes: Sequence[ReasonCode] = (),
    page_field: str | None = None,
    page_target_field: str | None = None,
    page_target_fields: Sequence[str] = (),
    page_value: object | None = None,
    page_values: Sequence[CheckResultValue] = (),
    control_type: str | None = None,
    writable: bool = False,
    values: Sequence[CheckResultValue] = (),
    evidence: Sequence[EvidenceFact] = (),
    details: dict[str, object] | None = None,
    requires_reviewer_action: bool | None = None,
    evidence_mode: str | None = None,
    not_found_sources: Sequence[str] = (),
    normalized_page_value: str | None = None,
) -> ReviewTask:
    """构建单个步骤；MATCH 不需要人工处理，其余状态必须处理。"""
    resolved_details = details or {}
    return ReviewTask(
        step_id=step_id,
        check_ids=[step_id],
        sequence=1,
        category=category,
        display_target=display_target,
        page_field=page_field if display_target is ReviewDisplayTarget.PAGE_FIELD else None,
        page_target_field=page_target_field,
        page_target_fields=list(page_target_fields),
        page_value=page_value,
        page_values=list(page_values),
        control_type=control_type,
        writable=writable,
        requires_reviewer_action=(result_status != "MATCH") if requires_reviewer_action is None else requires_reviewer_action,
        label=label,
        result_status=result_status,
        reason=reason,
        reason_codes=list(reason_codes) or default_reason_codes(result_status),
        values=[item.model_copy(update={
            "comparison_status": item.comparison_status or (
                "INSUFFICIENT" if item.uncertain else result_status
                if item.conflicting is not None or item.differences else None
            ),
        }) for item in values],
        evidence=[_coerce_evidence(item) for item in evidence],
        details=resolved_details,
        evidence_count=len(evidence),
        evidence_sources=list(dict.fromkeys(_evidence_source(item) for item in evidence)),
        evidence_mode=evidence_mode,
        not_found_sources=list(not_found_sources),
        normalized_page_value=normalized_page_value,
    )


def _comparison_status(comparison: FieldComparison) -> ResultStatus:
    if comparison.status is FieldStatus.MATCH:
        return "MATCH"
    if comparison.status is FieldStatus.CONFLICT:
        return "CONFLICT"
    return "INSUFFICIENT"


def _page_field_collected(request: ReviewRequest, page_field: str) -> bool:
    """只有页面成功采集且无歧义的字段才能作为展示目标。"""
    if page_field in request.collection_diagnostics.ambiguous_fields:
        return False
    return request.page_fields.get(page_field) not in (None, "")


def _field_step(
    request: ReviewRequest,
    comparison: FieldComparison,
    *,
    page_interaction: bool,
) -> ReviewTask:
    label = FIELD_LABELS.get(comparison.field, "材料字段核验")
    values = [
        CheckResultValue(
            source=item.source,
            differences=item.differences,
            comparison_status=item.comparison_status,
            value=item.value,
            source_id=item.source_id,
            image_id=item.image_id,
            image_index=item.image_index,
            document_type=item.document_type,
            detail=item.detail,
            derived_from=item.derived_from,
            evidence_region=item.evidence_region,
            uncertain=item.uncertain,
            group_title=item.group_title,
        )
        for item in comparison.evidence
        if item.value not in (None, "")
    ]
    result_status = _comparison_status(comparison)
    material_evidence_by_image: dict[tuple[str, str | None], EvidenceFact] = {}
    for item in comparison.evidence:
        image_identity = item.image_id or (
            f"index:{item.image_index}" if item.image_index is not None else item.source_id
        )
        if image_identity and item.source == "图片识别":
            material_evidence_by_image.setdefault((image_identity, item.source_field), item)
    material_evidence = list(material_evidence_by_image.values())
    policy = field_policy(comparison.field)
    if (
        result_status == "MATCH" and (policy is None or policy.page_evidence_required)
        and not _page_field_collected(request, comparison.field)
    ):
        result_status = "INSUFFICIENT"
        reason = f"{comparison.message}；{MISSING_PAGE_FIELD_REASON}"
    else:
        reason = comparison.message
    # 数电发票票面上没有「发票代码」这一栏，值是从数电号码派生的。不说清楚，
    # 审核员会照着这一栏去原图上找，找不到。派生关系由材料声明给出，这里
    # 不看字段名——报废置换和过户的字段键并不相同。
    if any(item.derived_from for item in comparison.evidence):
        reason = f"{reason}；{label}由票面数电号码适配"
    # 报废置换字段全部在审核助手中展示；原页面保持干净，不再注入标记。
    # 页面是否采集、页面原值和材料证据都由 evidence/values 传给工作台处理。
    return _step(
        step_id=f"FIELD-{comparison.field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=label,
        result_status=result_status,
        reason=reason,
        reason_codes=(["PAGE_VALUE_MISSING"] if result_status != _comparison_status(comparison)
                      else comparison.reason_codes),
        page_target_field=comparison.field if page_interaction else None,
        writable=page_interaction,
        page_value=request.page_fields.get(comparison.field),
        values=values,
        evidence=material_evidence,
        requires_reviewer_action=(result_status != "MATCH"),
        details={
            "evidence_count": len({image for image, _ in material_evidence_by_image}),
            "evidence_mode": policy.mode if policy else None,
            "evidence_sources": list(dict.fromkeys(_evidence_source(item) for item in material_evidence)),
            "not_found_sources": list(policy.allowed_document_types) if policy and not material_evidence else [],
            "normalized_page_value": normalize_value(comparison.field, comparison.right_value),
            "differences": comparison.differences,
        },
        evidence_mode=policy.mode if policy else None,
        not_found_sources=policy.allowed_document_types if policy and not material_evidence else (),
        normalized_page_value=normalize_value(comparison.field, comparison.right_value),
    )


def _page_catalog_step(snapshot: ReviewFieldSnapshot, step_id: str, profile: BusinessProfile) -> ReviewTask:
    """为没有已配置核验来源的实际页面控件保留可审查步骤。

    只有声明了 `review_declared_fields_only` 的业务不生成这类条目——那类业务
    的审核字段就是声明过的字段，页面上其余控件直接不进审核目录。
    """
    has_value = snapshot.value not in (None, "")
    declared = profile.pack_declaration and any(
        snapshot.label.strip() in (declaration.label, *declaration.aliases)
        for declaration in profile.pack_declaration.fields
        if declaration.reviewable
    )
    return _step(
        step_id=step_id,
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=snapshot.label,
        result_status="INSUFFICIENT",
        reason=(
            "未能关联此字段的页面值，请重新采集后复核"
            if declared and not snapshot.field
            else "未取得此字段的材料核验结果，请查看原图人工核对"
            if declared
            else "当前材料和规则未配置该页面控件的核验来源，请人工核对"
            if has_value
            else "当前页面控件未填写，且没有已配置的材料核验来源，请人工核对"
        ),
        page_value=snapshot.value,
        page_target_field=snapshot.field,
        control_type=snapshot.control_type,
        writable=False,
        values=[CheckResultValue(source="申请页面字段", value=snapshot.value)],
    )


def _is_review_scope_field(profile: BusinessProfile, field: str | None) -> bool:
    """该页面控件是否属于本业务的审核目录。

    默认所有页面控件都留在目录里（未配置来源时提示人工核对）；声明了
    `review_declared_fields_only` 的业务只保留自己声明过的字段。
    """
    if not profile.review_declared_fields_only:
        return True
    if profile.pack_declaration:
        declaration = profile.pack_declaration.field(field)
        return bool(declaration and declaration.reviewable)
    return bool(field) and field in PAGE_FIELD_LABELS


def _composite_field_step(
    request: ReviewRequest,
    check: CheckResult,
    *,
    page_interaction: bool,
) -> ReviewTask:
    """Render one task for a pair of page controls with one material value."""

    spec = next(item for item in PAGE_FIELD_COMPOSITES if item.check_id == check.check_id)
    page_values = [
        CheckResultValue(source=spec.primary_label, value=request.page_fields.get(spec.primary_field)),
        CheckResultValue(source=spec.secondary_label, value=request.page_fields.get(spec.secondary_field)),
    ]
    material_values = [item for item in check.values if item.source not in {
        spec.primary_field,
        spec.secondary_field,
    }]
    return _step(
        step_id=check.check_id,
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=check.label,
        result_status=check.status,
        reason=check.reason,
        reason_codes=check.reason_codes,
        page_target_field=spec.primary_field if page_interaction else None,
        page_target_fields=(spec.primary_field, spec.secondary_field) if page_interaction else (),
        page_value=request.page_fields.get(spec.primary_field),
        page_values=page_values,
        writable=page_interaction,
        values=material_values,
        evidence=[item for item in check.evidence if item.source != "申请页面字段"],
        details={
            **check.details,
            "comparison_basis": "PAGE_PAIR_THEN_MATERIAL",
            "paired_page_fields": [spec.primary_field, spec.secondary_field],
            "material_field": spec.material_field,
        },
        requires_reviewer_action=check.status != "MATCH",
    )


def _check_backed_field_step(
    request: ReviewRequest,
    field: str,
    checks: Sequence[CheckResult],
) -> ReviewTask:
    """把声明过的规则结论投影为实际 DOM 字段目录中的字段项。

    一个字段可以绑定多条检查（如车源的「车型」绑定马力、整车型号、排放标准
    三条）。字段条目展示的是**每条检查比了什么、结果如何**，不是规则的原始
    输入：把功率千瓦数、型号字符串之类的中间值堆进来，审核员只会看到一堆
    无法解释的数字。

    每条检查用 `PAGE_VALUE_SOURCE` 标出自己的页面侧取值。页面侧摆进
    「页面原始值」区块，材料侧摆进「材料提取值」区块，两侧用同一条检查的
    标签命名，审核员据此一一对应，而不是只看三行「一致」。

    每条检查自己的比对说明同时写进候选值的 `check_reason`，工作台会把它放进
    对应的候选框里。车型这种多结论字段只有把说明和值放在一起才读得懂。
    """
    status = max(
        (item.status for item in checks),
        key=lambda value: SEVERITY[value],
    )
    reason = "；".join(dict.fromkeys(item.reason for item in checks if item.reason))
    page_text = request.page_fields.get(field)
    # 理由是否已经逐条搬进候选框。整段理由和框里的说明同时出现是重复信息；
    # 但只要理由里多出检查没写过的一句（页面字段缺失），那一句就没有别的地方
    # 可看，整段必须留着。
    reason_distributed = True
    if status == "MATCH" and not _page_field_collected(request, field):
        status = "INSUFFICIENT"
        reason = f"{reason}；{MISSING_PAGE_FIELD_REASON}"
        reason_distributed = False
    label = PAGE_FIELD_LABELS.get(field, field)
    page_values = [CheckResultValue(source=f"{label}原文", value=page_text)]
    page_values.extend(
        # 页面侧改用检查自己的标签命名，与材料侧一一对应；`PAGE_VALUE_SOURCE`
        # 只是检查内部用来区分两侧的标记，不是展示名。
        CheckResultValue(source=check.label, value=value.value)
        for check in checks
        for value in check.values
        if value.source == PAGE_VALUE_SOURCE
    )
    return _step(
        step_id=f"FIELD-{field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=label,
        result_status=status,
        reason=reason,
        reason_codes=(["PAGE_VALUE_MISSING"] if not reason_distributed
                      else list(dict.fromkeys(code for check in checks for code in check.reason_codes))),
        page_target_field=field,
        page_value=page_text,
        page_values=page_values,
        # 每条检查的比对说明贴到它自己的候选值上。车型一个条目下挂着五六条
        # 候选，整段理由读下来才知道哪一条对不上；句子跟值放在一起，审核员
        # 在一个框里就能看完。理由本身仍然完整保留，供列表和别处使用。
        values=[
            value.model_copy(update={"check_reason": check.reason,
                                     "comparison_status": value.comparison_status or (
                                         "INSUFFICIENT" if value.uncertain else check.status),
                                     })
            for check in checks
            for value in check.values
            if value.source != PAGE_VALUE_SOURCE
        ],
        evidence=[item for check in checks for item in check.evidence],
        details={
            "check_ids": [item.check_id for item in checks],
            "reason_distributed": reason_distributed,
        },
    )


def _with_control_snapshot(
    step: ReviewTask,
    snapshot: ReviewFieldSnapshot,
    *,
    allow_write: bool,
    profile: BusinessProfile,
) -> ReviewTask:
    label = snapshot.label
    return step.model_copy(
        update={
            "label": label,
            "page_value": snapshot.value,
            "control_type": snapshot.control_type,
            # 三层都满足才给出回填按钮：业务注册了写回动作、控件本身可写、
            # 控件类型在业务声明里放开过。任何一层不满足都不显示按钮，避免
            # 审核员点出一个必然失败或写不进去的操作。
            "writable": (
                allow_write
                and snapshot.editable
                and profile.allows_field_write(snapshot.control_type)
            ),
        }
    )


def _field_first_steps(
    request: ReviewRequest,
    profile: BusinessProfile,
    comparisons: Sequence[FieldComparison],
    business_checks: Sequence[CheckResult],
    *,
    writable: bool,
) -> list[ReviewTask]:
    """字段优先工作台：按页面字段目录出条目，字段缺失时降级人工复核。"""
    comparisons_by_field = {item.field: item for item in comparisons}
    checks_by_id = {item.check_id: item for item in business_checks}
    # 页面字段 → 投影到它上面的规则结论。一个字段可以绑定多条检查，
    # 按声明顺序合并成一条字段条目。
    projected_by_field: dict[str, list[CheckResult]] = {}
    for check_id, field in profile.field_check_bindings:
        check = checks_by_id.get(check_id)
        if check is not None:
            projected_by_field.setdefault(field, []).append(check)

    if not request.review_fields:
        composite_checks = {
            item.check_id: item
            for item in business_checks
            if any(item.check_id == spec.check_id for spec in PAGE_FIELD_COMPOSITES)
        }
        result: list[ReviewTask] = []
        emitted: set[str] = set()
        for comparison in comparisons:
            composite = COMPOSITE_FIELDS_BY_MEMBER.get(comparison.field)
            if composite is None:
                result.append(_field_step(request, comparison, page_interaction=writable))
                continue
            check = composite_checks.get(composite.check_id)
            if check is not None and composite.check_id not in emitted:
                result.append(_composite_field_step(request, check, page_interaction=writable))
                emitted.add(composite.check_id)
        # 没有 DOM 目录时也要给出投影字段的条目：同一份响应不能因为这一轮
        # 采集是否成功而少一行字段。
        result.extend(
            _check_backed_field_step(request, field, checks)
            for field, checks in projected_by_field.items()
            if field not in comparisons_by_field
        )
        return result

    composite_checks = {
        item.check_id: item
        for item in business_checks
        if any(item.check_id == spec.check_id for spec in PAGE_FIELD_COMPOSITES)
    }
    mapped_counts: dict[str, int] = {}
    for snapshot in request.review_fields:
        if snapshot.field:
            mapped_counts[snapshot.field] = mapped_counts.get(snapshot.field, 0) + 1

    snapshots_by_field = {
        item.field: item
        for item in request.review_fields
        if item.field and mapped_counts.get(item.field) == 1
    }
    steps: list[ReviewTask] = []
    emitted_composites: set[str] = set()
    for index, snapshot in enumerate(
        sorted(request.review_fields, key=lambda item: item.order), start=1
    ):
        # 只读日期等控件仍然是需要核验的页面字段；只有无法映射语义的
        # 只读控件才跳过。editable 仅决定是否允许页面回写。
        if not snapshot.editable and not snapshot.field:
            continue
        field = snapshot.field if mapped_counts.get(snapshot.field or "") == 1 else None
        composite = COMPOSITE_FIELDS_BY_MEMBER.get(field or "")
        if composite is not None:
            check = composite_checks.get(composite.check_id)
            if check is not None and composite.check_id not in emitted_composites:
                paired_snapshots = [
                    snapshots_by_field.get(composite.primary_field),
                    snapshots_by_field.get(composite.secondary_field),
                ]
                step = _composite_field_step(
                    request,
                    check,
                    page_interaction=writable,
                ).model_copy(update={
                    "writable": writable and all(
                        item is not None and item.editable for item in paired_snapshots
                    ),
                    "control_type": "group",
                })
                steps.append(step)
                emitted_composites.add(composite.check_id)
            continue
        projection_snapshot = (snapshot.model_copy(update={"value": None})
                               if field in request.collection_diagnostics.ambiguous_fields else snapshot)
        projected_task = project_field_task(profile, projection_snapshot, business_checks, _step) if field else None
        if projected_task is not None:
            steps.append(projected_task)
            continue
        comparison = comparisons_by_field.get(field)
        projected = projected_by_field.get(field or "")
        if comparison is not None:
            steps.append(
                _with_control_snapshot(
                    _field_step(request, comparison, page_interaction=writable),
                    snapshot,
                    allow_write=writable,
                    profile=profile,
                )
            )
        elif projected:
            steps.append(
                _with_control_snapshot(
                    _check_backed_field_step(request, field, projected),
                    snapshot,
                    allow_write=writable,
                    profile=profile,
                )
            )
        elif _is_review_scope_field(profile, field):
            step_id = f"FIELD-{field}" if field else f"FIELD-DOM-{index}"
            steps.append(_page_catalog_step(snapshot, step_id, profile))
    # Material-only fields have no DOM control; retain their results alongside
    # page fields instead of dropping them when a DOM catalogue is available.
    emitted_step_ids = {step.step_id for step in steps}
    steps.extend(
        _field_step(request, comparison, page_interaction=False)
        for comparison in comparisons
        if f"FIELD-{comparison.field}" not in emitted_step_ids
        and comparison.field not in COMPOSITE_FIELDS_BY_MEMBER
        and (policy := field_policy(comparison.field)) is not None
        and not policy.page_evidence_required
    )
    return steps


def _external_step(check: CheckResult) -> ReviewTask:
    return _step(
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
    return _step(
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
    page_interaction = profile_uses_page_interaction(profile)
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
        steps.extend(_field_first_steps(
            request,
            profile,
            comparisons,
            effective_business_checks,
            writable=bool(profile.enabled_page_actions),
        ))
    else:
        steps.extend(
            _field_step(request, comparison, page_interaction=False)
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
    from forj_review_assistant.presentation.tasks import prepare_display_tasks

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
