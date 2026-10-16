"""车源审核的车型一致性检查。

页面“车型”是下拉值，形如
``一汽解放 J6L 中卡 220马力 4X2 6.75米仓栅式载货车(CA5180CCYP62K1L4E5)(国五)``。
本规则按**固定正则**从中取出整车型号、马力与排放标准，再分别与材料侧比对，
各出一条独立结论；驱动形式不核验。

解析不交给模型：模型只负责从图片里读出原文，归类与取值一律由本模块的确定性
规则完成——这也是“字段优先”业务的既定分工。燃料种类由页面字段
``vehicle.fuel_type`` 独立核验，本规则不重复出结论。

无法解析出可比对的值时一律返回 `INSUFFICIENT`（人工复核），绝不因为没有证据
就判为通过。
"""

import re
from dataclasses import replace
from typing import Literal

from forj_review_assistant.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.review import FieldObservation
from forj_review_assistant.evidence.comparison.aggregate import (
    PAGE_VALUE_SOURCE,
    deduplicate_sources,
)
from forj_review_assistant.evidence.comparison.evidence_values import (
    observation_evidence,
    readable_value,
    trusted_page_value,
)

PAGE_MODEL_FIELD = "vehicle.model"
ENGINE_MODEL_FIELD = "vehicle.engine_model"
MODEL_CODE_FIELD = "vehicle.model_code"
POWER_FIELD = "vehicle.power_kw"
EMISSION_FIELD = "vehicle.emission_standard"

POWER_CHECK_ID = "VEHICLE-MODEL-POWER"
MODEL_CODE_CHECK_ID = "VEHICLE-MODEL-CODE"
EMISSION_CHECK_ID = "VEHICLE-MODEL-EMISSION"

# 马力容差：差几个马力仍判一致；同时给一个 5% 的相对窗口，覆盖大马力机型。
POWER_ABSOLUTE_TOLERANCE = 10
POWER_RELATIVE_TOLERANCE = 0.05
# 型号代码做包含匹配时的最短长度，避免用 "CA" 这类短前缀误判一致。
MIN_MODEL_CODE_LENGTH = 8
# 发动机型号尾部数字转马力：两位数表示“马力 / 10”。
ENGINE_MODEL_POWER_SCALE = 10
KILOWATT_TO_HORSEPOWER = 1.36

_POWER_IN_PAGE = re.compile(r"(\d{2,4})\s*马力")
_POWER_IN_ENGINE_MODEL = re.compile(r"-(\d{2,3})\s*E\d", re.IGNORECASE)
# 登记证书的“排量/功率”是同一栏，例如“12520 ml / 359 kw”。优先取带千瓦
# 单位的数，取不到才退回整串里的最后一个数——排量总在功率前面。
_KILOWATT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kw|千瓦)", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 页面车型里的括号有两种：整车型号（字母数字）与排放标准（中文或罗马数字）。
# 只把字母数字开头、长度足够的括号当作整车型号，"(国五)" "4X2" 自然被排除。
_MODEL_CODE_IN_PAGE = re.compile(r"[（(]([A-Za-z0-9][A-Za-z0-9\-]{4,})[）)]")
_EMISSION_IN_TEXT = re.compile(
    r"国\s*(IV|VI|V|Ⅳ|Ⅴ|Ⅵ|Ⅲ|Ⅱ|Ⅰ|[一二三四五六])", re.IGNORECASE
)
# 发动机型号的排放后缀不总是收尾：CA4DK1-22E5、CA6SM6-A48E6N、WP12.430E50
# 都是合法写法，所以取最后一段符合“E4/E5/E6”的记号，而不是锚定在结尾。
_ENGINE_MODEL_EMISSION = re.compile(r"E(\d)", re.IGNORECASE)

_EMISSION_BY_TOKEN = {
    "一": "国一",
    "二": "国二",
    "三": "国三",
    "四": "国四",
    "五": "国五",
    "六": "国六",
    "Ⅰ": "国一",
    "Ⅱ": "国二",
    "Ⅲ": "国三",
    "Ⅳ": "国四",
    "Ⅴ": "国五",
    "Ⅵ": "国六",
    "I": "国一",
    "II": "国二",
    "III": "国三",
    "IV": "国四",
    "V": "国五",
    "VI": "国六",
}
_ENGINE_MODEL_EMISSION_BY_DIGIT = {"4": "国四", "5": "国五", "6": "国六"}


