"""归一化用例集：写法不同但值相同的，不许判成冲突；字符真不同的，必须仍然判成冲突。

这条防线不需要审核员回忆任何东西——用例由程序造，改一次配置就自动复跑。
覆盖格式差异与真实字符差异，防止归一化掩盖审核冲突。

为什么直接测 `normalize_value`：比较路径两边都过它
（`app/compare/aggregate.py:62,68,90,98`），所以"两边归一化后同形"就是
"这一对值不会冒冲突"的充分条件；反过来 `O/0`、`I/1` 必须异形，否则真差异会被洗掉。
"""

from __future__ import annotations

import pytest

from app.businesses.field_policies import field_policy
from app.fields.normalize import declared_normalizer, normalize_value

# 同一值的多种写法：票面这样印、页面那样录，都该算一致。
EQUIVALENT = {
    "invoice.invoice_date": ("2026年3月18日", "2026-03-18", "2026/3/18", "2026.03.18", " 2026-03-18 "),
    "invoice.amount": ("￥56,800.00", "56800", "56,800.00元", "56800.00", "¥56800"),
    "invoice.code": ("033002120511", "033002120511 ", "０３３００２１２０５１１"),
    "invoice.invoice_no": ("06182334", "数电号码 06182334", "发票号码：06182334", "06182334 "),
    "new_vehicle.vin": ("LBV12345678901234", "lbv12345678901234", "LBV 12345678901234"),
    "page_ocr.new_vehicle_vin": ("LBV12345678901234", "lbv12345678901234", "LBV-12345678901234"),
    "new_vehicle.plate_no": ("鲁B88888", "鲁b88888", "鲁B 88888"),
}

# 真差异：归一化不许把它们折叠掉。
DISTINCT = {
    "new_vehicle.vin": ("LBV12345678901234", "LBVO2345678901234", "LBVI2345678901234"),
    "page_ocr.new_vehicle_vin": ("LBV12345678901234", "LBV123456789012345", "LBV1234567890123"),
    "invoice.invoice_date": ("2026-03-18", "2026-03-19"),
    "invoice.amount": ("56800", "56800.01", "48600"),
    "invoice.code": ("033002120511", "033002120512"),
    "new_vehicle.plate_no": ("鲁B88888", "鲁B88889"),
}


@pytest.mark.parametrize("field", sorted(EQUIVALENT))
def test_writing_variants_collapse_to_one_form(field: str) -> None:
    forms = EQUIVALENT[field]
    normalized = {normalize_value(field, value) for value in forms}
    assert len(normalized) == 1, f"{field} 的写法差异没被归一化吃掉：{normalized}"
    assert next(iter(normalized)) is not None


@pytest.mark.parametrize("field", sorted(DISTINCT))
def test_real_differences_survive(field: str) -> None:
    variants = DISTINCT[field]
    normalized = {normalize_value(field, value) for value in variants}
    assert len(normalized) == len(variants), f"{field} 把真差异洗成同一个了：{normalized}"


def test_ocr_vin_shares_the_vin_normalizer() -> None:
    """`page_ocr.*` 靠 `material_field` 继承归一化器——没有它，OCR 的大小写与空格就会冒假冲突。"""
    assert declared_normalizer("page_ocr.new_vehicle_vin") == "vin"
    assert field_policy("page_ocr.new_vehicle_vin").normalizer == "vin"


def test_amiguity_hints_are_not_folded_away() -> None:
    """车架号里 I/1、O/0 是不同字符，谁也不许替审核员判定它们相等。"""
    assert normalize_value("new_vehicle.vin", "LBVO2345678901234") != normalize_value("new_vehicle.vin", "LBV02345678901234")
    assert normalize_value("new_vehicle.vin", "LBVI2345678901234") != normalize_value("new_vehicle.vin", "LBV12345678901234")


def test_unparseable_values_keep_their_difference() -> None:
    """归一化失败要回退成原样比较，不能吞掉差异装作一致。"""
    assert normalize_value("invoice.amount", "￥56,800.00元整") != normalize_value("invoice.amount", "56800")
    assert normalize_value("invoice.invoice_date", "二三六年三月") == "二三六年三月"


def test_empty_and_none_are_distinguishable_from_a_real_value() -> None:
    assert normalize_value("invoice.invoice_date", "") in (None, "")
    assert normalize_value("invoice.amount", None) in (None, "")
