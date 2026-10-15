"""业务规则组注册与顺序执行。"""

from collections.abc import Mapping
from types import MappingProxyType

from forj_review_assistant.capabilities.specs import (
    BusinessRuleHandler,
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_assistant.evidence.comparison.check_results import unique_checks


class UnknownBusinessRule(LookupError):
    pass


class BusinessRuleRegistry:
    def __init__(self, handlers: Mapping[str, BusinessRuleHandler]) -> None:
        self._handlers = MappingProxyType(dict(handlers))

    def execute(
        self,
        rule_group_ids: tuple[str, ...],
        context: ReviewExecutionContext,
    ) -> RuleExecutionResult:
        checks = []
        actions = []
        intents = []
        evidence = []
        for rule_group_id in dict.fromkeys(rule_group_ids):
            handler = self._handlers.get(rule_group_id)
            if handler is None:
                raise UnknownBusinessRule(f"未注册业务规则：{rule_group_id}")
            result = handler(context)
            checks.extend(result.checks)
            actions.extend(result.page_action_candidates)
            intents.extend(result.page_action_intents)
            evidence.extend(result.evidence)
        return RuleExecutionResult(
            checks=tuple(unique_checks(checks)),
            page_action_candidates=tuple(actions),
            page_action_intents=tuple(intents),
            evidence=tuple(evidence),
        )

    def validate(self, rule_group_ids: tuple[str, ...]) -> None:
        missing = [item for item in rule_group_ids if item not in self._handlers]
        if missing:
            raise UnknownBusinessRule(f"未注册业务规则：{', '.join(missing)}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