def _page_model(context: ReviewExecutionContext) -> str:
    return str(trusted_page_value(context.request, PAGE_MODEL_FIELD) or "")


def _readable_values(
    observations: list[FieldObservation],
    field: str,
) -> list[FieldObservation]:
    """取某个材料字段的可读观察，合并相同读值并保留不同读值。

    登记证书第 1、2 页与第 3、4 页、铭牌的正反两面、以及用户重复上传的图片，
    都是同一份材料的多次读取。不先合并的话，马力会推成「488、488」、
    型号会列出两条一模一样的字符串，审核员看到的是一堆重复值而不是结论。

    合并策略与字段比较一致，不按页面值挑选证据。
    """
    sources = [
        item
        for item in observations
        if item.field == field
        and str(item.value or "").strip()
        and (
            item.source_type == "page"
            or (item.source_type == "image" and readable_value(item.value))
        )
    ]
    return [item for item in deduplicate_sources(field, sources) if item.source_type == "image"]


CheckStatus = Literal["MATCH", "CONFLICT", "INSUFFICIENT"]


def _page_side(value: object) -> CheckResultValue:
    """页面侧取值。

    用 `PAGE_VALUE_SOURCE` 标记，任务装配会把带这个来源的值摆到“页面原始值”
    区块，和材料侧一一对应；审核员因此能看出每一项到底比了什么，而不是只看
    到一句“一致”。
    """
    return CheckResultValue(source=PAGE_VALUE_SOURCE, value=value)


def _conclusion(
    label: str,
    value: object,
    source: FieldObservation,
    by: list[str],
    *,
    conflicting: bool | None,
) -> CheckResultValue:
    """一条结论值：结论 + 推导途径 + 原图定位。

    同一个结论可能有多种推导途径（220 马力既来自发动机型号的尾部数字，也来自
    发动机功率的换算）。同一结论只出一行，推导途径并列写在括号里；否则审核员
    会看到两行一模一样的值，以为材料互相重复。

    `conflicting` 说明这一条与页面是否对得上；为 None 表示页面侧没解析出值，
    此时不做判断，不给出误导性的"一致"。逐条的比对说明（“页面车型马力 430
    与材料推导值 473、480 不一致”）是**检查级**的，由字段装配写到 `check_reason`
    上，规则这里只负责值本身。
    """
    text = f"{value}（由{'、'.join(by)}推导）" if by else str(value)
    return CheckResultValue(
        source=label,
        value=text,
        conflicting=None if source.uncertain else conflicting,
        detail=str(source.value),
        source_id=source.source_id,
        image_id=source.image_id,
        image_index=source.image_index,
        document_type=source.document_type,
        uncertain=source.uncertain,
    )


def _collapse(candidates):
    """同一结论的多个推导途径合并成一条，保持首次出现的顺序。

    入参是 `(结论, 推导方式或 None, 来源观察)`，返回 `(结论, [推导方式], 来源观察)`。
    结论可以是马力数字或排放标准文本，所以这里不限定类型。

    推导方式为 None 表示**证件上直接印的就是这个值**，此时整条不再列推导途径：
    排放标准既印在证件上又能从发动机型号的后缀推出来时，写上「（由发动机型号
    的排放后缀推导）」会让审核员以为这个结论是算出来的，而材料上写得明明白白。
    理由里也是这么措辞的（`_emission_check` 的 `inferred`），两处必须一致，
    否则同一个框里的值和说明会互相打架。
    """
    merged: dict[object, tuple[list[str], FieldObservation]] = {}
    order: list[object] = []
    direct: set[object] = set()
    for value, note, item in candidates:
        if value not in merged:
            merged[value] = ([], item)
            order.append(value)
        elif merged[value][1].uncertain and not item.uncertain:
            merged[value] = (merged[value][0], item)
        if note is None:
            direct.add(value)
            continue
        notes = merged[value][0]
        if note not in notes:
            notes.append(note)
    return [
        (value, [] if value in direct else merged[value][0], merged[value][1])
        for value in order
    ]


