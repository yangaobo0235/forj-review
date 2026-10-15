"""外部核验能力注册与顺序执行。"""

from collections.abc import Mapping
from types import MappingProxyType

from forj_review_assistant.capabilities.specs import (
    ExternalCheckHandler,
    ExternalCheckSpec,
    ReviewExecutionContext,
)
from forj_review_assistant.contracts.recognition import CheckResult
from forj_review_assistant.contracts.review import QrCheck


class UnknownExternalCheck(LookupError):
    pass


class ExternalCheckRegistry:
    def __init__(self, handlers: Mapping[str, ExternalCheckHandler]) -> None:
        self._handlers = MappingProxyType(dict(handlers))

    async def execute(
        self,
        specs: tuple[ExternalCheckSpec, ...],
        context: ReviewExecutionContext,
    ) -> tuple[CheckResult | QrCheck, ...]:
        results = []
        for spec in dict.fromkeys(specs):
            handler = self._handlers.get(spec.check_id)
            if handler is None:
                raise UnknownExternalCheck(f"未注册外部核验：{spec.check_id}")
            results.extend(await handler(context, spec))
        return tuple(results)

    def validate(self, specs: tuple[ExternalCheckSpec, ...]) -> None:
        missing = [
            item.check_id for item in specs if item.check_id not in self._handlers
        ]
        if missing:
            raise UnknownExternalCheck(f"未注册外部核验：{', '.join(missing)}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
