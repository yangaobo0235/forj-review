"""同字段多源证据聚合。

主要职责：标准化页面、图片和官网证据并生成字段比较。
修改日期：2026-08-26
"""

from collections import Counter

from forj_review_assistant.businesses.field_policies import AuthorityRule
from forj_review_assistant.contracts.evidence import (
    DifferenceRange,
    EvidenceFact,
    FieldObservation,
)
from forj_review_assistant.contracts.review import FieldComparison, FieldStatus
from forj_review_assistant.evidence.fields.differences import value_differences
from forj_review_assistant.evidence.fields.normalize import (
    format_like_page,
    normalize_value,
)

AUTHORITY_SUFFIX_LENGTH = 8

# 页面侧的展示来源。字段比较和确定性规则都用它标记“这一条是页面值”，
# 任务装配据此把页面侧和材料侧分开摆。
PAGE_VALUE_SOURCE = "申请页面字段"

EVIDENCE_SOURCE_LABELS = {
    "image": "图片识别",
    "page": PAGE_VALUE_SOURCE,
    "qr_page": "二维码官网字段",
    "capability": "能力核验",
}


def deduplicate_sources(
    field_name: str,
    observations: list[FieldObservation],
) -> list[FieldObservation]:
    """把同一份材料的多次读取合并为一个证据来源。

    一份材料可以由多张图片组成——行驶证正反面、登记证书第 1、2 页与第 3、4 页，
    或者用户把同一张图重复上传。它们是**同一份材料**的多次读取，不是多个互相
    独立的来源，因此按（业务分区，材料类型）合并，而不是按图片合并。

    只合并规范化后相同的读值，保留置信度最高的原图引用。不同读值必须保留，
    不能用待审核的页面值筛选材料证据，否则会把真实冲突变成通过。
    """
    grouped: dict[tuple[str, str, str | None], list[FieldObservation]] = {}
    order: list[tuple[str, str, str | None]] = []
    for item in observations:
        # 材料类型是这份材料在请求中的稳定身份；只有类型缺失时才退回
        # image_index——兼容 OCR 层可能同时产生 ``ocr-1`` 和 ``1`` 两行。
        if item.source_type == "image" and item.document_type:
            source_key = f"material:{item.business_scope or 'unknown'}:{item.document_type}"
            if item.source_field:
                source_key += f":{item.group_order}:{item.source_field}"
        elif item.source_type == "image" and item.image_index is not None:
            source_key = f"image-index:{item.image_index}"
        else:
            source_key = item.image_id or item.source_id or "page"
        key = (item.source_type, str(source_key), normalize_value(field_name, item.value))
        current = grouped.get(key)
        if current is None:
            grouped[key] = [item]
            order.append(key)
            continue
        current.append(item)
    result: list[FieldObservation] = []
    for key in order:
        candidates = grouped[key]
        selected = max(
            candidates,
            key=lambda item: (
                item.confidence if item.confidence is not None else -1.0,
                bool(item.image_id) and not str(item.source_id).startswith("ocr-"),
            ),
        )
        # 相同值去重不能同时抹掉识别不确定性。
        result.append(selected.model_copy(update={
            "uncertain": any(item.uncertain for item in candidates),
            "retried": any(item.retried for item in candidates),
        }))
    return result


def _mark_conflicting_evidence(
    field_name: str,
    valid: list[FieldObservation],
    evidence: list[EvidenceFact],
    status: FieldStatus,
) -> None:
    if status is not FieldStatus.CONFLICT:
        return

    official_values = {
        normalize_value(field_name, item.value)
        for item in valid
        if item.source_type == "qr_page"
    }
    reference: str | None
    if official_values:
        reference = next(iter(official_values)) if len(official_values) == 1 else None
    else:
        counts = Counter(normalize_value(field_name, item.value) for item in valid)
        highest_count = max(counts.values())
        winners = [value for value, count in counts.items() if count == highest_count]
        reference = winners[0] if len(winners) == 1 else None

    for observation, item in zip(valid, evidence, strict=True):
        item.conflicting = (
            reference is None
            or normalize_value(field_name, observation.value) != reference
        )


