"""跨材料规则公共工具。

主要职责：提供稳定取值、证据筛选和日期解析辅助函数。
修改日期：2026-08-26
"""


from forj_review_assistant.contracts.review import FieldComparison, FieldStatus


def settled_value(
    comparisons: dict[str, FieldComparison],
    field: str,
) -> object | None:
    """Return a normalized comparison value only after its sources agree."""

    comparison = comparisons.get(field)
    if comparison is None or comparison.status is not FieldStatus.MATCH:
        return None
    return (
        comparison.right_value
        if comparison.right_value not in (None, "")
        else comparison.left_value
    )


def raw_settled_value(
    comparisons: dict[str, FieldComparison],
    field: str,
) -> object | None:
    comparison = comparisons.get(field)
    if comparison is None or comparison.status is not FieldStatus.MATCH:
        return None
    for item in comparison.evidence:
        if item.value not in (None, ""):
            return item.value
    return settled_value(comparisons, field)

