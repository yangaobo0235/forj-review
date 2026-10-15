"""LangGraph 审核工作流。

主要职责：按确定顺序编排提取、核验、比较和建议生成节点。
修改日期：2026-08-26
修改人：wuyi
"""

import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from itertools import pairwise
from time import perf_counter
from typing import Any, Required, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from forj_review_assistant.businesses.completeness import (
    evaluate_collected,
    evaluate_extracted,
)
from forj_review_assistant.businesses.context import with_business_context
from forj_review_assistant.businesses.context_validation import validate_request_route
from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.rules.affiliation import run_affiliation_subject
from forj_review_assistant.businesses.rules.invoice_verification import (
    run_verify_invoice,
)
from forj_review_assistant.businesses.rules.owner_consistency import (
    run_owner_consistency,
)
from forj_review_assistant.businesses.rules.replacement_policy import (
    run_replacement_policy,
)
from forj_review_assistant.businesses.rules.transfer_invoice_date import (
    run_transfer_invoice_date,
)
from forj_review_assistant.businesses.rules.vehicle_model import run_vehicle_model
from forj_review_assistant.businesses.rules.vehicle_type_light_duty import (
    run_vehicle_type_light_duty,
)
from forj_review_assistant.capabilities import CapabilityRegistry
from forj_review_assistant.capabilities.business_rules import BusinessRuleRegistry
from forj_review_assistant.capabilities.external_checks import ExternalCheckRegistry
from forj_review_assistant.capabilities.page_actions import (
    PageActionHandler,
    PageActionRegistry,
    PageActionSpec,
)
from forj_review_assistant.capabilities.specs import (
    BusinessRuleHandler,
    CapabilityResult,
    CapabilitySpec,
    ExternalCheckHandler,
    ExternalCheckSpec,
    ReviewExecutionContext,
)
from forj_review_assistant.capabilities.subgraphs import build_capability_subgraph
from forj_review_assistant.contracts.evidence import FieldObservation
from forj_review_assistant.contracts.recognition import (
    AgentAdvice,
    AgentBatchResult,
    CheckResult,
    MaterialCompletenessReport,
)
from forj_review_assistant.contracts.review import (
    BusinessType,
    CapabilityPlanEntry,
    PageActionIntent,
    PageFillAction,
    QrCheck,
    Region,
    ReviewRequest,
    ReviewResponse,
    ReviewTask,
)
from forj_review_assistant.evidence.comparison.check_results import (
    qr_review_checks,
    unique_checks,
)
from forj_review_assistant.evidence.comparison.composite_fields import (
    build_page_composite_checks,
)
from forj_review_assistant.evidence.comparison.evidence_values import batch_observations
from forj_review_assistant.presentation.assembly import assemble_review_response
from forj_review_assistant.presentation.response import _log_decision
from forj_review_assistant.presentation.routing import build_review_tasks
from forj_review_assistant.runtime.workflow.planner import (
    CapabilityPlanItem,
    plan_capabilities,
)

logger = logging.getLogger(__name__)

