import re
import unicodedata
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext

# 这些字段来自证件/车辆编号，OCR 常把大小写或空格识别得不稳定。
IDENTIFIER_SUFFIXES = (".vin", ".plate_no", ".certificate_no", ".invoice_no", ".code")
DATE_SUFFIXES = (".date", "_date")
OWNER_SUFFIX_LABELS = re.compile(r"(?:统一社会信用代码|社会信用代码|组织机构代码|统一信用代码).*\Z")
PARTY_NAME_SUFFIXES = (".owner", ".buyer_name", ".seller_name")
PARTY_PUNCTUATION = re.compile(r"[\s,，。．·•:：;；()（）\[\]【】/／]+")
VIN_AMBIGUITY_HINTS = re.compile(r"(?:易混淆|易混|不确定字符|疑似字符).*\Z", re.IGNORECASE)
# 汉字及兼容区的 CJK 字符。发动机型号的证件写法带中文品牌前缀（潍柴WP10.5H430E62），
# 品牌与型号是两件事，型号比对只看型号本身。
CJK_CHARACTERS = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def strip_cjk(text: str) -> str:
    """去掉汉字，只留型号/编号本身。"""
    return CJK_CHARACTERS.sub("", text)


def _normalize_vin(text: str) -> str:
    # OCR 提示是元数据，不是 VIN 本身；只保留规范值参与比较。
    return "".join(
        character for character in VIN_AMBIGUITY_HINTS.sub("", text).upper() if character.isalnum()
    )


def _normalize_invoice_number(text: str) -> str:
    text = re.sub(r"^(?:数电号码|发票号码|发票代码)[:：]?", "", text)
    return "".join(character for character in text.upper() if character.isalnum())


def _normalize_vehicle_type(text: str) -> str:
    value = re.sub(r"\s+", "", text)
    # 行驶证/登记证通常给出完整车型，页面只保存业务大类。只有语义明确
    # 落入同一大类时才折叠，不能依靠任意子串模糊匹配。
    if "牵引车" in value:
        return "牵引车"
    if any(kind in value for kind in ("载货汽车", "载货车", "货车")):
        return "载货汽车"
    return value


def _normalize_engine_model(text: str) -> str:
    """发动机型号只比型号本身。

    证件写法带中文品牌前缀（``潍柴WP10.5H430E62``），页面不一定带，
    两者是不同来源对同一台发动机的两种写法，品牌差异不该判成型号冲突。
    仅消除格式差异与品牌；I/1、O/0 等字符差异必须保留，交给审核员确认。
    """
    return re.sub(r"[\s\-－]", "", strip_cjk(text)).upper()


def _normalize_fuel_type(text: str) -> str:
    """Normalize equivalent fuel labels without crossing hybrid/fuel boundaries."""
    value = re.sub(r"\s+", "", text)
    # 混合动力是独立业务语义，不能因为包含“电”就折叠为新能源。
    if any(token in value for token in ("油电混合", "插电混合", "混合动力", "混合燃料")):
        return "混合动力"
    # 证件上统一写作“天然气”，页面会写得更具体（液化天然气(LNG) /
    # 压缩天然气(CNG)）。同一种燃料的不同写法不应判成冲突。
    folded = value.upper()
    if any(token in folded for token in ("天然气", "LNG", "CNG")):
        return "天然气"
    if value in {"电", "纯电", "纯电动", "电动", "新能源", "新能源汽车", "电能"}:
        return "新能源"
    return value


def _normalize_fuel_type_exact(text: str) -> str:
    """Normalize registration fuel labels without merging LNG and CNG."""
    value = re.sub(r"\s+", "", text)
    aliases = {
        "汽油": "汽油",
        "柴油": "柴油",
        "液化天然气": "液化天然气（LNG）",
        "液化天然气(LNG)": "液化天然气（LNG）",
        "液化天然气（LNG）": "液化天然气（LNG）",
        "LNG": "液化天然气（LNG）",
        "压缩天然气": "压缩天然气（CNG）",
        "压缩天然气(CNG)": "压缩天然气（CNG）",
        "压缩天然气（CNG）": "压缩天然气（CNG）",
        "CNG": "压缩天然气（CNG）",
        "电": "电动",
        "纯电": "电动",
        "纯电动": "电动",
        "电动": "电动",
        "新能源": "电动",
        "新能源汽车": "电动",
        "无": "无",
        "无发动机": "无",
    }
    return aliases.get(value, value)


def _normalize_jiefang_flag(text: str) -> str:
    """Map a page flag or registration brand to the page's 是/否 options."""
    value = re.sub(r"\s+", "", text)
    if value in {"是", "否"}:
        return value
    if "解放" in value:
        return "是"
    if value:
        return "否"
    return value


def canonical_emission_standard(value: object | None) -> str | None:
    """把登记证/页面常见的国几写法收敛为同一比较值。"""
    if value is None:
        return None
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).upper()
    if not text:
        return None
    roman = {
        "I": "一",
        "II": "二",
        "III": "三",
        "IV": "四",
        "V": "五",
        "VI": "六",
        "Ⅰ": "一",
        "Ⅱ": "二",
        "Ⅲ": "三",
        "Ⅳ": "四",
        "Ⅴ": "五",
        "Ⅵ": "六",
    }
    for source, target in sorted(roman.items(), key=lambda item: -len(item[0])):
        text = text.replace(source, target)
    for digit, chinese in (("3", "三"), ("4", "四"), ("5", "五"), ("6", "六")):
        text = re.sub(rf"国{digit}(?!\d)", f"国{chinese}", text)
    match = re.fullmatch(r"国([三四五六])(?:阶段|标准)?", text)
    if match:
        return f"国{match.group(1)}"
    return text