def _result(
    check_id: str,
    label: str,
    status: CheckStatus,
    reason: str,
    sources: list[FieldObservation],
    values: list[CheckResultValue],
    details: dict[str, object],
) -> CheckResult:
    uncertain = any(item.uncertain for item in sources)
    if uncertain:
        status = "INSUFFICIENT"
        reason = "材料识别不确定，无法确认车型比对结论，请核对原图"
    return CheckResult(
        check_id=check_id,
        label=label,
        status=status,
        reason=reason,
        reason_codes=["RECOGNITION_UNCERTAIN"] if uncertain else [],
        values=values,
        evidence=observation_evidence(sources),
        details=details,
    )


# --- 马力 -------------------------------------------------------------------


def _page_power(page_model: str) -> int | None:
    match = _POWER_IN_PAGE.search(page_model)
    return int(match.group(1)) if match else None


def _powers_from_engine_model(text: str) -> list[int]:
    """发动机型号尾部数字推马力，如 CA4DK1-22E5 → 220。"""
    powers = []
    for match in _POWER_IN_ENGINE_MODEL.finditer(text):
        digits = int(match.group(1))
        powers.append(digits * ENGINE_MODEL_POWER_SCALE if digits < 100 else digits)
    return powers


def _power_from_kilowatts(text: str) -> int | None:
    """把千瓦换算成马力；排量和功率同栏时不要取到排量。"""
    match = _KILOWATT.search(text)
    if match:
        return round(float(match.group(1)) * KILOWATT_TO_HORSEPOWER)
    numbers = _NUMBER.findall(text)
    if not numbers:
        return None
    return round(float(numbers[-1]) * KILOWATT_TO_HORSEPOWER)


def _power_matches(page_power: int, candidate: int) -> bool:
    difference = abs(candidate - page_power)
    return (
        difference <= POWER_ABSOLUTE_TOLERANCE
        or difference <= page_power * POWER_RELATIVE_TOLERANCE
    )


def _power_text(power: int | None) -> str | None:
    return None if power is None else f"{power} 马力"


def _power_check(context: ReviewExecutionContext) -> CheckResult:
    check_id, label = POWER_CHECK_ID, "车型马力"
    observations = list(context.observations)
    page_power = _page_power(_page_model(context))
    engine_sources = _readable_values(observations, ENGINE_MODEL_FIELD)
    kilowatt_sources = _readable_values(observations, POWER_FIELD)
    candidates: list[tuple[int, str, FieldObservation]] = []
    for item in engine_sources:
        candidates.extend(
            (power, "发动机型号", item)
            for power in _powers_from_engine_model(str(item.value))
        )
    for item in kilowatt_sources:
        power = _power_from_kilowatts(str(item.value))
        if power is not None:
            candidates.append((power, "发动机功率", item))
    details = {
        "page_power": page_power,
        "material_powers": [power for power, _, _ in candidates],
        "derivation": [origin for _, origin, _ in candidates],
    }
    sources = engine_sources + kilowatt_sources
    # 合并后再出值和理由：同一条结论的多种推导途径只算一个值，理由里也不该
    # 出现「220、220」这种看着像两份材料的重复。
    concluded = _collapse(candidates)
    values = [_page_side(_power_text(page_power))]
    values.extend(
        _conclusion(
            label,
            _power_text(power),
            item,
            by,
            conflicting=None if page_power is None else not _power_matches(page_power, power),
        )
        for power, by, item in concluded
    )
    if page_power is None:
        return _result(
            check_id, label, "INSUFFICIENT",
            f"未能从页面车型“{_page_model(context)}”解析出马力", sources, values, details,
        )
    if not candidates:
        return _result(
            check_id, label, "INSUFFICIENT",
            "材料未提供可推导马力的发动机型号或发动机功率，请人工核对", sources, values, details,
        )
    matched = all(_power_matches(page_power, power) for power, _, _ in candidates)
    material_text = "、".join(str(power) for power, _, _ in concluded)
    return _result(
        check_id, label, "MATCH" if matched else "CONFLICT",
        (
            f"页面车型马力 {page_power} 与材料推导值 {material_text} 一致"
            if matched
            else f"页面车型马力 {page_power} 与材料推导值 {material_text} 不一致"
        ),
        sources, values, details,
    )


