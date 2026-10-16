"""报废车辆类型的档位复核（青岛）。

「报废车辆类型」按大类比对：`normalizer="vehicle_type"` 把行驶证、登记证书和
回收证明上的完整车型折叠成业务大类（`app/fields/normalize.py`），所以票面写
「重型厢式货车」还是「轻型厢式货车」，归一化后都是「载货汽车」，与页面的
「载货车」同样判一致——**档位根本不在比对范围内**。三份材料里一份写重型、
一份写轻型也照样一致。补贴范围只看得到大类，分不清轻型的单子会一路自动通过。

本规则补的就是这个盲区：任一材料的车辆类型**原文**里出现政策声明的关键词
（青岛为「轻型」）时产出一条 `INSUFFICIENT`，经 `field_check_bindings` 并进
「报废车辆类型」字段行，审核员在字段卡片上看到的是橙色「待复核」而不是绿色
「一致」。

- 只看材料侧，不看页面值：页面的报废车辆类型是粗分类下拉，从来不写档位。
- 只看旧车一侧：该字段只审旧车（见扩展包 `routes` 里的分区限定）。
- 关键词由地区政策声明（`ReplacementPolicy.vehicle_type_review_keywords`）：
  未配置的地区不产出检查项，与产地 `origin_keywords` 同一套做法。
- 未命中时**不产出任何检查项**（与 `verify_invoice` 一样）。MATCH 结论并进字段
  行会把材料值再列一遍、理由也多出一句废话，字段卡片保持原样才是对的。
"""

from forj_review_assistant.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_assistant.contracts.checks import CheckResult
from forj_review_assistant.contracts.review import FieldObservation
from forj_review_assistant.evidence.comparison.aggregate import deduplicate_sources
from forj_review_assistant.evidence.comparison.evidence_values import readable_value

VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID = "VEHICLE-TYPE-LIGHT-DUTY"
LABEL = "车辆类型档位核验"

TYPE_FIELD = "old_vehicle.type"

# 该字段的三个来源材料（扩展包 `sources` 声明的就是这三份）。理由里点名到具体
# 材料，审核员才知道该回哪张原图上看；未列出的材料类型退回它自己的类型名，不猜。
_MATERIAL_LABELS = {
    "vehicle_license": "机动车行驶证",
    "registration_certificate": "机动车登记证书",
    "scrap_certificate": "报废机动车回收证明",
}


def _material_label(item: FieldObservation) -> str:
    document_type = item.document_type or ""
    return _MATERIAL_LABELS.get(document_type, document_type or "材料")


def _matched_keywords(value: object, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in str(value)]


def build_vehicle_type_light_duty_check(
    context: ReviewExecutionContext,
) -> RuleExecutionResult:
    policy = context.profile.replacement_policy
    keywords = policy.vehicle_type_review_keywords if policy is not None else ()
    if not keywords:
        return RuleExecutionResult()

    # 同一份材料的正反面、重复上传合并成一条读值，否则一次误读会被点名两遍。
    sources = [
        item
        for item in context.observations
        if item.field == TYPE_FIELD
        and item.source_type == "image"
        and str(item.value or "").strip()
        and readable_value(item.value)
    ]
    hits = [
        item
        for item in deduplicate_sources(TYPE_FIELD, sources)
        if _matched_keywords(item.value, keywords)
    ]
    if not hits:
        return RuleExecutionResult()

    matched = list(dict.fromkeys(
        keyword for item in hits for keyword in _matched_keywords(item.value, keywords)
    ))
    detail = "、".join(f"{_material_label(item)}「{item.value}」" for item in hits)
    return RuleExecutionResult(checks=(CheckResult(
        check_id=VEHICLE_TYPE_LIGHT_DUTY_CHECK_ID,
        label=LABEL,
        status="INSUFFICIENT",
        reason_codes=(["RECOGNITION_UNCERTAIN"] if any(item.uncertain for item in hits)
                      else ["POLICY_REVIEW"]),
        reason=(
            f"材料识别的车辆类型含「{'」「'.join(matched)}」：{detail}。"
            "页面按大类填写、比对折叠后看不出档位差异，请人工复核该档位是否属于本次补贴范围"
        ),
    ),))


def run_vehicle_type_light_duty(context: ReviewExecutionContext) -> RuleExecutionResult:
    """报废车辆类型出现「轻型」等政策关键词时转人工复核。"""
    return build_vehicle_type_light_duty_check(context)
