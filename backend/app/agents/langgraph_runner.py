from __future__ import annotations

from dataclasses import fields
from typing import Any, TypedDict

from app.core.telemetry import span


class GraphState(TypedDict, total=False):
    incident_id: str
    incident: dict[str, Any]
    events: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    telemetry: dict[str, Any]
    logs: dict[str, Any]
    topology: dict[str, Any]
    dependencies: dict[str, Any]
    evidence: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    rca: dict[str, Any]
    remediation: dict[str, Any]
    validation: dict[str, Any]
    investigator_summary: str
    final_report: str
    step_count: int
    tool_calls: list[dict[str, Any]]
    llm_errors: list[dict[str, Any]]
    agent_mode: str


NODES = ("triage", "plan", "investigator", "rca", "remediation", "report")


async def build_and_run(parent, state_obj):
    """Run the investigation as a LangGraph StateGraph; each node delegates to the InvestigationGraph method."""
    from langgraph.graph import END, START, StateGraph

    from app.agents.state import InvestigationState

    def snapshot(s: InvestigationState) -> GraphState:
        return {f.name: getattr(s, f.name) for f in fields(s)}  # type: ignore[return-value]

    def make_node(name: str):
        async def wrapped(state: GraphState):
            current = InvestigationState(**state)
            with span(f"agent.{name}", {"incident.id": current.incident_id}):
                await getattr(parent, name)(current)
            return snapshot(current)
        return wrapped

    graph = StateGraph(GraphState)
    for name in NODES:
        graph.add_node(name, make_node(name))
    graph.add_edge(START, NODES[0])
    for a, b in zip(NODES, NODES[1:]):
        graph.add_edge(a, b)
    graph.add_edge(NODES[-1], END)

    result = await graph.compile().ainvoke(snapshot(state_obj))
    return InvestigationState(**result)
