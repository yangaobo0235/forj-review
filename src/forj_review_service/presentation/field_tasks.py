"""按页面字段目录组装比较、规则投影和缺失控件任务。"""

from collections.abc import Sequence

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.field_policies import field_policy
from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.contracts.checks import CheckResult, CheckResultValue
from forj_review_service.contracts.review import (
    EvidenceFact,
    FieldComparison,
    FieldStatus,
    ReviewDisplayTarget,
    ReviewFieldSnapshot,
    ReviewRequest,
    ReviewTask,
)
from forj_review_service.evidence.comparison.aggregate import PAGE_VALUE_SOURCE
from forj_review_service.evidence.fields.normalize import normalize_value
from forj_review_service.presentation.projectors import project_field_task
from forj_review_service.presentation.task_factory import ResultStatus, make_task

MISSING_PAGE_FIELD_REASON = "页面字段缺失或存在歧义，请重新采集后人工核对该字段"
SEVERITY = {"MATCH": 0, "INSUFFICIENT": 1, "CONFLICT": 2}


def _display_value(
    field: str,
    value: object,
    *,
    business: BusinessContext,
) -> object:
    """Use the page vocabulary for values that have a declared flag normalizer.

    The evidence object keeps the registration's original text (for example,
    ``福田牌``) so the reviewer can inspect what was read.  The candidate shown
    in the workbench is also the value sent by the fill action, so a field such
    as ``是否解放牌`` must use its normalized ``是``/``否`` value here.
    """
    policy = field_policy(field, business=business)
    if policy is not None and policy.normalizer == "jiefang_flag":
        return normalize_value(field, value, business=business) or value
    return value


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


