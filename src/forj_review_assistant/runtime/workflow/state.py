"""审核流水线的共享状态；节点只返回本次产生的增量。"""

from collections.abc import Callable
from typing import Any, Required, TypedDict

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.capabilities.specs import CapabilityResult
from forj_review_assistant.contracts.evidence import EvidenceFact, FieldObservation
from forj_review_assistant.contracts.recognition import (
    AgentAdvice,
    AgentBatchResult,
    CheckResult,
    MaterialCompletenessReport,
)
from forj_review_assistant.contracts.review import (
    FieldComparison,
    PageActionIntent,
    PageFillAction,
    QrCheck,
    ReviewRequest,
    ReviewResponse,
    ReviewTask,
)
from forj_review_assistant.runtime.workflow.planner import CapabilityPlanItem


class ReviewState(TypedDict, total=False):
    """工作流节点共享状态；Required 字段由入口保证，其余字段按节点顺序产生。"""

    request: Required[ReviewRequest]
    profile: Required[BusinessProfile]
    batch_callback: Callable[[AgentBatchResult], Any] | None
    batch: AgentBatchResult
    qr_checks: list[QrCheck]
    external_results: list[CheckResult]
    response: ReviewResponse  # Produced only by final/error rendering nodes.
    observations: list[FieldObservation]
    comparisons: list[FieldComparison]
    issues: list[str]
    cross_checks: list[CheckResult]
    composite_checks: list[CheckResult]
    page_fill_intent: list[PageFillAction]
    page_action_intents: list[PageActionIntent]
    recommendation: str
    advice: AgentAdvice
    material_completeness: MaterialCompletenessReport
    review_tasks: list[ReviewTask]
    capability_plan: tuple[CapabilityPlanItem, ...]
    capability_results: list[CapabilityResult]
    evidence_facts: list[EvidenceFact]
    validation_error: str
    coverage_status: str
    degradation_reasons: list[str]
    trace_id: str

