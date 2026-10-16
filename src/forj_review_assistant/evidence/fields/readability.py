"""材料读值的可读性检查，不包含业务判定。"""

import re

UNREADABLE_VALUES = frozenset(
    {
        "无法识别",
        "无法确认",
        "不详",
        "未知",
        "不清",
        "空白",
        "N/A",
        "NA",
        "NULL",
        "NONE",
        "未识别",
        "模糊",
        "看不清",
    }
)


def readable_value(value: object | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(
        text
        and text.upper() not in UNREADABLE_VALUES
        and not any(
            word in text for word in ("无法识别", "无法确认", "未知", "不详", "看不清")
        )
        and not re.search(r"[?？*＊�]", text)
        and re.search(r"[\w\u3400-\u9fff]", text)
    )

