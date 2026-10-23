"""Independent durable configuration graph; nodes hold references, not business facts."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    task_id: str
    route: str


def build_configuration_graph(nodes, checkpointer):
    builder = StateGraph(State)
    for name in (
        "load_context",
        "reason",
        "execute_tools",
        "await_user",
        "await_browser",
        "verify_candidate",
        "await_confirmation",
        "commit_configuration",
    ):
        builder.add_node(name, getattr(nodes, name))
    builder.add_edge(START, "load_context")
    for name in ("load_context", "reason", "execute_tools", "verify_candidate"):
        builder.add_conditional_edges(
            name,
            lambda state: state["route"],
            {
                "reason": "reason",
                "execute_tools": "execute_tools",
                "await_user": "await_user",
                "await_browser": "await_browser",
                "verify_candidate": "verify_candidate",
                "await_confirmation": "await_confirmation",
                "end": END,
            },
        )
    builder.add_edge("await_user", "load_context")
    builder.add_edge("await_browser", "load_context")
    builder.add_conditional_edges(
        "await_confirmation",
        lambda state: state["route"],
        {"end": END, "commit_configuration": "commit_configuration"},
    )
    builder.add_edge("commit_configuration", END)
    return builder.compile(checkpointer=checkpointer)
