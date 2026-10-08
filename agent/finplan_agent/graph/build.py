"""The compiled LangGraph graph (design D1). This is the ONLY agent entry point: the architecture check
(``scripts/agent_gates.py``) rejects other agent frameworks and managed harnesses."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..explanations import nodes as ex
from . import nodes as n
from .state import AgentContext, AgentState

__all__ = ["NODES", "build_graph"]

NODES = ("start_turn", "route", "plan", "confirm", "tool_call", "narrate", "claim_check", "respond", "explain_prepare", "explain_confirm", "explain_collect", "explain_narrate")


def build_graph(checkpointer: Any) -> Any:
    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("start_turn", n.start_turn)
    g.add_node("route", n.route)
    g.add_node("plan", n.plan)
    g.add_node("confirm", n.confirm)
    g.add_node("tool_call", n.tool_call)
    g.add_node("narrate", n.narrate)
    g.add_node("claim_check", n.check_claims)
    g.add_node("respond", n.respond)
    # Explanation subflow (add-explanation-workflows design E1-E7).
    g.add_node("explain_prepare", ex.explain_prepare)
    g.add_node("explain_confirm", ex.explain_confirm)
    g.add_node("explain_collect", ex.explain_collect)
    g.add_node("explain_narrate", ex.explain_narrate)
    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "route")
    g.add_conditional_edges("route", n.after_route, {"plan": "plan", "respond": "respond", "explain_prepare": "explain_prepare"})
    g.add_conditional_edges("explain_prepare", ex.after_explain_prepare, {"explain_confirm": "explain_confirm", "explain_collect": "explain_collect", "respond": "respond"})
    g.add_conditional_edges("explain_confirm", ex.after_explain_confirm, {"explain_collect": "explain_collect", "respond": "respond"})
    g.add_conditional_edges("explain_collect", ex.after_explain_collect, {"explain_narrate": "explain_narrate", "respond": "respond"})
    g.add_edge("explain_narrate", "respond")
    g.add_conditional_edges("plan", n.after_plan, {"confirm": "confirm", "tool_call": "tool_call", "narrate": "narrate"})
    g.add_conditional_edges("confirm", n.after_confirm, {"tool_call": "tool_call", "narrate": "narrate"})
    g.add_conditional_edges("tool_call", n.after_tool_call, {"plan": "plan", "narrate": "narrate"})
    g.add_edge("narrate", "claim_check")
    g.add_edge("claim_check", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer)
