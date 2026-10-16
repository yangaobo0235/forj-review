"""LangGraph 审核工作流。

主要职责：按确定顺序编排提取、核验、比较和建议生成节点。
修改日期：2026-08-26
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from itertools import pairwise
from time import perf_counter
from typing import Any, Required, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from forj_review_assistant.businesses.completeness import (
    evaluate_collected,
    evaluate_extracted,
)
from forj_review_assistant.businesses.configuration.inspection import profile_fingerprint
from forj_review_assistant.businesses.context import with_business_context
from forj_review_assistant.businesses.context_validation import validate_request_route
from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.capabilities.page_actions import PageActionHandler
from forj_review_assistant.capabilities.specs import (
    BusinessRuleHandler,
    CapabilityResult,
    ExternalCheckHandler,
    ReviewExecutionContext,
)
from forj_review_assistant.contracts.evidence import EvidenceFact, FieldObservation
from forj_review_assistant.contracts.recognition import (
    AgentAdvice,
    AgentBatchResult,
    CheckResult,
    MaterialCompletenessReport,
)
from forj_review_assistant.contracts.review import (
    CapabilityPlanEntry,
    FieldComparison,
    PageActionIntent,
    PageFillAction,
    QrCheck,
    Recommendation,
    Region,
    ReviewRequest,
    ReviewResponse,
    ReviewTask,
)
from forj_review_assistant.evidence.collection import (
    assemble_evidence_facts,
    collect_observations,
    collection_issues,
)
from forj_review_assistant.evidence.comparison.check_results import (
    qr_review_checks,
    unique_checks,
)
from forj_review_assistant.evidence.comparison.composite_fields import (
    build_page_composite_checks,
)
from forj_review_assistant.evidence.comparison.review import build_field_comparisons
from forj_review_assistant.observability.context import safe_id
from forj_review_assistant.presentation.advice import build_final_advice
from forj_review_assistant.presentation.response import _log_decision, response_from_results
from forj_review_assistant.presentation.routing import build_review_tasks
from forj_review_assistant.runtime.workflow.capability_assembly import WorkflowCapabilities
from forj_review_assistant.runtime.workflow.planner import (
    CapabilityPlanItem,
    plan_capabilities,
)

logger = logging.getLogger(__name__)

CAPABILITY_STAGE_NODES = {
    "execute_input_capabilities": "INPUT_COVERAGE",
    "execute_evidence_validation": "EVIDENCE_VALIDATION",
    "execute_evidence_capabilities": "EVIDENCE",
    "execute_pre_compare": "PRE_COMPARE",
    "execute_post_compare": "POST_COMPARE",
    "execute_final_review": "FINAL_REVIEW",
}

WORKFLOW_NODE_ORDER = (
    "resolve_context",
    "validate_input",
    "assess_coverage",
    "plan_input_capabilities",
    "execute_input_capabilities",
    "extract_evidence",
    "assess_evidence_quality",
    "plan_capabilities",
    "execute_evidence_validation",
    "execute_evidence_capabilities",
    "execute_pre_compare",
    "compare_fields",
    "execute_post_compare",
    "execute_final_review",
    "assemble_facts",
    "record_degradation",
    "prepare_review_tasks",
    "derive_recommendation",
    "build_response",
)


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


class ReviewWorkflow:
    """按固定节点顺序执行提取、核验、比较和最终建议组装。"""

    def __init__(
        self,
        service: Any,
        *,
        external_check_handlers: Mapping[str, ExternalCheckHandler] | None = None,
        business_rule_handlers: Mapping[str, BusinessRuleHandler] | None = None,
        page_action_handlers: Mapping[str, PageActionHandler] | None = None,
    ) -> None:
        """构建并编译审核状态图，具体业务能力由服务门面提供。"""
        self.service = service
        assembled = WorkflowCapabilities(
            service,
            external_check_handlers=external_check_handlers,
            business_rule_handlers=business_rule_handlers,
            page_action_handlers=page_action_handlers,
        )
        self.external_checks = assembled.external_checks
        self.business_rules = assembled.business_rules
        self.page_actions = assembled.page_actions
        self.capabilities = assembled.capabilities
        nodes = {
            "resolve_context": self._resolve_context,
            "validate_input": self._validate_input,
            "assess_coverage": self._assess_collected_materials,
            "plan_input_capabilities": self._plan_input_capabilities,
            "extract_evidence": self._extract_documents,
            "assess_evidence_quality": self._assess_extracted_evidence,
            "plan_capabilities": self._plan_capabilities,
            "compare_fields": self._compare_same_fields,
            "assemble_facts": self._assemble_facts,
            "record_degradation": self._record_degradation,
            "prepare_review_tasks": self._prepare_review_tasks,
            "derive_recommendation": self._derive_recommendation,
            "build_response": self._build_final_response,
            **{
                name: partial(self._execute_stage, stages={stage})
                for name, stage in CAPABILITY_STAGE_NODES.items()
            },
        }
        builder = StateGraph(ReviewState)
        for name in WORKFLOW_NODE_ORDER:
            builder.add_node(name, self._instrument(name, nodes[name]))
        builder.add_node(
            "build_error_response",
            self._instrument("build_error_response", self._build_error_response),
        )
        builder.add_edge(START, WORKFLOW_NODE_ORDER[0])
        for source, target in pairwise(WORKFLOW_NODE_ORDER):
            if source == "validate_input":
                builder.add_conditional_edges(
                    source,
                    lambda state: "error" if state.get("validation_error") else "continue",
                    {"error": "build_error_response", "continue": target},
                )
            else:
                builder.add_edge(source, target)
        builder.add_edge("build_error_response", END)
        builder.add_edge(WORKFLOW_NODE_ORDER[-1], END)
        # Reviews own transient execution state, including callbacks. When invoked by
        # a durable configuration graph, never inherit its session checkpointer.
        self.graph = builder.compile(checkpointer=False)

    @staticmethod
    def _instrument(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(state: ReviewState) -> dict[str, Any]:
            started = perf_counter()
            outcome, error_type = "SUCCEEDED", "-"
            try:
                result = fn(state)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except asyncio.CancelledError:
                outcome = "CANCELLED"
                raise
            except Exception as exc:
                outcome, error_type = "FAILED", type(exc).__name__
                raise
            finally:
                logger.log(
                    logging.INFO if outcome == "SUCCEEDED" else logging.WARNING,
                    "review node completed node=%s duration_ms=%.1f trace_id=%s result=%s error_type=%s",
                    name,
                    (perf_counter() - started) * 1000,
                    state.get("trace_id", ""),
                    outcome, error_type,
                )

        return wrapped

    def _validate_context(self, state: ReviewState) -> dict[str, Any]:
        """保留入口已解析的请求和业务配置，建立稳定的初始状态。"""
        request, profile = state["request"], state["profile"]
        validate_request_route(request, self.service.registry.active_profiles)
        if (
            profile.business_type != request.business_type
            or profile.version != request.profile_version
            or profile.region != request.region
            and profile.region is not Region.DEFAULT
        ):
            raise ValueError("请求业务、地区或版本与显式 Profile 不一致")
        return {"request": state["request"], "profile": state["profile"]}

    @staticmethod
    def _resolve_context(state: ReviewState) -> dict[str, Any]:
        """Resolve the already selected profile into a stable graph context."""
        profile = state["profile"]
        logger.info(
            "review configuration page_id=%s profile=%s configuration_sha256=%s",
            safe_id(profile.page_id), safe_id(profile.version), profile_fingerprint(profile),
        )
        return {"request": state["request"], "profile": state["profile"]}

    def _validate_input(self, state: ReviewState) -> dict[str, Any]:
        """Validate request/profile compatibility before any external work."""
        try:
            return self._validate_context(state)
        except (ValueError, LookupError) as exc:
            return {"validation_error": str(exc)}

    @staticmethod
    def _build_error_response(state: ReviewState) -> dict[str, Any]:
        message = state.get("validation_error") or "审核请求无效"
        response = ReviewResponse(
            business_type=state["request"].business_type,
            region=state["request"].region,
            profile_version=state["request"].profile_version,
            recommendation="REVIEW_REQUIRED",
            risk_level="HIGH",
            summary=message,
            issues=[message],
            review_tasks=[],
            trace_id=state.get("trace_id", ""),
        )
        return {"response": response, "recommendation": "REVIEW_REQUIRED"}

    async def _extract_documents(self, state: ReviewState) -> dict[str, Any]:
        """调用批处理服务提取材料字段，并透传进度回调。"""
        existing = state.get("batch")
        if existing is not None:
            return {"batch": existing}
        batch = await self.service._extract_documents(
            state["request"],
            state.get("batch_callback"),
            state["profile"],
            state.get("material_completeness"),
        )
        return {"batch": batch}

    @staticmethod
    async def _assess_collected_materials(state: ReviewState) -> dict[str, Any]:
        report = evaluate_collected(state["request"], state["profile"])
        callback = state.get("batch_callback")
        if callback is not None:
            callback_result = callback(
                AgentBatchResult(
                    total_count=len(state["request"].images),
                    material_completeness=report,
                )
            )
            if hasattr(callback_result, "__await__"):
                await callback_result
        return {"material_completeness": report, "coverage_status": report.status}

    @staticmethod
    async def _assess_extracted_evidence(state: ReviewState) -> dict[str, Any]:
        batch = state.get("batch")
        if batch is None:
            raise RuntimeError("工作流状态缺少文档提取结果")
        report = evaluate_extracted(state["request"], state["profile"], batch)
        logger.info("review evidence quality status=%s documents=%d issues=%d reasons=%s trace_id=%s",
                    report.status, len(batch.recognized_documents), len(report.issues),
                    ",".join(sorted({issue.reason_code or "unspecified" for issue in report.issues})) or "-",
                    state["trace_id"])
        updated = batch.model_copy(update={"material_completeness": report})
        callback = state.get("batch_callback")
        if callback is not None:
            callback_result = callback(updated)
            if hasattr(callback_result, "__await__"):
                await callback_result
        return {"batch": updated, "material_completeness": report, "coverage_status": report.status}

    @staticmethod
    def _degradation_reasons(results: Sequence[Any]) -> list[str]:
        """把被阻断、失败或未配置的能力转成面向审核员的降级原因。"""
        reasons: list[str] = []
        for result in results:
            if result.status in {"BLOCKED", "FAILED", "NOT_CONFIGURED"}:
                reason = f"能力 {result.capability_id} 状态为 {result.status}"
                if result.limitations:
                    reason = f"{reason}：{'；'.join(result.limitations)}"
                if reason not in reasons:
                    reasons.append(reason)
        return reasons

    @classmethod
    def _record_degradation(cls, state: ReviewState) -> dict[str, Any]:
        reasons = list(state.get("degradation_reasons", []))
        for reason in cls._degradation_reasons(state.get("capability_results", [])):
            if reason not in reasons:
                reasons.append(reason)
        return {"degradation_reasons": reasons}

    @staticmethod
    def _observations(state: ReviewState):
        return collect_observations(
            state["request"],
            state["profile"],
            state.get("batch", AgentBatchResult()),
            state.get("capability_results", ()),
            state.get("qr_checks", ()),
        )

    @classmethod
    def _available_facts(cls, state: ReviewState) -> set[str]:
        images = [image for image in state["request"].images if not image.collection_error]
        available = {image.business_scope for image in images}
        available.update(image.category_hint for image in images)
        batch = state.get("batch", AgentBatchResult())
        available.update(document.business_scope for document in batch.recognized_documents)
        pack = state["profile"].pack_declaration
        if pack:
            for image in images:
                if image.business_scope not in {"unknown", ""}:
                    continue
                material = pack.material(image.document_type_hint) or pack.material(
                    image.category_hint
                )
                if material and len(material.allowed_scopes) == 1:
                    available.add(material.allowed_scopes[0])
        observations, _ = cls._observations(state)
        available.update(
            item.field
            for item in observations
            if item.value not in (None, "") and not item.uncertain
        )
        return available

    @classmethod
    def _plan_input_capabilities(cls, state: ReviewState) -> dict[str, Any]:
        plan = plan_capabilities(state["profile"], cls._available_facts(state))
        return {
            "capability_plan": tuple(item for item in plan if item.stage == "INPUT_COVERAGE"),
            "capability_results": [],
        }

    @classmethod
    def _plan_capabilities(cls, state: ReviewState) -> dict[str, Any]:
        plan = plan_capabilities(state["profile"], cls._available_facts(state))
        executed = {result.capability_id for result in state.get("capability_results", ())}
        previous = {item.capability_id: item for item in state.get("capability_plan", ())}
        return {
            "capability_plan": tuple(
                previous[item.capability_id] if item.capability_id in executed else item
                for item in plan
            ),
            "capability_results": list(state.get("capability_results", ())),
        }

    def _execution_context(self, state: ReviewState) -> ReviewExecutionContext:
        observations, qr = self._observations(state)
        return ReviewExecutionContext(
            request=state["request"],
            profile=state["profile"],
            batch=state.get("batch", AgentBatchResult()),
            observations=tuple(observations),
            comparisons=tuple(state.get("comparisons", ())),
            qr_checks=tuple(qr),
            checks=tuple(
                unique_checks([*state.get("external_results", ()), *state.get("cross_checks", ())])
            ),
            material_completeness=state.get("material_completeness"),
        )

    async def _execute_stage(
        self,
        state: ReviewState,
        stages: set[str],
    ) -> dict[str, Any]:
        """Execute all Profile capabilities for the requested lifecycle stages."""
        context = self._execution_context(state)
        capability_plan = state.get("capability_plan")
        if capability_plan is None:
            capability_plan = plan_capabilities(state["profile"], self._available_facts(state))
        plans = {item.capability_id: item for item in capability_plan}
        bindings = {binding.capability_id: binding for binding in context.profile.bindings}
        specs = {spec.capability_id: spec for spec in context.profile.capabilities}
        results = list(state.get("capability_results", ()))
        executed = {result.capability_id for result in results}
        for item in capability_plan:
            spec = specs.get(item.capability_id)
            if spec is None or spec.stage not in stages or spec.capability_id in executed:
                continue
            binding = bindings.get(spec.capability_id)
            if binding is not None and binding.required is not None:
                spec = replace(spec, required=binding.required)
            actual_facts = {
                observation.field
                for observation in context.observations
                if observation.value is not None
                and observation.value != ""
                and not observation.uncertain
            }
            missing = set(spec.input_facts) - actual_facts
            if item.status == "READY" and missing:
                item = replace(
                    item,
                    status="BLOCKED" if spec.required else "SKIPPED",
                    reason="依赖事实未实际产出：" + "、".join(sorted(missing)),
                    missing_dependencies=tuple(sorted(missing)),
                )
                plans[spec.capability_id] = item
            result = await self.capabilities.execute(spec, item, context)
            results.append(result)
            observations, qr_checks = collect_observations(
                state["request"],
                state["profile"],
                context.batch,
                results,
                (*context.qr_checks, *result.qr_checks),
            )
            context = replace(
                context,
                observations=tuple(observations),
                qr_checks=tuple(qr_checks),
                checks=tuple(unique_checks([*context.checks, *result.checks])),
            )
        observations, qr_checks = collect_observations(
            state["request"],
            state["profile"],
            context.batch,
            results,
            context.qr_checks,
        )
        external_ids = {spec.capability_id for spec in specs.values() if spec.kind == "EXTERNAL"}
        return {
            "capability_plan": tuple(plans[item.capability_id] for item in capability_plan),
            "capability_results": results,
            "qr_checks": qr_checks,
            "observations": observations,
            "external_results": unique_checks(
                [
                    *(
                        check
                        for item in results
                        if item.capability_id in external_ids
                        for check in item.checks
                    ),
                    *qr_review_checks(qr_checks, state["request"].images),
                ]
            ),
            "cross_checks": [
                *state.get("composite_checks", ()),
                *(
                    check
                    for item in results
                    for check in item.checks
                    if item.capability_id not in external_ids
                ),
            ],
            "page_fill_intent": [action for item in results for action in item.page_actions],
            "page_action_intents": [
                intent for item in results for intent in item.page_action_intents
            ],
        }

    def _compare_same_fields(self, state: ReviewState) -> dict[str, Any]:
        observations, qr = self._observations(state)
        comparisons = build_field_comparisons(state["request"], state["profile"], observations)
        composites = build_page_composite_checks(
            state["request"], {item.field: item for item in comparisons}
        )
        return {
            "observations": observations,
            "comparisons": comparisons,
            "qr_checks": qr,
            "issues": collection_issues(state["request"], state["profile"]),
            "composite_checks": composites,
            "external_results": unique_checks(
                [*state.get("external_results", ()), *qr_review_checks(qr, state["request"].images)]
            ),
            "cross_checks": unique_checks([*state.get("cross_checks", ()), *composites]),
        }

    @classmethod
    def _assemble_facts(cls, state: ReviewState) -> dict[str, Any]:
        observations, qr = cls._observations(state)
        return {
            "observations": observations,
            "qr_checks": qr,
            "evidence_facts": assemble_evidence_facts(observations, state.get("comparisons", ())),
        }

    @staticmethod
    def _prepare_review_tasks(state: ReviewState) -> dict[str, Any]:
        """读取工作流状态，把步骤路由委托给集中路由模块。"""

        if "comparisons" not in state:
            raise RuntimeError("工作流状态缺少同字段比对结果")
        batch = state.get("batch")
        return {
            "review_tasks": build_review_tasks(
                request=state["request"],
                profile=state["profile"],
                comparisons=list(state["comparisons"]),
                external_checks=list(state.get("external_results", [])),
                business_checks=list(state.get("cross_checks", [])),
                completeness=state.get("material_completeness"),
                limitations=[
                    *(batch.limitations if batch else []),
                    *state.get("degradation_reasons", []),
                ],
            )
        }

    @staticmethod
    def _derive_recommendation(state: ReviewState) -> dict[str, Any]:
        batch = state["batch"]
        decision, advice = build_final_advice(
            state["comparisons"],
            unique_checks([*state.get("cross_checks", ()), *state.get("external_results", ())]),
            state.get("qr_checks", []),
            state.get("issues", []),
            list(dict.fromkeys([*batch.limitations, *state.get("degradation_reasons", ())])),
            batch.confidences,
            completeness=state.get("material_completeness"),
            review_tasks=state.get("review_tasks", []),
        )
        return {"recommendation": decision, "advice": advice}

    @staticmethod
    def _build_final_response(state: ReviewState) -> dict[str, Any]:
        response = response_from_results(
            state["request"],
            state["batch"],
            state["profile"],
            state["comparisons"],
            list(dict.fromkeys([*state.get("issues", ()), *state.get("degradation_reasons", ())])),
            qr_checks=state.get("qr_checks", []),
            business_checks=state.get("cross_checks", []),
            page_fill_intent=state.get("page_fill_intent", []),
        )
        advice = state["advice"]
        response = response.model_copy(
            update={
                "recommendation": Recommendation(state["recommendation"]),
                "agent_advice": advice,
                "risk_level": "LOW"
                if state["recommendation"] == "PASS"
                else "HIGH"
                if any(item.status == "CONFLICT" for item in advice.findings)
                else "MEDIUM",
                "summary": advice.summary,
                "review_tasks": state.get("review_tasks", []),
                "page_actions": list(state.get("page_action_intents", [])),
                "trace_id": state.get("trace_id", ""),
                "capability_plan": [
                    CapabilityPlanEntry(
                        capability_id=item.capability_id,
                        status=item.status,
                        stage=item.stage,
                        reason=item.reason,
                        dependencies=list(item.dependencies),
                        missing_dependencies=list(item.missing_dependencies),
                    )
                    for item in state.get("capability_plan", ())
                ],
                "capability_results": state.get("capability_results", []),
                "evidence_facts": state.get("evidence_facts", []),
            }
        )
        # Validate serialized enum values at the API boundary.
        response = ReviewResponse.model_validate(response.model_dump())
        _log_decision(response, "final")
        return {"response": response}

    @with_business_context
    async def run(
        self,
        request: ReviewRequest,
        profile: BusinessProfile | None = None,
        batch_callback: Callable[[AgentBatchResult], Any] | None = None,
        initial_batch: AgentBatchResult | None = None,
    ) -> tuple[ReviewResponse, AgentBatchResult]:
        """执行完整审核图并返回经过类型校验的最终响应。"""

        resolved_profile: BusinessProfile = (
            self.service.resolve_profile(request) if profile is None else profile
        )
        initial_state: ReviewState = {
            "request": request,
            "profile": resolved_profile,
            "batch_callback": batch_callback,
            "trace_id": request.trace_id or uuid4().hex,
        }
        if initial_batch is not None:
            initial_state["batch"] = initial_batch
        logger.info(
            "review workflow started trace_id=%s profile=%s",
            initial_state["trace_id"],
            resolved_profile.version,
        )
        state = await self.graph.ainvoke(initial_state)
        response = state.get("response")
        batch = state.get("batch")
        if response is None:
            raise RuntimeError("工作流未生成完整审核结果")
        if batch is None:
            # Validation/error branches terminate before document extraction;
            # return a typed empty batch together with the structured response.
            batch = AgentBatchResult(total_count=len(request.images))
        return response, batch