def _normalize_amount(text: str) -> str:
    cleaned = re.sub(r"[¥￥\u00a5,，元]", "", text, flags=re.IGNORECASE)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return text
    normalized = format(amount.normalize(), "f")
    return "0" if normalized in {"-0", "-0.0"} else normalized


def _normalize_date(text: str) -> str:
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if not match:
        return text
    try:
        parsed = date(*(int(part) for part in match.groups()))
    except ValueError:
        return text
    return parsed.isoformat()


def _normalize_upper(text: str) -> str:
    """只统一大小写的归一化：车牌等编号类字段。"""
    return text.upper()


def _normalize_phone(text: str) -> str:
    """Remove presentation punctuation; keep area codes, extensions and real differences."""
    value = re.sub(r"[\s()\-－—]", "", text)
    for prefix in ("+86", "0086"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


# 字段声明里 `normalizer` 的取值 → 实际处理函数。
# 新增字段时在扩展包里声明归一化器即可，不需要改本文件的分派表。
DECLARED_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "text": lambda text: text,
    "identifier": _normalize_upper,
    "plate": _normalize_upper,
    "vin": _normalize_vin,
    "invoice_number": _normalize_invoice_number,
    "vehicle_type": _normalize_vehicle_type,
    "engine_model": _normalize_engine_model,
    "fuel_type": _normalize_fuel_type,
    "fuel_type_exact": _normalize_fuel_type_exact,
    "jiefang_flag": _normalize_jiefang_flag,
    "emission_standard": lambda text: canonical_emission_standard(text) or text,
    "amount": _normalize_amount,
    "date": _normalize_date,
    # 主体名的空白和标点在预处理阶段就已去掉，这里不再加工。
    "party_name": lambda text: text,
    "phone": _normalize_phone,
}


def declared_normalizer(
    field_name: str, *, business: BusinessContext = DEFAULT_BUSINESS
) -> str | None:
    """返回字段声明里的归一化器名；没声明或声明为 default 时返回 None。"""
    # 延迟导入：字段层不依赖业务层，只在调用时取声明。
    from forj_review_service.businesses.field_policies import field_policy

    policy = field_policy(field_name, business=business)
    name = getattr(policy, "normalizer", None)
    return name if name and name != "default" else None


def normalize_value(
    field_name: str, value: object, *, business: BusinessContext = DEFAULT_BUSINESS
) -> str | None:
    """把不同来源的字段转换成可比较、但不丢失业务差异的形式。

    归一化器以字段声明（`forj_review_service.businesses.packs`）为准；声明里没写或写了未知
    名字时，回退到下面按字段名分派的兼容规则。
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if field_name.endswith(PARTY_NAME_SUFFIXES):
        text = OWNER_SUFFIX_LABELS.sub("", text)
        text = PARTY_PUNCTUATION.sub("", text)
    text = re.sub(r"\s+", "", text)

    declared = declared_normalizer(field_name, business=business)
    if declared is not None:
        handler = DECLARED_NORMALIZERS.get(declared)
        if handler is not None:
            return handler(text)

    if field_name in {"old_vehicle.vin", "new_vehicle.vin", "page_ocr.new_vehicle_vin"}:
        return _normalize_vin(text)
    if field_name in {"invoice.invoice_no", "invoice.code"}:
        return _normalize_invoice_number(text)
    if field_name == "old_vehicle.type":
        return _normalize_vehicle_type(text)
    if field_name == "old_vehicle.engine_model":
        return _normalize_engine_model(text)
    if field_name == "new_vehicle.fuel_type":
        return _normalize_fuel_type(text)
    if field_name == "invoice.amount":
        return _normalize_amount(text)
    if field_name.endswith(DATE_SUFFIXES):
        return _normalize_date(text)
    if field_name.endswith(IDENTIFIER_SUFFIXES):
        return text.upper()
    return text


def comparison_view(
    field_name: str, text: str, *, business: BusinessContext = DEFAULT_BUSINESS
) -> tuple[str, int]:
    """返回参与**差异展示**的片段，以及该片段在原文中的起始偏移。

    归一化回答的是"是否一致"，高亮必须落在原文上，所以两件事分开：归一化
    可以任意折叠语义，高亮只能裁剪位置。发动机型号比对只看型号本身，高亮也
    要落在型号上，否则审核员会在材料值前看到「缺少 潍柴」这种与结论无关的
    标记——品牌本来就不参与比较。

    裁剪只按前后缀进行（偏移量描述的是"前面去掉了几个字"），因此要求被去掉
    的字符集中在首尾；型号里出现夹在中间的汉字时退回原串，宁可高亮不准，
    也不给出偏移错误的标记。
    """
    if declared_normalizer(field_name, business=business) != "engine_model":
        return text, 0
    stripped = strip_cjk(text)
    if not stripped:
        return text, 0
    if text.endswith(stripped):
        return stripped, len(text) - len(stripped)
    if text.startswith(stripped):
        return stripped, 0
    return text, 0


def format_like_page(
    field_name: str,
    image_value: object,
    page_value: object,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> object:
    """Use the page representation only when both values are semantically equal."""
    if page_value is None:
        return image_value
    if normalize_value(field_name, image_value, business=business) == normalize_value(
        field_name, page_value, business=business
    ):
        return page_value
    return image_value