def aggregate_field(
    field_name: str,
    observations: list[FieldObservation],
    *,
    uncertain_requires_review: bool = False,
    single_evidence_requires_review: bool = True,
    page_evidence_required: bool = False,
    required_document_types: tuple[str, ...] = (),
) -> FieldComparison:
    """Compare all non-empty sources without discarding conflicting values."""
    valid = deduplicate_sources(
        field_name,
        [item for item in observations if str(item.value or "").strip()]
    )
    image_values = [item.value for item in valid if item.source_type in {"image", "capability"}]
    page_values = [item.value for item in valid if item.source_type == "page"]
    evidence = [
        EvidenceFact(
            source=EVIDENCE_SOURCE_LABELS.get(item.source_type, item.source_type),
            source_id=item.source_id,
            field=item.field,
            source_field=item.source_field,
            uncertain=item.uncertain,
            image_index=item.image_index,
            detail=item.source_id,
            image_id=item.image_id,
            business_scope=item.business_scope,
            group_title=item.group_title,
            group_order=item.group_order,
            document_type=item.document_type,
            value=item.value,
            normalized_value=normalize_value(field_name, item.value),
            derived_from=item.derived_from,
            evidence_region=item.evidence_region,
        )
        for item in valid
    ]

    # 不确定标记的一票否决只服务于字段优先的目标 Profile；
    # 未配置业务保持历史聚合语义，不因 uncertain 改变状态。
    if uncertain_requires_review and any(item.uncertain for item in valid):
        status = FieldStatus.REVIEW_REQUIRED
        message = (
            "能力核验结果不确定，请人工确认"
            if any(item.uncertain and item.source_type == "capability" for item in valid)
            else "图片识别不确定，已重读仍无法确认，请核对原图"
            if any(item.retried for item in valid)
            else "图片识别结果不确定，请核对原图"
        )
    elif not valid:
        status = FieldStatus.REVIEW_REQUIRED
        message = "页面与图片均未取得有效值"
    elif len(valid) == 1 and valid[0].source_type == "page":
        status = FieldStatus.REVIEW_REQUIRED
        message = "页面字段已采集，但未从材料中取得可核验值"
    elif page_evidence_required and not page_values:
        status = FieldStatus.REVIEW_REQUIRED
        message = "页面字段未采集或未填写，无法与材料比对，请人工核对"
    elif required_document_types and not set(required_document_types).issubset(
        {item.document_type for item in valid if item.source_type == "image"}
    ) and len({normalize_value(field_name, item.value) for item in valid}) == 1:
        status = FieldStatus.REVIEW_REQUIRED
        message = "需要互相印证的材料来源未齐全，页面填写值不能替代材料证据"
    elif len(valid) == 1 and single_evidence_requires_review:
        status = FieldStatus.REVIEW_REQUIRED
        message = "仅有一个有效来源，证据不足"
    elif len(valid) == 1:
        status = FieldStatus.MATCH
        message = "已发现 1 份有效证据，按实际证据核验"
    else:
        normalized = {normalize_value(field_name, item.value) for item in valid}
        if len(normalized) == 1:
            status = FieldStatus.MATCH
            message = "多个来源字段一致"
        else:
            status = FieldStatus.CONFLICT
            message = "多个来源存在不同值"

    confidences = [item.confidence for item in valid if item.confidence is not None]
    confidence = sum(confidences) / len(confidences) if confidences else None
    image_value = image_values[0] if image_values else None
    page_value = page_values[0] if page_values else None
    if field_name.endswith(".owner"):
        image_value = normalize_value(field_name, image_value)
        page_value = normalize_value(field_name, page_value)
    if status is FieldStatus.MATCH:
        image_value = format_like_page(field_name, image_value, page_value)
    for item in evidence:
        item.differences = value_differences(field_name, page_value, item.value) if item.source != "申请页面字段" else []
    _mark_conflicting_evidence(field_name, valid, evidence, status)
    differences: list[int] = []
    if page_values and image_values:
        left = normalize_value(field_name, page_values[0]) or ""
        right = normalize_value(field_name, image_values[0]) or ""
        differences = [i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b]
        differences.extend(range(min(len(left), len(right)), max(len(left), len(right))))
    return FieldComparison(
        field=field_name,
        left_value=image_value,
        right_value=page_value,
        status=status,
        confidence=confidence,
        evidence=evidence,
        message=message,
        differences=sorted(set(differences)),
    )


