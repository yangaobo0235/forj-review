"""Common single-capability LangGraph wrapper."""

from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from forj_review_assistant.capabilities.specs import (
    CapabilitySpec,
    ReviewExecutionContext,
)
from forj_review_assistant.contracts.review import CapabilityResult


class CapabilitySubgraphState(TypedDict, total=False):
    context: ReviewExecutionContext
    spec: CapabilitySpec
    result: CapabilityResult


def build_capability_subgraph(
    handler: Callable[[ReviewExecutionContext, CapabilitySpec], Awaitable[CapabilityResult]],
) -> Callable[[ReviewExecutionContext, CapabilitySpec], Awaitable[CapabilityResult]]:
    """Compile a reusable capability graph around a domain handler.

    Domain subgraphs can replace the single ``execute`` node with extraction,
    normalization and validation nodes later without changing the registry
    contract.
    """

    async def execute(state: CapabilitySubgraphState) -> dict[str, CapabilityResult]:
        return {"result": await handler(state["context"], state["spec"])}

    builder = StateGraph(CapabilitySubgraphState)
    builder.add_node("execute", execute)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    graph = builder.compile()

    async def invoke(
        context: ReviewExecutionContext,
        spec: CapabilitySpec,
    ) -> CapabilityResult:
        state = await graph.ainvoke({"context": context, "spec": spec})
        return state["result"]

    return invoke
