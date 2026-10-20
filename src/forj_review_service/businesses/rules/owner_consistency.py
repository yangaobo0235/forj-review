"""新旧车所有人一致性检查（青岛、长春报废置换）。

业务只关心一件事：页面上的「报废车辆所有人」和「新车所有人」是不是同一个
主体，材料只是佐证。所以比对分两步，**先页面、后材料**：

1. 两个页面原值归一化后比对。不一致直接 `CONFLICT`，材料再怎么一致也改变
   不了"页面填了两个不同的人"这个事实。
2. 页面一致时再看材料。每份读到的所有人名称都要与页面值一致，有一份对不上
   就是 `CONFLICT`——那正是审核员必须亲眼看的场景。

证据只取 `old_vehicle.owner`（行驶证、回收证明）和 `new_vehicle.owner`
（行驶证、发票）这两组，业务明确只认这几份材料。每位所有人**每份材料一条**，
重复上传和正反面合并成一条（`deduplicate_sources`），否则同一张行驶证会
列出两遍。

**冲突不写成长句子**，而是就地表达：

- 材料值与页面值不同 → 那条材料值带上差异区间，工作台逐字标红；
- 两个页面值不同 → 在「页面原始值」区块下面留一句 `page_value_note`；
- 两种情况都因此**不再重复整段理由**（`reason_distributed`）。

只有"证据不足"仍然保留理由：页面值缺失、某一侧没有可读材料时，界面上没有
任何东西能说明为什么判待复核，那几句话是唯一的信息来源，藏掉等于让审核员
对着一个橙色标记猜。

这条结论没有页面写回目标：它不是一个页面控件，只是对两组既有字段的判断，
所以工作台只会给出「查看原图」，不给回填按钮。
"""

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_service.contracts.checks import CheckResult, CheckResultValue
from forj_review_service.contracts.decisions import ReasonCode
from forj_review_service.contracts.review import FieldObservation
from forj_review_service.evidence.comparison.aggregate import deduplicate_sources
from forj_review_service.evidence.comparison.evidence_values import (
    observation_evidence,
    readable_value,
    trusted_page_value,
)
from forj_review_service.evidence.fields.differences import value_differences
from forj_review_service.evidence.fields.normalize import normalize_value

OWNER_CONSISTENCY_CHECK_ID = "OWNER-CONSISTENCY-001"
LABEL = "车辆所有人一致性"

PAGE_VALUE_DETAIL_KEY = "page_values"
PAGE_VALUE_NOTE_DETAIL_KEY = "page_value_note"
REASON_DISTRIBUTED_DETAIL_KEY = "reason_distributed"

# 两个页面值不同时留在「页面原始值」下面的一句话。状态徽标已经写着"冲突"，
# 这里只需要指出冲突在哪两块之间，不再复述两边的值。
PAGE_MISMATCH_NOTE = "新旧车页面所有人不一致"

OLD_OWNER_FIELD = "old_vehicle.owner"
NEW_OWNER_FIELD = "new_vehicle.owner"

# 证据标签：业务侧的称呼 + 材料类型。同一份行驶证在旧车和新车两侧都会出现，
# 只写「行驶证」会让审核员分不清是哪一边的——而两边不一致时正是最需要分清
# 的时候。没有登记的组合作退回材料类型本身，不猜。
_EVIDENCE_LABELS = {
    (OLD_OWNER_FIELD, "vehicle_license"): "旧车行驶证",
    (OLD_OWNER_FIELD, "scrap_certificate"): "旧车回收证明",
    (NEW_OWNER_FIELD, "vehicle_license"): "新车行驶证",
    (NEW_OWNER_FIELD, "invoice"): "新车销售发票",
}


def _normalized(
    field: str, value: object | None, *, business: BusinessContext = DEFAULT_BUSINESS
) -> str | None:
    return normalize_value(field, value, business=business) or None


