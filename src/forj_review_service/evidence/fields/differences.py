"""在原始展示文本上生成差异区间；规范化只决定是否存在业务差异。"""

from difflib import SequenceMatcher

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.contracts.evidence import DifferenceRange
from forj_review_service.evidence.fields.normalize import (
    comparison_view,
    normalize_value,
)

_OPCODE_KINDS = {"replace": "REPLACE", "insert": "EXTRA", "delete": "MISSING"}


def value_differences(
    field: str, page: object, value: object, *, business: BusinessContext = DEFAULT_BUSINESS
) -> list[DifferenceRange]:
    """页面值与材料值的差异区间。

    区间坐标是**原始展示文本**的下标，工作台据此把页面原文标红；但比对本身
    发生在 `comparison_view` 裁出的片段上（发动机型号不带中文品牌），所以
    裁剪掉的字数要加回坐标里。`page_text` 取自裁剪后的页面片段，它就是审核员
    真正需要看到的那些缺失字符。
    """
    if page is None or value is None:
        return []
    if normalize_value(field, page, business=business) == normalize_value(
        field, value, business=business
    ):
        return []
    original, page_offset = comparison_view(field, str(page), business=business)
    recognized, value_offset = comparison_view(field, str(value), business=business)
    return [
        DifferenceRange(
            kind=_OPCODE_KINDS[tag],
            start=j1 + value_offset,
            end=j2 + value_offset,
            page_start=i1 + page_offset,
            page_end=i2 + page_offset,
            page_text=original[i1:i2],
        )
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, original, recognized, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