# --- 整车型号 ---------------------------------------------------------------


def _page_model_code(page_model: str) -> str | None:
    match = _MODEL_CODE_IN_PAGE.search(page_model)
    return match.group(1) if match else None


def _normalize_model_code(text: object) -> str:
    return "".join(character for character in str(text).upper() if character.isalnum())


def _model_codes_match(page_code: str, material_code: str) -> bool:
    if page_code == material_code:
        return True
    shorter, longer = sorted((page_code, material_code), key=len)
    return len(shorter) >= MIN_MODEL_CODE_LENGTH and shorter in longer


def _distinct_values(sources: list[FieldObservation], normalize) -> dict[str, list[object]]:
    """归一化值 → 原始值列表；用于判断材料内部是否自相矛盾。"""
    grouped: dict[str, list[object]] = {}
    for item in sources:
        grouped.setdefault(normalize(item.value), []).append(item.value)
    return grouped


def _model_code_check(context: ReviewExecutionContext) -> CheckResult:
    check_id, label = MODEL_CODE_CHECK_ID, "车型整车型号"
    page_model = _page_model(context)
    page_code = _normalize_model_code(_page_model_code(page_model) or "")
    sources = _readable_values(list(context.observations), MODEL_CODE_FIELD)
    grouped = _distinct_values([item for item in sources if _normalize_model_code(item.value)], _normalize_model_code)
    grouped.pop("", None)
    details = {"page_model_code": page_code or None, "material_model_codes": sorted(grouped)}
    values = [_page_side(page_code or None)]
    for item in sources:
        code = _normalize_model_code(item.value)
        if not code:
            continue
        values.append(
            _conclusion(
                label,
                str(item.value),
                item,
                [],
                conflicting=None if not page_code else not _model_codes_match(page_code, code),
            )
        )
    if not page_code:
        return _result(
            check_id, label, "INSUFFICIENT",
            f"未能从页面车型“{page_model}”解析出整车型号", sources, values, details,
        )
    if not grouped:
        return _result(
            check_id, label, "INSUFFICIENT",
            "材料未提供可读取的整车型号，请人工核对", sources, values, details,
        )
    if len(grouped) > 1:
        return _result(
            check_id, label, "CONFLICT",
            f"材料之间整车型号不一致：{'、'.join(sorted(grouped))}，请人工核对",
            sources, values, details,
        )
    material_code = next(iter(grouped))
    matched = _model_codes_match(page_code, material_code)
    return _result(
        check_id, label, "MATCH" if matched else "CONFLICT",
        (
            f"页面整车型号 {page_code} 与材料 {material_code} 一致"
            if matched
            else f"页面整车型号 {page_code} 与材料 {material_code} 不一致"
        ),
        sources, values, details,
    )


# --- 排放标准 ---------------------------------------------------------------


def _emission_of(text: object) -> str | None:
    match = _EMISSION_IN_TEXT.search(str(text))
    if not match:
        return None
    return _EMISSION_BY_TOKEN.get(match.group(1).upper())


def _emission_from_engine_model(text: object) -> str | None:
    """从发动机型号的排放后缀推排放标准，如 …48E6N → 国六。"""
    for digit in reversed(_ENGINE_MODEL_EMISSION.findall(str(text))):
        if digit in _ENGINE_MODEL_EMISSION_BY_DIGIT:
            return _ENGINE_MODEL_EMISSION_BY_DIGIT[digit]
    return None