def field_task(
    request: ReviewRequest,
    comparison: FieldComparison,
    *,
    page_interaction: bool,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> ReviewTask:
    label = business.labels.get(comparison.field, "材料字段核验")
    values = [
        CheckResultValue(
            source=item.source,
            differences=item.differences,
            comparison_status=item.comparison_status,
            value=_display_value(comparison.field, item.value, business=business),
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
    policy = field_policy(comparison.field, business=business)
    if (
        result_status == "MATCH"
        and (policy is None or policy.page_evidence_required)
        and not _page_field_collected(request, comparison.field)
    ):
        result_status = "INSUFFICIENT"
        reason = f"{comparison.message}；{MISSING_PAGE_FIELD_REASON}"
    else:
        reason = comparison.message
    # 数电发票票面上没有「发票代码」这一栏，值是从数电号码派生的。不说清楚，
    # 审核员会照着这一栏去原图上找，找不到。派生关系由材料声明给出，这里
    # 不在引擎里固定具体业务的字段名。
    if any(item.derived_from for item in comparison.evidence):
        reason = f"{reason}；{label}由票面数电号码适配"
    # 任务在助手中展示，宿主页作为原值和写回目标。
    return make_task(
        step_id=f"FIELD-{comparison.field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=label,
        result_status=result_status,
        reason=reason,
        reason_codes=(
            ["PAGE_VALUE_MISSING"]
            if result_status != _comparison_status(comparison)
            else comparison.reason_codes
        ),
        page_target_field=comparison.field if page_interaction else None,
        writable=page_interaction,
        page_value=request.page_fields.get(comparison.field),
        values=values,
        evidence=material_evidence,
        requires_reviewer_action=(result_status != "MATCH"),
        details={
            "evidence_count": len({image for image, _ in material_evidence_by_image}),
            "evidence_mode": policy.mode if policy else None,
            "evidence_sources": list(dict.fromkeys(item.source for item in material_evidence)),
            "not_found_sources": list(policy.allowed_document_types)
            if policy and not material_evidence
            else [],
            "normalized_page_value": normalize_value(
                comparison.field, comparison.right_value, business=business
            ),
            "differences": comparison.differences,
        },
        evidence_mode=policy.mode if policy else None,
        not_found_sources=policy.allowed_document_types if policy and not material_evidence else (),
        normalized_page_value=normalize_value(
            comparison.field, comparison.right_value, business=business
        ),
    )


def _page_catalog_step(
    snapshot: ReviewFieldSnapshot, step_id: str, profile: BusinessProfile
) -> ReviewTask:
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
    return make_task(
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
    business = BusinessContext.from_profile(profile)
    if not profile.review_declared_fields_only:
        return True
    if profile.pack_declaration:
        declaration = profile.pack_declaration.field(field)
        return bool(declaration and declaration.reviewable)
    return bool(field) and field in business.labels


def _composite_field_step(
    request: ReviewRequest,
    check: CheckResult,
    *,
    page_interaction: bool,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> ReviewTask:
    """Render one task for a pair of page controls with one material value."""

    spec = next(item for item in business.composites if item.check_id == check.check_id)
    page_values = [
        CheckResultValue(
            source=spec.primary_label, value=request.page_fields.get(spec.primary_field)
        ),
        CheckResultValue(
            source=spec.secondary_label, value=request.page_fields.get(spec.secondary_field)
        ),
    ]
    material_values = [
        item
        for item in check.values
        if item.source
        not in {
            spec.primary_field,
            spec.secondary_field,
        }
    ]
    return make_task(
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
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> ReviewTask:
    """将一个字段绑定的多条检查合成字段任务。

    PAGE_VALUE_SOURCE 区分页面侧和材料侧；候选携带各自的检查标签和说明。
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
    not_applicable = all(check.details.get("not_applicable") is True for check in checks)
    # 版面明确没有该字段时，唯一且已采集的空控件就是正确页面值。
    collected_empty = (
        field in request.page_fields
        and field not in request.collection_diagnostics.ambiguous_fields
        and sum(item.field == field for item in request.review_fields) == 1
    )
    if (
        status == "MATCH"
        and not _page_field_collected(request, field)
        and not (not_applicable and collected_empty)
    ):
        status = "INSUFFICIENT"
        reason = f"{reason}；{MISSING_PAGE_FIELD_REASON}"
        reason_distributed = False
    label = business.labels.get(field, field)
    page_values = [CheckResultValue(source=f"{label}原文", value=page_text)]
    page_values.extend(
        # 页面侧改用检查自己的标签命名，与材料侧一一对应；`PAGE_VALUE_SOURCE`
        # 只是检查内部用来区分两侧的标记，不是展示名。
        CheckResultValue(source=check.label, value=value.value)
        for check in checks
        for value in check.values
        if value.source == PAGE_VALUE_SOURCE
    )
    completion = next(
        (
            check.details["brand_model_completion"]
            for check in checks
            if "brand_model_completion" in check.details
        ),
        None,
    )
    return make_task(
        step_id=f"FIELD-{field}",
        category="FIELD",
        display_target=ReviewDisplayTarget.ASSISTANT,
        label=label,
        result_status=status,
        reason=reason,
        reason_codes=(
            ["PAGE_VALUE_MISSING"]
            if not reason_distributed
            else list(dict.fromkeys(code for check in checks for code in check.reason_codes))
        ),
        page_target_field=field,
        page_value=page_text,
        page_values=page_values,
        # 每条检查的比对说明贴到它自己的候选值上。车型一个条目下挂着五六条
        # 候选，整段理由读下来才知道哪一条对不上；句子跟值放在一起，审核员
        # 在一个框里就能看完。理由本身仍然完整保留，供列表和别处使用。
        values=[
            value.model_copy(
                update={
                    "check_reason": check.reason,
                    "comparison_status": value.comparison_status
                    or ("INSUFFICIENT" if value.uncertain else check.status),
                }
            )
            for check in checks
            for value in check.values
            if value.source != PAGE_VALUE_SOURCE
        ],
        evidence=[item for check in checks for item in check.evidence],
        details={
            "check_ids": [item.check_id for item in checks],
            **({"brand_model_completion": completion} if completion is not None else {}),
            **({"not_applicable": True} if not_applicable else {}),
            **(
                {
                    "permitted_fill_values": list(
                        dict.fromkeys(
                            value
                            for check in checks
                            for value in check.details.get("permitted_fill_values", [])
                        )
                    )
                }
                if any("permitted_fill_values" in check.details for check in checks)
                else {}
            ),
            "reason_distributed": reason_distributed and not not_applicable,
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


def _restore_missing_fields(
    steps: list[ReviewTask],
    profile: BusinessProfile,
    snapshots_by_field: dict[str, ReviewFieldSnapshot],
    comparisons_by_field: dict[str, FieldComparison],
    request: ReviewRequest,
) -> list[ReviewTask]:
    """缺失控件也保留审核条目，按声明位置插入并禁止写回。"""
    business = BusinessContext.from_profile(profile)
    if not profile.review_declared_fields_only or not profile.pack_declaration:
        return steps
    # 声明过的审核字段不能因 DOM 目录漏采而消失。保留已有控件的页面顺序，
    # 缺项按业务声明插到下一个已知字段之前，明确提示重新采集且禁止写回。
    declared = [item for item in profile.pack_declaration.fields if item.reviewable]
    by_id = {step.step_id: step for step in steps}
    missing = {}
    for declaration in declared:
        field = declaration.key
        if field in snapshots_by_field:
            continue
        policy = field_policy(field, business=business)
        if policy is not None and not policy.page_evidence_required:
            continue
        step_id = f"FIELD-{field}"
        step = by_id.get(step_id)
        if step is None and field in comparisons_by_field:
            step = field_task(
                request, comparisons_by_field[field], page_interaction=True, business=business
            )
        if step is None:
            continue
        missing[step_id] = step.model_copy(
            update={
                "result_status": "INSUFFICIENT"
                if step.result_status == "MATCH"
                else step.result_status,
                "requires_reviewer_action": True,
                "writable": False,
                "reason": f"{step.reason}；{MISSING_PAGE_FIELD_REASON}",
                "reason_codes": list(dict.fromkeys([*step.reason_codes, "PAGE_VALUE_MISSING"])),
            }
        )
    steps = [step for step in steps if step.step_id not in missing]
    for index in range(len(declared) - 1, -1, -1):
        step = missing.get(f"FIELD-{declared[index].key}")
        if step is None:
            continue
        following = {item.key for item in declared[index + 1 :]}
        position = next(
            (i for i, item in enumerate(steps) if item.page_target_field in following), len(steps)
        )
        steps.insert(position, step)
    return steps


def _deduplicate_dom_field_steps(
    steps: Sequence[ReviewTask], business: BusinessContext
) -> list[ReviewTask]:
    """Drop collector fallbacks already represented by a canonical field task.

    A page can expose a decorative duplicate of a control.  The browser keeps
    such a snapshot unmapped deliberately, but the canonical field/composite
    task is still authoritative when it exists.  Showing both would turn one
    field into two reviewer actions and the fallback often carries an empty
    value.  If no canonical task exists, retain one fallback so an unconfigured
    or missing field remains visible to the reviewer.
    """
    canonical_labels = {
        label
        for declaration in (business.pack.fields if business.pack else ())
        for label in (declaration.label, *declaration.aliases)
        if label
    }
    canonical_labels.update(
        label
        for spec in business.composites
        for label in (spec.primary_label, spec.secondary_label, spec.label)
        if label
    )
    canonical_labels.update(label for label in business.labels.values() if label)
    canonical = {
        step.label
        for step in steps
        if step.category == "FIELD" and not step.step_id.startswith("FIELD-DOM-")
    }
    result: list[ReviewTask] = []
    seen_fallbacks: set[tuple[str, str | None]] = set()
    for step in steps:
        if not step.step_id.startswith("FIELD-DOM-"):
            result.append(step)
            continue
        if step.label in canonical_labels and step.label in canonical:
            continue
        key = (step.label, step.page_target_field)
        if key in seen_fallbacks:
            continue
        seen_fallbacks.add(key)
        result.append(step)
    return result


def field_tasks(
    request: ReviewRequest,
    profile: BusinessProfile,
    comparisons: Sequence[FieldComparison],
    business_checks: Sequence[CheckResult],
    *,
    writable: bool,
) -> list[ReviewTask]:
    """字段优先工作台：按页面字段目录出条目，字段缺失时降级人工复核。"""
    business = BusinessContext.from_profile(profile)
    comparisons_by_field = {item.field: item for item in comparisons}
    checks_by_id = {item.check_id: item for item in business_checks}
    # 页面字段 → 投影到它上面的规则结论。一个字段可以绑定多条检查，
    # 按声明顺序合并成一条字段条目。
    projected_by_field: dict[str, list[CheckResult]] = {}
    for check_id, field in profile.field_check_bindings:
        check = checks_by_id.get(check_id)
        if check is not None:
            projected_by_field.setdefault(field, []).append(check)

    composite_checks = {
        item.check_id: item
        for item in business_checks
        if any(item.check_id == spec.check_id for spec in business.composites)
    }
    if not request.review_fields:
        result: list[ReviewTask] = []
        emitted: set[str] = set()
        for comparison in comparisons:
            composite = business.composites_by_member.get(comparison.field)
            if composite is None:
                result.append(
                    field_task(request, comparison, page_interaction=writable, business=business)
                )
                continue
            check = composite_checks.get(composite.check_id)
            if check is not None and composite.check_id not in emitted:
                result.append(
                    _composite_field_step(
                        request, check, page_interaction=writable, business=business
                    )
                )
                emitted.add(composite.check_id)
        # 没有 DOM 目录时也要给出投影字段的条目：同一份响应不能因为这一轮
        # 采集是否成功而少一行字段。
        result.extend(
            _check_backed_field_step(request, field, checks, business=business)
            for field, checks in projected_by_field.items()
            if field not in comparisons_by_field
        )
        return result

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
        composite = business.composites_by_member.get(field or "")
        if composite is not None:
            check = composite_checks.get(composite.check_id)
            if check is not None and composite.check_id not in emitted_composites:
                paired_snapshots = [
                    snapshots_by_field.get(composite.primary_field),
                    snapshots_by_field.get(composite.secondary_field),
                ]
                step = _composite_field_step(
                    request, check, page_interaction=writable, business=business
                ).model_copy(
                    update={
                        "writable": writable
                        and all(item is not None and item.editable for item in paired_snapshots),
                        "control_type": "group",
                    }
                )
                steps.append(step)
                emitted_composites.add(composite.check_id)
            continue
        projection_snapshot = (
            snapshot.model_copy(update={"value": None, "editable": False})
            if field in request.collection_diagnostics.ambiguous_fields
            else snapshot
        )
        projected_task = (
            project_field_task(profile, projection_snapshot, business_checks) if field else None
        )
        if projected_task is not None:
            steps.append(projected_task)
            continue
        comparison = comparisons_by_field.get(field)
        projected = projected_by_field.get(field or "")
        if comparison is not None:
            steps.append(
                _with_control_snapshot(
                    field_task(request, comparison, page_interaction=writable, business=business),
                    projection_snapshot,
                    allow_write=writable,
                    profile=profile,
                )
            )
        elif projected:
            steps.append(
                _with_control_snapshot(
                    _check_backed_field_step(request, field, projected, business=business),
                    projection_snapshot,
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
        field_task(request, comparison, page_interaction=False, business=business)
        for comparison in comparisons
        if f"FIELD-{comparison.field}" not in emitted_step_ids
        and comparison.field not in business.composites_by_member
        and (policy := field_policy(comparison.field, business=business)) is not None
        and not policy.page_evidence_required
    )
    # 已声明的规则字段即使 DOM 漏采也保留，避免车型入口静默消失。
    steps.extend(
        _check_backed_field_step(request, field, checks, business=business)
        for field, checks in projected_by_field.items()
        if f"FIELD-{field}" not in emitted_step_ids
    )
    return _deduplicate_dom_field_steps(
        _restore_missing_fields(steps, profile, snapshots_by_field, comparisons_by_field, request),
        business,
    )
