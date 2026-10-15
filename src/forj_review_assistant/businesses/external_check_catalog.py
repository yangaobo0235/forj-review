"""Built-in external check adapters; clients are injected by the runtime."""

from collections.abc import Awaitable, Callable

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.capabilities.specs import (
    ExternalCheckHandler,
    ExternalCheckSpec,
    ReviewExecutionContext,
)
from forj_review_assistant.contracts.recognition import AgentBatchResult, CheckResult
from forj_review_assistant.contracts.review import QrCheck, ReviewRequest

QrCollector = Callable[[ReviewRequest, AgentBatchResult, BusinessProfile], Awaitable[list[QrCheck]]]


def build_external_check_handlers(collect_qr: QrCollector) -> dict[str, ExternalCheckHandler]:
    async def scrap_certificate_qr(context: ReviewExecutionContext, spec: ExternalCheckSpec):
        checks = await collect_qr(context.request, context.batch, context.profile)
        if not checks and spec.mode == "REQUIRED":
            return (
                CheckResult(
                    check_id=f"EXTERNAL-{spec.check_id}",
                    label="二维码官网核验",
                    status="INSUFFICIENT",
                    reason="缺少可核验的报废回收证明材料",
                ),
            )
        return tuple(checks)

    return {"scrap_certificate_qr": scrap_certificate_qr}
