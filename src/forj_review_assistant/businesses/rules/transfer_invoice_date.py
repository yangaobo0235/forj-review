"""过户审核：开票日期必须晚于车源发布时间。

车源先发布、过户发票后开，这是过户业务的时序要求。发票比车源发布还早，
说明开票对象和这条车源对不上——要么发票不是这一单的，要么车源发布时间被改过。

**用页面上的开票日期，不是发票材料上的那个。** 页面上那个值是审核员在审的
记录本身，材料侧读出来的值由字段比对单独负责；这条规则只管"记录上填的日期
和车源发布时间对不对得上"。两个值不一致时字段比分会把它标成冲突，两条结论
各说各的事，不互相覆盖。

读不到任何一个日期就交人工复核，不判不通过：时间戳缺一个数字、格式少见，
都会让自动判断变成误伤。
"""

from __future__ import annotations

import re
from datetime import date

from forj_review_assistant.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue

CHECK_ID = "TRANSFER-INVOICE-DATE"
LABEL = "开票日期"
PAGE_FIELD = "transfer.invoice_date"
SOURCE_PUBLISHED_FIELD = "application.source_published_at"

# 年月日三段，分隔符是 - / . 年月日 或任意非数字。时分秒跟在后面会被忽略。
_DATE_PATTERN = re.compile(r"(\d{4})\D{1,2}(\d{1,2})\D{1,2}(\d{1,2})")


def _parse_day(value: object) -> str | None:
    """把日期取到"天"这一级；解析不出来返回 None。

    车源发布时间带时分秒（`2026-09-08 13:04:54`），开票日期只到天
    （`2026-09-17`）。**只比到天**：一天之内先发布后开票是常事，比到秒会让
    同一天的两条记录随机判成不通过。
    """
    match = _DATE_PATTERN.search(str(value or ""))
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        # 2026-13-45 这种"看着像日期"的值，不能算读到了日期。
        return None


def build_transfer_invoice_date_check(
    context: ReviewExecutionContext,
) -> RuleExecutionResult:
    """按页面值判定开票日期是否晚于车源发布时间。"""
    page_fields = context.request.page_fields or {}
    invoice_date = _parse_day(page_fields.get(PAGE_FIELD))
    published_at = _parse_day(page_fields.get(SOURCE_PUBLISHED_FIELD))

    values = [
        CheckResultValue(
            source=f"{LABEL}（页面）",
            value=page_fields.get(PAGE_FIELD),
        ),
        CheckResultValue(
            source="车源发布时间（页面）",
            value=page_fields.get(SOURCE_PUBLISHED_FIELD),
        ),
    ]

    if invoice_date is None or published_at is None:
        missing = LABEL if invoice_date is None else "车源发布时间"
        check = CheckResult(
            check_id=CHECK_ID,
            label=LABEL,
            status="INSUFFICIENT",
            reason=f"未取得有效的{missing}，无法判断开票日期是否晚于车源发布时间，请人工复核",
            values=values,
        )
    elif invoice_date <= published_at:
        check = CheckResult(
            check_id=CHECK_ID,
            label=LABEL,
            status="CONFLICT",
            reason=(
                f"开票日期 {invoice_date} 不晚于车源发布时间 "
                f"{published_at}，请核对该发票是否属于本条车源"
            ),
            values=values,
        )
    else:
        check = CheckResult(
            check_id=CHECK_ID,
            label=LABEL,
            status="MATCH",
            reason=f"开票日期 {invoice_date} 晚于车源发布时间 {published_at}",
            values=values,
        )

    return RuleExecutionResult(checks=(check,))


def run_transfer_invoice_date(context: ReviewExecutionContext) -> RuleExecutionResult:
    """过户审核：开票日期必须晚于车源发布时间。"""
    return build_transfer_invoice_date_check(context)