def _authority_rule_for(
    authority: tuple[AuthorityRule, ...],
    item: FieldObservation,
) -> AuthorityRule | None:
    """返回覆盖该证据的权威规则；不在链上的证据不参与该字段。"""
    for rule in authority:
        if rule.source != item.source_type:
            continue
        if (
            rule.source == "image"
            and rule.document_types
            and item.document_type not in rule.document_types
        ):
            continue
        return rule
    return None


def _authority_window(value: str | None, rule: AuthorityRule) -> str:
    text = value or ""
    return text[-AUTHORITY_SUFFIX_LENGTH:] if rule.match == "suffix8" else text


def _authority_evidence(
    field_name: str,
    item: FieldObservation,
    rule: AuthorityRule,
    *,
    page_item: FieldObservation | None,
    page_value: str | None,
    conflicting: bool,
) -> EvidenceFact:
    """构造一条证据；差异一律相对页面值计算，再平移回完整值坐标。

    差异区间落在**原始展示文本**上，因为工作台标红的是页面与材料的原文。
    `value_differences` 自身会用规范化结果判断是否存在业务差异，因此这里
    不需要重复判断。
    """
    item_value = normalize_value(field_name, item.value)
    differences: list[DifferenceRange] = []
    if item is not page_item and page_item is not None:
        page_text = str(page_item.value)
        item_text = str(item.value)
        page_window = _authority_window(page_text, rule)
        item_window = _authority_window(item_text, rule)
        item_offset = max(0, len(item_text) - len(item_window))
        page_offset = max(0, len(page_text) - len(page_window))
        differences = [
            difference.model_copy(update={
                "start": difference.start + item_offset,
                "end": difference.end + item_offset,
                "page_start": difference.page_start + page_offset,
                "page_end": difference.page_end + page_offset,
            })
            for difference in value_differences(field_name, page_window, item_window)
        ]
    return EvidenceFact(
        source=EVIDENCE_SOURCE_LABELS.get(item.source_type, item.source_type),
        source_id=item.source_id,
        field=item.field,
        source_field=item.source_field,
        uncertain=item.uncertain,
        image_index=item.image_index,
        detail=item.source_id,
        image_id=item.image_id,
        business_scope=item.business_scope,
        group_title=item.group_title,
        group_order=item.group_order,
        document_type=item.document_type,
        value=item.value,
        normalized_value=item_value,
        derived_from=item.derived_from,
        evidence_region=item.evidence_region,
        conflicting=conflicting,
        differences=differences,
    )


def _authority_message(
    authority: tuple[AuthorityRule, ...],
    benchmark_rule: AuthorityRule,
    conflicting_rules: list[AuthorityRule],
    present_rules: list[AuthorityRule],
) -> str:
    if conflicting_rules:
        return "；".join(
            f"{rule.label}与{benchmark_rule.label}不一致" for rule in conflicting_rules
        )
    suffix_used = any(
        rule.match == "suffix8" and rule in present_rules for rule in authority
    )
    tail = "，其余来源后 8 位一致" if suffix_used else "，其余来源一致"
    return f"以{benchmark_rule.label}为基准{tail}"


