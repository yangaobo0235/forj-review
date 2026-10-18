"""能力的规划与阶段执行；独立于 LangGraph 的图装配和最终展示。"""

from dataclasses import replace
from typing import Any

from forj_review_assistant.capabilities import CapabilityRegistry
from forj_review_assistant.capabilities.specs import ReviewExecutionContext
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import FieldObservation, QrCheck
from forj_review_assistant.evidence.collection import collect_observations
from forj_review_assistant.evidence.comparison.check_results import qr_review_checks, unique_checks
from forj_review_assistant.runtime.workflow.planner import plan_capabilities
from forj_review_assistant.runtime.workflow.state import ReviewState


def observations(state: ReviewState) -> tuple[list[FieldObservation], list[QrCheck]]:
    return collect_observations(
        state["request"],
        state["profile"],
        state.get("batch", AgentBatchResult()),
        state.get("capability_results", ()),
        state.get("qr_checks", ()),
    )

def available_facts(state: ReviewState) -> set[str]:
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
    collected, _ = observations(state)
    available.update(
        item.field
        for item in collected
        if item.value not in (None, "") and not item.uncertain
    )
    return available

def plan_input_capabilities(state: ReviewState) -> dict[str, Any]:
    plan = plan_capabilities(state["profile"], available_facts(state))
    return {
        "capability_plan": tuple(item for item in plan if item.stage == "INPUT_COVERAGE"),
        "capability_results": [],
    }

def plan_remaining_capabilities(state: ReviewState) -> dict[str, Any]:
    plan = plan_capabilities(state["profile"], available_facts(state))
    executed = {result.capability_id for result in state.get("capability_results", ())}
    previous = {item.capability_id: item for item in state.get("capability_plan", ())}
    return {
        "capability_plan": tuple(
            previous[item.capability_id] if item.capability_id in executed else item
            for item in plan
        ),
        "capability_results": list(state.get("capability_results", ())),
    }

def execution_context(state: ReviewState) -> ReviewExecutionContext:
    collected, qr = observations(state)
    return ReviewExecutionContext(
        request=state["request"],
        profile=state["profile"],
        batch=state.get("batch", AgentBatchResult()),
        observations=tuple(collected),
        comparisons=tuple(state.get("comparisons", ())),
        qr_checks=tuple(qr),
        checks=tuple(
            unique_checks([*state.get("external_results", ()), *state.get("cross_checks", ())])
        ),
        material_completeness=state.get("material_completeness"),
    )

async def execute_stage(
    registry: CapabilityRegistry,
    state: ReviewState,
    stages: set[str],
) -> dict[str, Any]:
    """Execute all Profile capabilities for the requested lifecycle stages."""
    context = execution_context(state)
    capability_plan = state.get("capability_plan")
    if capability_plan is None:
        capability_plan = plan_capabilities(state["profile"], available_facts(state))
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
        result = await registry.execute(spec, item, context)
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

