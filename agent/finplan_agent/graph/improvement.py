"""Bounded, evidence-first recursive research entry path for the hosted LangGraph agent."""
from __future__ import annotations

import re

from ..providers.base import ToolCall, text_of
from ..core.ids import idempotency_key
from .recommendations import saved_portfolio_reference

_CYCLE = re.compile(r"\bca_[0-9a-f]{32}\b")
_RECURSIVE = re.compile(r"\b(?:recursive|self[ -]?improv\w*|improvement cycle|research cycle|improvement agent)\b", re.I)
_BENCHMARK = re.compile(r"\b(?:qwen|swarm|jev|typesafe)\b", re.I)
_LAUNCH = re.compile(r"\b(?:run|launch|start|trigger)\b[^.!?]{0,80}\b(?:experiment|benchmark|iteration|job)\b", re.I)


def _benchmark_request(document, family, session_id, turn):
    from finplan_contracts.validate import validate

    request = (document.get("proposed_experiment") or {}).get("tool_request") or {}
    arguments = dict(request.get("arguments") or {})
    if request.get("name") != "submit_experiment" or arguments.get("job_type") != family:
        return None
    expected_strategy = {"swarm_mode_a": "qwen_swarm", "jev_backtest": "jev"}.get(family)
    if (arguments.get("configuration") or {}).get("payload", {}).get("strategy") != expected_strategy:
        return None
    arguments.update(dry_run=True, purpose="research")
    arguments["idempotency_key"] = idempotency_key(session_id, turn, "submit_experiment", arguments)
    return arguments if validate(arguments, "tools/submit-experiment-request").valid else None


def _benchmark_history(messages):
    requests, out = {}, []
    for message in messages:
        for block in message.get("content", []):
            request = block.get("tool_use") or {}
            if request.get("name") == "submit_experiment":
                requests[request.get("id")] = request.get("input") or {}
            result = block.get("tool_result") or {}
            arguments = requests.get(result.get("id"))
            document = result.get("content")
            if result.get("name") == "submit_experiment" and result.get("status") == "success" and arguments and isinstance(document, dict):
                out.append((arguments, document))
    return out


def improvement_plan(state, session_id):
    messages = state.get("messages", [])
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    text = text_of(user)
    turn = state.get("turn", 1)
    workflow = state.get("portfolio_workflow")
    if workflow:
        if workflow.get("mode") == "benchmark_query":
            results = state.get("tool_results") or []
            preview = next((row.get("result", {}) for row in reversed(results) if row.get("tool") == "run_recursive_improvement" and row.get("ok")), None)
            if preview is not None:
                arguments = _benchmark_request(preview, workflow["family"], session_id, turn)
                if arguments:
                    return {"workflow": {**workflow, "mode": "benchmark_estimate"}, "calls": [ToolCall(id=f"benchmark-{turn}", name="submit_experiment", arguments=arguments)]}
                return {"workflow": {**workflow, "mode": "benchmark_estimate"}, "calls": [], "clarification": "A complete matching sandbox benchmark request is unavailable. The retained capability evidence explains the configuration or readiness gap; no benchmark was launched."}
            return {"workflow": workflow, "calls": []}
        return {"workflow": workflow, "calls": []} if workflow.get("mode") in {"recursive_improvement", "benchmark_estimate", "benchmark_launch"} else None
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
    benchmark_history = _benchmark_history(messages)
    followup = bool(history and re.search(r"\b(?:cycle|iteration|next experiment|resume|continue|lineage)\b", text, re.I)) or bool(benchmark_history and _LAUNCH.search(text) and re.search(r"\bbenchmark\b", text, re.I))
    if not explicit and not followup:
        return None
    family = "jev_backtest" if re.search(r"\b(?:jev|typesafe)\b", text, re.I) else "swarm_mode_a" if _BENCHMARK.search(text) else None
    benchmark = next(((args, doc) for args, doc in reversed(benchmark_history) if not family or args.get("job_type") == family), None)
    if benchmark and _LAUNCH.search(text) and not re.search(r"\b(?:estimate|dry[ -]?run|cost|preview)\b", text, re.I):
        previous_args, estimate = benchmark
        if previous_args.get("dry_run") is True and estimate.get("cost_estimate") and (estimate.get("tool_limit") or {}).get("within_limit") is True:
            arguments = {**previous_args, "dry_run": False}
            arguments["idempotency_key"] = idempotency_key(session_id, turn, "submit_experiment", arguments)
            return {"workflow": {"mode": "benchmark_launch"}, "calls": [ToolCall(id=f"benchmark-{turn}", name="submit_experiment", arguments=arguments)]}
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
    workflow = {"mode": "benchmark_query", "family": family} if new_benchmark else {"mode": "recursive_improvement"}
    return {"workflow": workflow, "calls": [ToolCall(id=f"improvement-{turn}", name="run_recursive_improvement", arguments=arguments)]}
