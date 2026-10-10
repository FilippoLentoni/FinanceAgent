"""Bounded, evidence-first recursive research entry path for the hosted LangGraph agent."""
from __future__ import annotations

import re

from ..providers.base import ToolCall, text_of
from .recommendations import saved_portfolio_reference

_CYCLE = re.compile(r"\bca_[0-9a-f]{32}\b")
_RECURSIVE = re.compile(r"\b(?:recursive|self[ -]?improv\w*|improvement cycle|research cycle|improvement agent)\b", re.I)
_BENCHMARK = re.compile(r"\b(?:qwen|swarm|jev|typesafe)\b", re.I)
_LAUNCH = re.compile(r"\b(?:run|launch|start|trigger)\b[^.!?]{0,80}\b(?:experiment|benchmark|iteration|job)\b", re.I)


def improvement_plan(state, session_id):
    messages = state.get("messages", [])
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    text = text_of(user)
    turn = state.get("turn", 1)
    workflow = state.get("portfolio_workflow")
    if workflow:
        return {"workflow": workflow, "calls": []} if workflow.get("mode") == "recursive_improvement" else None
    for block in user.get("content", []):
        request = block.get("tool_request") or {}
        if request.get("name") == "run_recursive_improvement":
            arguments = dict(request.get("arguments") or {})
            arguments.setdefault("dry_run", True)
            return {"workflow": {"mode": "recursive_improvement"}, "calls": [ToolCall(id=f"improvement-{turn}", name="run_recursive_improvement", arguments=arguments)]}
        if request:
            return None
    history = []
    for message in messages:
        for block in message.get("content", []):
            result = block.get("tool_result", {})
            if result.get("name") == "run_recursive_improvement" and result.get("status") == "success" and isinstance(result.get("content"), dict):
                history.append(result["content"])
    explicit = bool(_RECURSIVE.search(text) or _BENCHMARK.search(text))
    followup = bool(history and re.search(r"\b(?:cycle|iteration|next experiment|resume|continue|lineage)\b", text, re.I))
    if not explicit and not followup:
        return None
    ids = _CYCLE.findall(text)
    prior = next((row for row in reversed(history) if not ids or row.get("cycle_id") == ids[0]), None)
    cycle_id = ids[0] if ids else (prior or {}).get("cycle_id")
    # A benchmark request must inspect the named family instead of silently resuming
    # a different existing cycle. The producer binds its family/configuration at creation.
    new_benchmark = bool(_BENCHMARK.search(text) and not ids)
    if new_benchmark:
        cycle_id, prior = None, None
    arguments = {"dry_run": True}
    if cycle_id:
        arguments["cycle_id"] = cycle_id
    else:
        arguments.update(saved_portfolio_reference(text))
        arguments["query"] = text[:2000]
    requested_launch = bool(_LAUNCH.search(text)) and not re.search(r"\b(?:estimate|dry[ -]?run|cost|preview)\b", text, re.I)
    # Only offer a paid call after showing this cycle's producer estimate. The graph
    # will interrupt for approval; the MCP independently verifies identity and caps.
    ready = bool(prior and prior.get("state") == "awaiting_experiment_approval" and prior.get("cost_estimate"))
    if requested_launch and ready:
        arguments.update(dry_run=False, confirmed_by_user=True)
    return {"workflow": {"mode": "recursive_improvement"}, "calls": [ToolCall(id=f"improvement-{turn}", name="run_recursive_improvement", arguments=arguments)]}
