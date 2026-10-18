"""发票号码与页面值一致时提出「一键验真」页面动作。

只提出动作，不执行；是否执行以及如何执行由浏览器适配器决定。
本能力不产出检查项，因此不会改变工作台的待处理计数。
"""

from forj_review_assistant.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_assistant.contracts.review import FieldStatus, PageActionIntent


def run_verify_invoice(context: ReviewExecutionContext) -> RuleExecutionResult:
    """发票号码与页面一致时提出 `verify_invoice` 动作。

    触发字段由业务声明，例如报废置换的 `invoice.invoice_no`。
    写死一个业务的名字会让另一个业务永远不验真，
    而且不报错——只是那个按钮从来不亮。

    前端会在审核完成后自动点击它，因此显式声明不需要逐项授权；
    其他需要授权的动作仍只能由操作人员触发。
    """
    field = context.profile.invoice_verification_field
    if field is None:
        return RuleExecutionResult()
    matched = any(
        item.field == field and item.status is FieldStatus.MATCH
        for item in context.comparisons
    )
    if not matched:
        return RuleExecutionResult()
    return RuleExecutionResult(
        page_action_intents=(
            PageActionIntent(action_id="verify_invoice", payload={"field": field}, requires_authorization=False),
        )
    )