WORKFLOW_NODE_ORDER = (
    "resolve_context",
    "validate_input",
    "assess_coverage",
    "extract_evidence",
    "assess_evidence_quality",
    "plan_capabilities",
    "execute_capabilities",
    "compare_fields",
    "assemble_facts",
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
    response: ReviewResponse
    cross_checks: list[CheckResult]
    page_fill_intent: list[PageFillAction]
    page_action_intents: list[PageActionIntent]
    recommendation: str
    advice: AgentAdvice
    material_completeness: MaterialCompletenessReport
    review_tasks: list[ReviewTask]
    capability_plan: tuple[CapabilityPlanItem, ...]
    capability_results: list[CapabilityResult]
    evidence_facts: list[Any]
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
        # 地区政策规则的登记名从 Profile 推导，不在这里手抄一份青岛/长春名单：
        # 能力 ID 由 `RegionDeclaration.policy_capability_id()` 定义为
        # `{region}_replacement_policy`，处理器本身是通用的（具体政策从
        # `profile.replacement_policy` 读）。于是新增地区只要在
        # `packs/<业务>.py` 里加一条 `RegionDeclaration`，主图不必改动。
        policy_rule_ids = tuple(
            spec.capability_id
            for profile in service.registry.profiles
            for spec in profile.capabilities
            if spec.capability_id.endswith("_replacement_policy")
        )
        self.external_checks = ExternalCheckRegistry(
            {
                "scrap_certificate_qr": self._run_scrap_certificate_qr,
                **(external_check_handlers or {}),
            }
        )
        # 规则实现都在 `app/businesses/rules/` 各自的模块里，主图只负责按稳定
        # ID 登记。以前这些实现是 `ReviewWorkflow` 的私有方法，于是改一条业务
        # 门槛（例如「挂靠写回要同时满足哪几个条件」）得动这个通用文件。
        builtin_rules: dict[str, BusinessRuleHandler] = {
            **{rule_id: run_replacement_policy for rule_id in policy_rule_ids},
            "affiliation_subject": run_affiliation_subject,
            "verify_invoice": run_verify_invoice,
            "vehicle_model_consistency": run_vehicle_model,
            "owner_consistency": run_owner_consistency,
            "vehicle_type_light_duty": run_vehicle_type_light_duty,
            "transfer_invoice_date": run_transfer_invoice_date,
        }
        additional_rules = dict(business_rule_handlers or {})
        overridden = builtin_rules.keys() & additional_rules.keys()
        if overridden:
            raise ValueError(f"不能覆盖内置业务规则：{', '.join(sorted(overridden))}")
        self.business_rules = BusinessRuleRegistry({**builtin_rules, **additional_rules})
        async def _propose_page_action(actions: list[PageFillAction]) -> dict[str, object]:
            return {"status": "PROPOSED", "count": len(actions)}

        builtin_page_actions = {
            "fill_affiliation_fields": _propose_page_action,
            "verify_invoice": _propose_page_action,
            # 字段级回填：写回由浏览器按后端下发的白名单执行，后端只负责
            # 声明"这个业务允许提出写回请求"。
            "fill_review_fields": _propose_page_action,
        }
        builtin_page_specs = {"fill_affiliation_fields": PageActionSpec(
                "fill_affiliation_fields",
                reversible=True,
                requires_authorization=True,
                writable_fields=("old_vehicle.affiliation", "new_vehicle.affiliation"),
            )}
        builtin_page_specs["verify_invoice"] = PageActionSpec(
            "verify_invoice", reversible=False, requires_authorization=False,
            writable_fields=(),
        )
        # writable_fields 留空：可写字段是业务知识，由扩展包的字段声明给出，
        # 不在这里再维护一份，避免和 `writable_field_keys()` 打架。
        builtin_page_specs["fill_review_fields"] = PageActionSpec(
            "fill_review_fields", reversible=True, requires_authorization=True,
            writable_fields=(),
        )
        for action_id, handler in (page_action_handlers or {}).items():
            if action_id in builtin_page_actions:
                raise ValueError(f"不能覆盖内置页面动作：{action_id}")
            builtin_page_actions[action_id] = handler
        self.page_actions = PageActionRegistry(
            handlers=builtin_page_actions,
            specs=builtin_page_specs,
        )
        self.capabilities = CapabilityRegistry({
            **{
                key: build_capability_subgraph(self._execute_external)
                for key in self.external_checks.ids
            },
            **{
                key: build_capability_subgraph(self._execute_rule)
                for key in self.business_rules.ids
            },
            "material_completeness": build_capability_subgraph(self._execute_material),
        })
        for profile in service.registry.profiles:
            self.capabilities.validate(profile.capabilities)
            self.capabilities.validate_bindings(profile.bindings, profile.capabilities)
            external_specs = tuple(
                ExternalCheckSpec(spec.capability_id, "REQUIRED" if spec.required else "WHEN_PRESENT")
                for spec in profile.capabilities if spec.kind == "EXTERNAL"
            )
            rule_ids = tuple(
                spec.capability_id for spec in profile.capabilities if spec.kind == "RULE"
            )
            self.external_checks.validate(external_specs)
            self.business_rules.validate(rule_ids)
            self.page_actions.validate(tuple(profile.enabled_page_actions))
            policy_rules = set(rule_ids) & set(policy_rule_ids)
            if policy_rules and profile.replacement_policy is None:
                raise ValueError("地区政策规则缺少 replacement_policy 配置")
            policy = profile.replacement_policy
            # `and` 的优先级高于 `or`，最后一组条件必须加括号才读得出来。
            expected_policy_rules = {f"{profile.region.value}_replacement_policy"}
            if policy is not None and (
                profile.business_type is not BusinessType.SCRAP_REPLACEMENT
                or policy.region != profile.region
                or policy.version != profile.version
                or policy.policy_id != f"scrap_replacement_{profile.region.value}"
                or (policy_rules and policy_rules != expected_policy_rules)
            ):
                raise ValueError("地区政策的归属、版本或规则标识与 Profile 不一致")
        builder = StateGraph(ReviewState)
        builder.add_node("resolve_context", self._instrument("resolve_context", self._resolve_context))
        builder.add_node("validate_input", self._instrument("validate_input", self._validate_input))
        builder.add_node("build_error_response", self._instrument("build_error_response", self._build_error_response))
        builder.add_node("assess_coverage", self._instrument("assess_coverage", self._assess_collected_materials))
        builder.add_node("extract_evidence", self._instrument("extract_evidence", self._extract_documents))
        builder.add_node("assess_evidence_quality", self._instrument("assess_evidence_quality", self._assess_extracted_evidence))
        builder.add_node("plan_capabilities", self._instrument("plan_capabilities", self._plan_capabilities))
        builder.add_node("execute_capabilities", self._instrument("execute_capabilities", self._execute_capabilities))
        builder.add_node("record_degradation", self._instrument("record_degradation", self._record_degradation))
        builder.add_node("compare_fields", self._instrument("compare_fields", self._compare_same_fields))
        builder.add_node("assemble_facts", self._instrument("assemble_facts", self._assemble_facts))
        builder.add_node("prepare_review_tasks", self._instrument("prepare_review_tasks", self._prepare_review_tasks))
        builder.add_node("derive_recommendation", self._instrument("derive_recommendation", self._derive_recommendation))
        builder.add_node("build_response", self._instrument("build_response", self._build_final_response))
        builder.add_edge(START, WORKFLOW_NODE_ORDER[0])
        builder.add_edge("resolve_context", "validate_input")
        builder.add_conditional_edges(
            "validate_input",
            lambda state: "error" if state.get("validation_error") else "continue",
            {"error": "build_error_response", "continue": "assess_coverage"},
        )
        builder.add_conditional_edges(
            "execute_capabilities",
            lambda state: "degraded" if any(
                item.status in {"BLOCKED", "FAILED", "NOT_CONFIGURED"}
                for item in state.get("capability_results", [])
            ) else "continue",
            {"degraded": "record_degradation", "continue": "compare_fields"},
        )
        builder.add_edge("record_degradation", "compare_fields")
        for source, target in pairwise(WORKFLOW_NODE_ORDER[2:]):
            if source == "execute_capabilities":
                continue
            builder.add_edge(source, target)
        builder.add_edge("build_error_response", END)
        builder.add_edge(WORKFLOW_NODE_ORDER[-1], END)
        self.graph = builder.compile()

    @staticmethod
    def _instrument(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(state: ReviewState) -> dict[str, Any]:
            started = perf_counter()
            try:
                result = fn(state)
                if inspect.isawaitable(result):
                    result = await result
                return result
            finally:
                logger.info("review node completed node=%s duration_ms=%.1f trace_id=%s", name, (perf_counter() - started) * 1000, state.get("trace_id", ""))
        return wrapped

    @staticmethod
    def _validate_context(state: ReviewState) -> dict[str, Any]:
        """保留入口已解析的请求和业务配置，建立稳定的初始状态。"""
        request, profile = state["request"], state["profile"]
        validate_request_route(request)
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
        return {"request": state["request"], "profile": state["profile"]}

    @staticmethod
    def _validate_input(state: ReviewState) -> dict[str, Any]:
        """Validate request/profile compatibility before any external work."""
        try:
            return ReviewWorkflow._validate_context(state)
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
    def _available_facts(state: ReviewState) -> set[str]:
        available = {image.business_scope for image in state["request"].images if not image.collection_error}
        available.update(image.category_hint for image in state["request"].images if not image.collection_error)
        available.update(document.business_scope for document in state["batch"].recognized_documents)
        # 只有材料声明能唯一确定分区时才补全；不把新车材料冒充旧车依赖。
        pack = state["profile"].pack_declaration
        if pack:
            for image in state["request"].images:
                if image.collection_error or image.business_scope not in {"unknown", ""}:
                    continue
                material = pack.material(image.document_type_hint) or pack.material(image.category_hint)
                if material and len(material.allowed_scopes) == 1:
                    available.add(material.allowed_scopes[0])
        available.update(
            observation.field for observation in batch_observations(state["batch"])
            if observation.value is not None and observation.value != "" and not observation.uncertain
        )
        return available

    @classmethod
    def _plan_capabilities(cls, state: ReviewState) -> dict[str, Any]:
        return {"capability_plan": plan_capabilities(state["profile"], cls._available_facts(state)), "capability_results": []}

    @staticmethod
    def _capability_observations(profile: BusinessProfile, results: Sequence[CapabilityResult]) -> tuple[FieldObservation, ...]:
        outputs = {spec.capability_id: set(spec.output_facts) for spec in profile.capabilities}
        return tuple(
            FieldObservation.model_validate({**fact.model_dump(), "source_type": "capability", "source_id": result.capability_id})
            for result in results if result.status == "SUCCEEDED"
            for fact in result.evidence if fact.field in outputs.get(result.capability_id, ())
        )

    def _execution_context(self, state: ReviewState) -> ReviewExecutionContext:
        batch = state.get("batch")
        if batch is None:
            raise RuntimeError("工作流状态缺少文档提取结果")
        response = state.get("response")
        return ReviewExecutionContext(
            request=state["request"],
            profile=state["profile"],
            batch=batch,
            observations=(*batch_observations(batch), *self._capability_observations(state["profile"], state.get("capability_results", []))),
            comparisons=tuple(response.comparisons if response else ()),
            qr_checks=tuple(state.get("qr_checks", ())),
        )

    async def _run_scrap_certificate_qr(
        self,
        context: ReviewExecutionContext,
        spec: ExternalCheckSpec,
    ) -> tuple[CheckResult | QrCheck, ...]:
        checks = await self.service._collect_qr_checks(
            context.request,
            context.batch,
            context.profile,
        )
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

    async def _execute_external(self, context: ReviewExecutionContext, spec: CapabilitySpec) -> CapabilityResult:
        results = await self.external_checks.execute((ExternalCheckSpec(spec.capability_id, "REQUIRED" if spec.required else "WHEN_PRESENT"),), context)
        qr = [item for item in results if isinstance(item, QrCheck)]
        checks = [item for item in results if isinstance(item, CheckResult)]
        checks.extend(qr_review_checks(qr, context.request.images))
        return CapabilityResult(capability_id=spec.capability_id, status="SUCCEEDED", checks=checks, qr_checks=qr)

    async def _execute_rule(self, context: ReviewExecutionContext, spec: CapabilitySpec) -> CapabilityResult:
        result = self.business_rules.execute((spec.capability_id,), context)
        return CapabilityResult(
            capability_id=spec.capability_id,
            status="SUCCEEDED",
            checks=list(result.checks),
            page_actions=list(result.page_action_candidates),
            page_action_intents=list(result.page_action_intents),
        )

    async def _execute_material(self, context: ReviewExecutionContext, spec: CapabilitySpec) -> CapabilityResult:
        report = context.batch.material_completeness
        return CapabilityResult(capability_id=spec.capability_id, status="SUCCEEDED", checks=[CheckResult(
            check_id="MATERIAL-COMPLETENESS", label="材料完整性", status="MATCH" if report and report.status == "COMPLETE" else "INSUFFICIENT",
            reason="材料完整" if report and report.status == "COMPLETE" else "材料缺失或存在不确定证据",
        )])

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
        results = []
        for item in capability_plan:
            spec = specs.get(item.capability_id)
            if spec is None or spec.stage not in stages:
                continue
            binding = bindings.get(spec.capability_id)
            if binding is not None and binding.required is not None:
                spec = replace(spec, required=binding.required)
            actual_facts = {
                observation.field for observation in context.observations
                if observation.value is not None and observation.value != "" and not observation.uncertain
            }
            missing = set(spec.input_facts) - actual_facts
            if item.status == "READY" and missing:
                item = replace(item, status="BLOCKED" if spec.required else "SKIPPED",
                               reason="依赖事实未实际产出：" + "、".join(sorted(missing)),
                               missing_dependencies=tuple(sorted(missing)))
                plans[spec.capability_id] = item
            result = await self.capabilities.execute(spec, item, context)
            results.append(result)
            context = replace(context, observations=(
                *context.observations, *self._capability_observations(context.profile, [result]),
            ), qr_checks=(*context.qr_checks, *result.qr_checks))
        external_ids = {spec.capability_id for spec in specs.values() if spec.kind == "EXTERNAL"}
        return {
            "capability_plan": tuple(plans[item.capability_id] for item in capability_plan),
            "capability_results": results,
            "qr_checks": [check for item in results for check in item.qr_checks],
            "external_results": unique_checks(
                check for item in results if item.capability_id in external_ids
                for check in item.checks
            ),
            "cross_checks": [
                check for item in results for check in item.checks
                if item.capability_id not in external_ids
            ],
            "page_fill_intent": [
                action for item in results for action in item.page_actions
            ],
            "page_action_intents": [
                intent for item in results for intent in item.page_action_intents
            ],
        }

    async def _execute_capabilities(self, state: ReviewState) -> dict[str, Any]:
        """Execute capabilities by stage through the single registry entrypoint."""
        staged = await self._execute_stage(
            state,
            {"INPUT_COVERAGE", "EVIDENCE", "PRE_COMPARE"},
        )
        return {
            **staged,
            "capability_results": list(staged.get("capability_results", [])),
        }

    def _compare_same_fields(self, state: ReviewState) -> dict[str, Any]:
        """组装页面、图片和官网观察值并执行同字段比较。"""
        batch = state.get("batch")
        qr_checks = state.get("qr_checks")
        if batch is None:
            raise RuntimeError("工作流状态缺少文档提取结果")
        if qr_checks is None:
            raise RuntimeError("工作流状态缺少二维码核验结果")
        response = self.service._build_response(
            state["request"],
            batch,
            state["profile"],
            qr_checks=qr_checks,
            business_checks=[],
            defer_advice=True,
        )
        composite_checks = build_page_composite_checks(
            state["request"],
            {item.field: item for item in response.comparisons},
        )
        return {
            "response": response,
            "external_results": unique_checks(
                [
                    *state.get("external_results", []),
                    *qr_review_checks(response.qr_checks, state["request"].images),
                ]
            ),
            "cross_checks": unique_checks([
                *state.get("cross_checks", []),
                *composite_checks,
            ]),
        }

    async def _assemble_facts(self, state: ReviewState) -> dict[str, Any]:
        """Run post-compare capabilities and close the fact/evidence graph."""
        rules = await self._execute_stage(state, {"POST_COMPARE", "FINAL_REVIEW"})
        response = state.get("response")
        facts = [fact for comparison in response.comparisons for fact in comparison.evidence] if response else []
        merged_results = [
            *state.get("capability_results", []),
            *rules.get("capability_results", []),
        ]
        # 主图的降级条件边只挂在 execute_capabilities（前三个阶段）上，
        # POST_COMPARE / FINAL_REVIEW 的结果到这里才合并。不在这里补一次，
        # 后两个阶段被阻断或失败时降级原因会静默丢失。
        known_reasons = list(state.get("degradation_reasons", []))
        return {
            **rules,
            "qr_checks": [*state.get("qr_checks", []), *rules.get("qr_checks", [])],
            "external_results": unique_checks([
                *state.get("external_results", []),
                *rules.get("external_results", []),
            ]),
            "cross_checks": unique_checks([
                *state.get("cross_checks", []),
                *rules.get("cross_checks", []),
            ]),
            "page_fill_intent": [
                *state.get("page_fill_intent", []),
                *rules.get("page_fill_intent", []),
            ],
            "page_action_intents": [
                *state.get("page_action_intents", []),
                *rules.get("page_action_intents", []),
            ],
            "evidence_facts": facts,
            "capability_results": merged_results,
            "degradation_reasons": [
                *known_reasons,
                *(
                    reason
                    for reason in self._degradation_reasons(merged_results)
                    if reason not in known_reasons
                ),
            ],
        }

    @staticmethod
    def _prepare_review_tasks(state: ReviewState) -> dict[str, Any]:
        """读取工作流状态，把步骤路由委托给集中路由模块。"""

        response = state.get("response")
        if response is None:
            raise RuntimeError("工作流状态缺少同字段比对结果")
        batch = state.get("batch")
        return {
            "review_tasks": build_review_tasks(
                request=state["request"],
                profile=state["profile"],
                comparisons=list(response.comparisons),
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
        """汇总字段、跨材料、二维码和能力限制，生成安全审核建议。"""
        response = state.get("response")
        cross_checks = state.get("cross_checks")
        qr_checks = state.get("qr_checks")
        batch = state.get("batch")
        if response is None:
            raise RuntimeError("工作流状态缺少同字段比对结果")
        if cross_checks is None:
            raise RuntimeError("工作流状态缺少跨材料比对结果")
        if qr_checks is None:
            raise RuntimeError("工作流状态缺少二维码核验结果")
        if batch is None:
            raise RuntimeError("工作流状态缺少文档提取结果")
        assembled = assemble_review_response(
            response,
            batch,
            business_checks=cross_checks,
            external_checks=state.get("external_results", []),
            page_actions=state.get("page_fill_intent", []),
            review_tasks=state.get("review_tasks", []),
        )
        return {
            "response": assembled.model_copy(update={
                "page_actions": [
                    *assembled.page_actions,
                    *state.get("page_action_intents", []),
                ],
            }),
            "recommendation": assembled.recommendation.value,
            "advice": assembled.agent_advice,
        }

    @staticmethod
    def _build_final_response(state: ReviewState) -> dict[str, Any]:
        """把最终建议写回响应，并按冲突程度计算风险级别。"""
        current_response = state.get("response")
        recommendation = state.get("recommendation")
        advice = state.get("advice")
        cross_checks = state.get("cross_checks")
        if current_response is None:
            raise RuntimeError("工作流状态缺少同字段比对结果")
        if recommendation is None:
            raise RuntimeError("工作流状态缺少审核建议")
        if advice is None:
            raise RuntimeError("工作流状态缺少最终建议详情")
        if cross_checks is None:
            raise RuntimeError("工作流状态缺少跨材料比对结果")
        facts = list(state.get("evidence_facts", [])) or [
            fact for comparison in current_response.comparisons for fact in comparison.evidence
        ]
        final_response = current_response.model_copy(update={
            "trace_id": state.get("trace_id", current_response.trace_id),
            "capability_plan": [CapabilityPlanEntry(
                capability_id=item.capability_id,
                status=item.status,
                stage=item.stage,
                reason=item.reason,
                dependencies=list(item.dependencies),
                missing_dependencies=list(item.missing_dependencies),
            ) for item in state.get("capability_plan", ())],
            "capability_results": state.get("capability_results", []),
            "evidence_facts": facts,
            "issues": [
                *current_response.issues,
                *[
                    reason for reason in state.get("degradation_reasons", [])
                    if reason not in current_response.issues
                ],
            ],
        })
        _log_decision(final_response, "final")
        return {"response": final_response}

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
        logger.info("review workflow started trace_id=%s profile=%s", initial_state["trace_id"], resolved_profile.version)
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
