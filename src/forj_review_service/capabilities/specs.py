"""审核能力定义、执行上下文和处理器共享契约。"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from forj_review_service.contracts.evidence import EvidenceFact
from forj_review_service.contracts.recognition import (
    CheckResult,
    MaterialCompletenessReport,
    RecognitionBatchResult,
)
from forj_review_service.contracts.review import (
    CapabilityResult,
    FieldComparison,
    FieldObservation,
    PageActionIntent,
    QrCheck,
    ReviewRequest,
)

if TYPE_CHECKING:
    from forj_review_service.businesses.profiles import BusinessProfile


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    kind: Literal["EXTERNAL", "RULE", "MATERIAL"] = "RULE"
    version: str = "1.0"
    stage: Literal[
        "INPUT_COVERAGE",
        "EVIDENCE_VALIDATION",
        "EVIDENCE",
        "PRE_COMPARE",
        "POST_COMPARE",
        "FINAL_REVIEW",
    ] = "POST_COMPARE"
    input_facts: tuple[str, ...] = ()
    output_facts: tuple[str, ...] = ()
    required: bool = True
    dependencies: tuple[str, ...] = ()
    timeout_seconds: float = 30
    retries: int = 0
    failure_policy: Literal["BLOCK", "MANUAL_REVIEW", "SAFE_DEGRADE", "SKIP"] = "MANUAL_REVIEW"

    def __post_init__(self) -> None:
        if self.kind not in ("EXTERNAL", "RULE", "MATERIAL"):
            raise ValueError(f"未知能力类型：{self.kind}")
        if self.stage not in (
            "INPUT_COVERAGE",
            "EVIDENCE_VALIDATION",
            "EVIDENCE",
            "PRE_COMPARE",
            "POST_COMPARE",
            "FINAL_REVIEW",
        ):
            raise ValueError(f"未知能力阶段：{self.stage}")
        if self.failure_policy not in ("BLOCK", "MANUAL_REVIEW", "SAFE_DEGRADE", "SKIP"):
            raise ValueError(f"未知能力失败策略：{self.failure_policy}")
        if self.timeout_seconds <= 0 or not 0 <= self.retries <= 3:
            raise ValueError("能力超时必须为正数，重试次数范围为 0–3")


@dataclass(frozen=True)
class CapabilityBinding:
    """Profile 对注册能力的一次声明，不包含执行代码。"""

    capability_id: str
    enabled: bool = True
    required: bool | None = None
    parameters: tuple[tuple[str, str], ...] = ()


STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(
        (
            "INPUT_COVERAGE",
            "EVIDENCE_VALIDATION",
            "EVIDENCE",
            "PRE_COMPARE",
            "POST_COMPARE",
            "FINAL_REVIEW",
        )
    )
}


def order_capabilities(specs: Sequence[CapabilitySpec]) -> tuple[CapabilitySpec, ...]:
    """Validate fact producers and order by stage and dependency, not author order."""
    by_id = {spec.capability_id: spec for spec in specs}
    if len(by_id) != len(specs):
        raise ValueError("Profile 能力标识重复")
    producers: dict[str, str] = {}
    for spec in specs:
        for fact in spec.output_facts:
            if fact in producers:
                raise ValueError(
                    f"事实「{fact}」的生产者重复：{producers[fact]}、{spec.capability_id}"
                )
            producers[fact] = spec.capability_id
    pending = {}
    for spec in specs:
        predecessors = {producers[fact] for fact in spec.input_facts if fact in producers}
        for producer_id in predecessors:
            if STAGE_ORDER[by_id[producer_id].stage] > STAGE_ORDER[spec.stage]:
                raise ValueError(f"能力「{spec.capability_id}」依赖较晚阶段的「{producer_id}」")
        pending[spec.capability_id] = predecessors
    ordered = []
    while pending:
        ready = [key for key, predecessors in pending.items() if not predecessors]
        if not ready:
            raise ValueError(f"能力事实依赖存在循环：{', '.join(sorted(pending))}")
        key = min(ready, key=lambda key: (STAGE_ORDER[by_id[key].stage], key))
        ordered.append(by_id[key])
        del pending[key]
        for predecessors in pending.values():
            predecessors.discard(key)
    return tuple(ordered)


class CapabilityHandler(Protocol):
    async def __call__(
        self, context: ReviewExecutionContext, spec: CapabilitySpec
    ) -> CapabilityResult: ...


@dataclass(frozen=True)
class ReviewExecutionContext:
    request: ReviewRequest
    profile: BusinessProfile
    batch: RecognitionBatchResult
    observations: tuple[FieldObservation, ...]
    comparisons: tuple[FieldComparison, ...] = ()
    qr_checks: tuple[QrCheck, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    material_completeness: MaterialCompletenessReport | None = None
    absolute_deadline: float | None = None


@dataclass(frozen=True)
class RuleExecutionResult:
    checks: tuple[CheckResult, ...] = ()
    page_action_intents: tuple[PageActionIntent, ...] = ()
    evidence: tuple[EvidenceFact, ...] = ()


class ExternalCheckHandler(Protocol):
    async def __call__(
        self,
        context: ReviewExecutionContext,
        spec: CapabilitySpec,
    ) -> tuple[CheckResult | QrCheck | EvidenceFact, ...]: ...


class BusinessRuleHandler(Protocol):
    def __call__(
        self,
        context: ReviewExecutionContext,
    ) -> RuleExecutionResult | Awaitable[RuleExecutionResult]: ...