def _material_values(
    observations: list[FieldObservation],
    field: str,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> list[FieldObservation]:
    """一位所有人的材料读值，每份材料一条。

    不可读的读值（「无法识别」「看不清」）保留为不确定证据供人工查看，
    不能当成真实姓名证明冲突。
    """
    sources = [
        item
        for item in observations
        if item.field == field
        and item.source_type == "image"
        and (field, item.document_type) in _EVIDENCE_LABELS
        and str(item.value or "").strip()
    ]
    sources = [
        item.model_copy(update={"uncertain": True}) if not readable_value(item.value) else item
        for item in sources
    ]
    return deduplicate_sources(field, sources, business=business)


def _evidence_value(
    field: str,
    item: FieldObservation,
    page_value: object | None,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> CheckResultValue:
    """一条材料证据：标签带新旧车前缀，值与页面值的差异逐字标出来。

    `source` 直接用业务称呼（旧车行驶证 / 新车行驶证）而不是「图片识别」：
    同一份行驶证在两侧都会出现，只写「行驶证」在两边不一致时反而分不清是
    哪一边的，而那正是最需要分清的时候。

    `differences` 供工作台逐字标红——冲突用标红表达，不再写一句"材料上写的是
    某某"的长句子。`conflicting` 与差异区间同源：有差异就是不一致。
    """
    label = _EVIDENCE_LABELS.get(
        (field, item.document_type or ""), item.document_type or "图片证据"
    )
    differences = value_differences(field, page_value, item.value, business=business)
    return CheckResultValue(
        source=label,
        value=item.value,
        conflicting=None if item.uncertain else bool(differences),
        uncertain=item.uncertain,
        differences=differences,
        source_id=item.source_id,
        image_id=item.image_id,
        image_index=item.image_index,
        document_type=item.document_type,
    )


def _check(
    status: str,
    reason: str,
    page_values: list[dict[str, object]],
    materials: list[FieldObservation],
    values: list[CheckResultValue],
    *,
    page_value_note: str | None = None,
    reason_distributed: bool = False,
    reason_codes: list[ReasonCode] | None = None,
) -> CheckResult:
    details: dict[str, object] = {PAGE_VALUE_DETAIL_KEY: page_values}
    if page_value_note:
        details[PAGE_VALUE_NOTE_DETAIL_KEY] = page_value_note
    if reason_distributed:
        # 界面已经用标红和页面区块的说明表达了同一件事，整段理由不必再重复。
        details[REASON_DISTRIBUTED_DETAIL_KEY] = True
    return CheckResult(
        check_id=OWNER_CONSISTENCY_CHECK_ID,
        label=LABEL,
        status=status,
        reason=reason,
        reason_codes=reason_codes or [],
        values=[
            item.model_copy(
                update={
                    "comparison_status": "INSUFFICIENT"
                    if item.uncertain or status == "INSUFFICIENT"
                    else "CONFLICT"
                    if item.differences
                    else "MATCH",
                }
            )
            for item in values
        ],
        evidence=observation_evidence(materials),
        # 页面侧取值随检查一起下发；任务装配会把它们摆到「页面原始值」区块，
        # 材料侧留在「材料提取值」——审核员先看到页面比出了什么，再看材料。
        details=details,
    )


def build_owner_consistency_check(context: ReviewExecutionContext) -> RuleExecutionResult:
    business = BusinessContext.from_profile(context.profile)
    observations = list(context.observations)
    old_page = context.request.page_fields.get(OLD_OWNER_FIELD)
    new_page = context.request.page_fields.get(NEW_OWNER_FIELD)
    old_norm = _normalized(
        OLD_OWNER_FIELD, trusted_page_value(context.request, OLD_OWNER_FIELD), business=business
    )
    new_norm = _normalized(
        NEW_OWNER_FIELD, trusted_page_value(context.request, NEW_OWNER_FIELD), business=business
    )

    old_materials = _material_values(observations, OLD_OWNER_FIELD, business=business)
    new_materials = _material_values(observations, NEW_OWNER_FIELD, business=business)
    materials = old_materials + new_materials
    # 材料值只跟**自己那一边**的页面值比：先页面后材料，能走到这一步说明两个
    # 页面值已经一致，用哪一边当基准都一样，但按边配对更经得起以后改动。
    values = [
        _evidence_value(field, item, page, business=business)
        for field, items, page in (
            (OLD_OWNER_FIELD, old_materials, old_page),
            (NEW_OWNER_FIELD, new_materials, new_page),
        )
        for item in items
    ]
    page_values: list[dict[str, object]] = [
        {"source": "报废车辆所有人（页面）", "value": old_page},
        {"source": "新车所有人（页面）", "value": new_page},
    ]

    if not old_norm or not new_norm:
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    "旧车或新车的页面所有人未采集，无法比对，请人工核对",
                    page_values,
                    materials,
                    values,
                    reason_codes=["PAGE_VALUE_MISSING"],
                ),
            )
        )
    if old_norm != new_norm:
        # 冲突写在两个地方：页面区块下面一句提示，理由整段收起来。
        return RuleExecutionResult(
            checks=(
                _check(
                    "CONFLICT",
                    f"页面上的新旧车所有人不一致：{old_page} / {new_page}",
                    page_values,
                    materials,
                    values,
                    page_value_note=PAGE_MISMATCH_NOTE,
                    reason_distributed=True,
                    reason_codes=["VALUE_MISMATCH"],
                ),
            )
        )
    mismatched = [
        item
        for field, items in ((OLD_OWNER_FIELD, old_materials), (NEW_OWNER_FIELD, new_materials))
        for item in items
        if not item.uncertain and _normalized(field, item.value, business=business) != old_norm
    ]
    if mismatched:
        # 对不上的材料值已经逐字标红，理由不用再把两边念一遍。
        sides = "、".join(
            dict.fromkeys(
                _EVIDENCE_LABELS.get(
                    (item.field, item.document_type or ""), item.document_type or "图片证据"
                )
                for item in mismatched
            )
        )
        return RuleExecutionResult(
            checks=(
                _check(
                    "CONFLICT",
                    f"材料与页面上的所有人不一致：{sides}",
                    page_values,
                    materials,
                    values,
                    reason_distributed=True,
                    reason_codes=["VALUE_MISMATCH", "RECOGNITION_UNCERTAIN"]
                    if any(item.uncertain for item in materials)
                    else ["VALUE_MISMATCH"],
                ),
            )
        )

    if any(item.uncertain for item in materials):
        labels = "、".join(
            dict.fromkeys(
                _EVIDENCE_LABELS[(item.field, item.document_type)]
                for item in materials
                if item.uncertain
            )
        )
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    f"{labels}的所有人识别不确定，不能据此确认一致或冲突，请核对原图",
                    page_values,
                    materials,
                    values,
                    reason_codes=["RECOGNITION_UNCERTAIN"],
                ),
            )
        )
    if not old_materials or not new_materials:
        side = "旧车" if not old_materials else "新车"
        return RuleExecutionResult(
            checks=(
                _check(
                    "INSUFFICIENT",
                    f"页面上的新旧车所有人一致，但{side}没有可读取的所有人材料，无法佐证，请人工核对",
                    page_values,
                    materials,
                    values,
                ),
            )
        )

    return RuleExecutionResult(
        checks=(
            _check(
                "MATCH",
                f"页面上的新旧车所有人为 {old_page}，{len(materials)} 份材料与页面一致",
                page_values,
                materials,
                values,
            ),
        )
    )


def run_owner_consistency(context: ReviewExecutionContext) -> RuleExecutionResult:
    """报废置换新旧车所有人一致性：先比页面，再比材料。"""
    return build_owner_consistency_check(context)