def _emission_check(context: ReviewExecutionContext) -> CheckResult:
    check_id, label = EMISSION_CHECK_ID, "车型排放标准"
    page_model = _page_model(context)
    page_emission = _emission_of(page_model)
    observations = list(context.observations)
    declared = _readable_values(observations, EMISSION_FIELD)
    engine_sources = _readable_values(observations, ENGINE_MODEL_FIELD)
    # (排放标准, 推导方式；证件上直接印的为 None, 来源观察)
    candidates: list[tuple[str, str | None, FieldObservation]] = [
        (candidate, None, item)
        for item in declared
        if (candidate := _emission_of(item.value)) is not None
    ]
    candidates.extend(
        (inferred, "发动机型号的排放后缀", item)
        for item in engine_sources
        if (inferred := _emission_from_engine_model(item.value)) is not None
    )
    sources = declared + engine_sources
    details = {
        "page_emission": page_emission,
        "material_emissions": [candidate for candidate, _, _ in candidates],
    }
    values = [_page_side(page_emission)]
    values.extend(
        _conclusion(
            label,
            candidate,
            item,
            by,
            conflicting=None if page_emission is None else page_emission != candidate,
        )
        for candidate, by, item in _collapse(candidates)
    )
    if page_emission is None:
        return _result(
            check_id, label, "INSUFFICIENT",
            f"未能从页面车型“{page_model}”解析出排放标准", sources, values, details,
        )
    if not candidates:
        return _result(
            check_id, label, "INSUFFICIENT",
            "材料未提供可识别的排放标准，也无法从发动机型号推导，请人工核对",
            sources, values, details,
        )
    distinct = {candidate for candidate, _, _ in candidates}
    if len(distinct) > 1:
        return _result(
            check_id, label, "CONFLICT",
            f"材料之间排放标准不一致：{'、'.join(sorted(distinct))}，请人工核对",
            sources, values, details,
        )
    material_emission = next(iter(distinct))
    # 证件上直接印排放标准的很少，多数是从发动机型号的排放后缀推出来的。
    # 理由里说明来源，审核员才知道这个结论不是抄来的。
    inferred = all(note is not None for _, note, _ in candidates)
    source_text = "材料推导值（由发动机型号的排放后缀推导）" if inferred else "材料"
    matched = page_emission == material_emission
    return _result(
        check_id, label, "MATCH" if matched else "CONFLICT",
        (
            f"页面排放标准 {page_emission} 与{source_text} {material_emission} 一致"
            if matched
            else f"页面排放标准 {page_emission} 与{source_text} {material_emission} 不一致"
        ),
        sources, values, details,
    )


def build_vehicle_model_checks(context: ReviewExecutionContext) -> RuleExecutionResult:
    """车源车型三项结论：马力、整车型号、排放标准。"""
    # 保留全部候选供人工查看，但冲突证明只允许使用可靠材料。
    # 先按材料合并再筛可靠读值，避免同一材料的另一张不确定读图被过滤后丢失。
    merged = [item for field in dict.fromkeys(item.field for item in context.observations)
              for item in deduplicate_sources(field, [
                  item for item in context.observations if item.field == field
              ])]
    reliable_context = replace(context, observations=tuple(item for item in merged if not item.uncertain))
    checks = []
    for handler in (_power_check, _model_code_check, _emission_check):
        check = handler(context)
        if "RECOGNITION_UNCERTAIN" in check.reason_codes:
            confirmed = handler(reliable_context)
            if confirmed.status == "CONFLICT":
                check = check.model_copy(update={
                    "status": "CONFLICT",
                    "reason": confirmed.reason + "；另有识别不确定的证据，请核对原图",
                    "reason_codes": ["VALUE_MISMATCH", "RECOGNITION_UNCERTAIN"],
                })
        check.values = [item.model_copy(update={
            "comparison_status": "INSUFFICIENT" if item.uncertain or check.status == "INSUFFICIENT"
            else "CONFLICT" if item.conflicting else "MATCH",
        }) for item in check.values]
        checks.append(check)
    return RuleExecutionResult(checks=tuple(checks))


def run_vehicle_model(context: ReviewExecutionContext) -> RuleExecutionResult:
    """车源车型一致性：车型下拉值分别与材料的型号、马力、排放标准比对。"""
    return build_vehicle_model_checks(context)
