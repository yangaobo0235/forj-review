"""外部核验能力注册与顺序执行。"""

from collections.abc import Mapping
from types import MappingProxyType

from forj_review_service.capabilities.specs import (
    CapabilitySpec,
    ExternalCheckHandler,
    ReviewExecutionContext,
)
from forj_review_service.contracts.evidence import EvidenceFact
from forj_review_service.contracts.recognition import CheckResult
from forj_review_service.contracts.review import QrCheck


class UnknownExternalCheck(LookupError):
    pass


class ExternalCheckRegistry:
    def __init__(self, handlers: Mapping[str, ExternalCheckHandler]) -> None:
        self._handlers = MappingProxyType(dict(handlers))

    async def execute(
        self,
        specs: tuple[CapabilitySpec, ...],
        context: ReviewExecutionContext,
    ) -> tuple[CheckResult | QrCheck | EvidenceFact, ...]:
        results = []
        for spec in dict.fromkeys(specs):
            handler = self._handlers.get(spec.capability_id)
            if handler is None:
                raise UnknownExternalCheck(f"未注册外部核验：{spec.capability_id}")
            results.extend(await handler(context, spec))
        return tuple(results)

    def validate(self, specs: tuple[CapabilitySpec, ...]) -> None:
        missing = [item.capability_id for item in specs if item.capability_id not in self._handlers]
        if missing:
            raise UnknownExternalCheck(f"未注册外部核验：{', '.join(missing)}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