def aggregate_by_authority(
    field_name: str,
    observations: list[FieldObservation],
    authority: tuple[AuthorityRule, ...],
    *,
    uncertain_requires_review: bool = False,
    page_evidence_required: bool = False,
) -> FieldComparison:
    """按业务声明的权威链裁决字段。

    与 `aggregate_field` 的差别：比较基准是**链首来源**，不由票数推断；
    链首缺失或返回多个不同值时降级为人工复核，既不让位给链上的下一条
    来源，也不退化成投票。
    """
    if not authority:
        raise ValueError(f"{field_name} 未声明权威链")

    valid = deduplicate_sources(
        field_name,
        [
            item
            for item in observations
            if str(item.value or "").strip()
            and _authority_rule_for(authority, item) is not None
        ],
    )
    rule_of = {id(item): _authority_rule_for(authority, item) for item in valid}
    value_of = {id(item): normalize_value(field_name, item.value) for item in valid}

    def value(item: FieldObservation) -> str | None:
        return value_of[id(item)]

    # 1) 链首来源是唯一基准；链首缺失时不让位给下一条来源。
    head = authority[0]
    head_group = [item for item in valid if rule_of[id(item)] == head]
    benchmark: FieldObservation | None = head_group[0] if head_group else None
    benchmark_rule: AuthorityRule = head
    ambiguous: AuthorityRule | None = (
        head if head_group and len({value(item) for item in head_group}) != 1 else None
    )

    page_item = next((item for item in valid if item.source_type == "page"), None)
    page_value = value(page_item) if page_item is not None else None
    missing_required = [
        rule
        for rule in authority
        if rule.required and not any(rule_of[id(item)] == rule for item in valid)
    ]

    # 2) 判定状态：顺序与既有语义一致（多值 → 识别不确定 → 缺基准 → 缺必需来源）。
    conflicting_rules: list[AuthorityRule] = []
    if ambiguous is not None:
        status = FieldStatus.CONFLICT
        message = f"{ambiguous.label}返回多个不同值"
    elif uncertain_requires_review and any(item.uncertain for item in valid):
        status = FieldStatus.REVIEW_REQUIRED
        message = (
            "能力核验结果不确定，请人工确认"
            if any(item.uncertain and item.source_type == "capability" for item in valid)
            else "材料识别结果不确定，请核对原图"
        )
    elif benchmark is None:
        status = FieldStatus.REVIEW_REQUIRED
        message = f"未取得{head.label}值，无法建立权威比较基准"
    elif page_evidence_required and page_item is None:
        status = FieldStatus.REVIEW_REQUIRED
        message = "页面字段未采集或未填写，无法与材料比对，请人工核对"
    elif missing_required:
        status = FieldStatus.REVIEW_REQUIRED
        message = f"{missing_required[0].label}缺失，无法建立比较基准"
    else:
        benchmark_value = value(benchmark)
        for item in valid:
            rule = rule_of[id(item)]
            if (
                _authority_window(value(item), rule) != _authority_window(benchmark_value, rule)
                and rule not in conflicting_rules
            ):
                conflicting_rules.append(rule)
        status = FieldStatus.CONFLICT if conflicting_rules else FieldStatus.MATCH
        message = _authority_message(
            authority,
            benchmark_rule,
            conflicting_rules,
            [rule_of[id(item)] for item in valid],
        )

    # 3) 逐条证据着色与差异：结论以基准为准，标红以页面值为展示基准。
    benchmark_value = value(benchmark) if benchmark is not None else None
    evidence = [
        _authority_evidence(
            field_name,
            item,
            rule_of[id(item)],
            page_item=page_item,
            page_value=page_value,
            # 基准来源在多值冲突时标红；其余来源只有在基准唯一时才可能与
            # 基准不一致。基准缺失时没有任何来源算冲突。
            conflicting=(
                (ambiguous is not None and rule_of[id(item)] == head)
                or (
                    ambiguous is None
                    and benchmark is not None
                    and _authority_window(value(item), rule_of[id(item)])
                    != _authority_window(benchmark_value, rule_of[id(item)])
                )
            ),
        )
        for item in valid
    ]

    differences: list[int] = []
    if page_item is not None and benchmark is not None and page_value != benchmark_value:
        left = _authority_window(page_value, benchmark_rule)
        right = _authority_window(benchmark_value, benchmark_rule)
        differences = [index for index, (a, b) in enumerate(zip(left, right, strict=False)) if a != b]
        differences.extend(range(min(len(left), len(right)), max(len(left), len(right))))

    confidences = [item.confidence for item in valid if item.confidence is not None]
    return FieldComparison(
        field=field_name,
        left_value=benchmark.value if benchmark is not None else None,
        right_value=page_item.value if page_item is not None else None,
        status=status,
        confidence=sum(confidences) / len(confidences) if confidences else None,
        evidence=evidence,
        message=message,
        differences=sorted(set(differences)),
    )
