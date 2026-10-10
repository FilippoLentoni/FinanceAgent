"""Graph nodes: start_turn -> route -> plan -> [confirm] -> tool_call -> plan ... -> narrate ->
claim_check -> respond (design D1).

Streaming: nodes push ``progress``, ``tool_call``, ``tool_result_summary`` and ``token`` events through
LangGraph's custom stream writer (a no-op in non-streaming runs), so streamed and non-streamed runs
execute the identical graph and produce the identical ``final`` payload.

Budget: before EVERY provider invocation of kind ``bedrock`` the node runs
:meth:`~finplan_agent.budget.guard.BudgetGuard.preflight`. A refusal makes no Bedrock call: planning
degrades to the deterministic (fixture) planner and narration to an evidence-only answer with
``narrative_status`` ``budget_exceeded``; the ``BUDGET_EXCEEDED`` envelope is returned in ``final``.
Token caps apply to every model invocation; the tool-call cap applies to every provider kind (the
fixture provider calls no model, so it has no token or cost check).
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from .. import GRAPH_VERSION
from ..core.errors import AgentError
from ..core.ids import idempotency_key
from ..providers.base import GenerateRequest, GenerateResult, ToolCall, ToolSpec, Usage, text_of
from ..providers.fixture import FixtureProvider
from .claim_check import claim_check
from .policy import SYSTEM_PROMPT, classify_request, tool_call_refusal
from .recommendations import explanation_requested, recommendation_arguments, recommendation_reference, render_recommendation, render_recommendation_explanation, replay_matches, supplied_state_recommendation, target_cash_value
from .portfolio import portfolio_plan, render_portfolio_results, selected_portfolio_skills
from ..tools.portfolio import CLASSICAL_TOOLS, LIFECYCLE_TOOLS
from .lifecycle import lifecycle_plan
from ..skills import provider_tool_specs
from .state import TURN_RESET, AgentContext, AgentState

__all__ = ["start_turn", "route", "plan", "confirm", "tool_call", "narrate", "check_claims", "respond", "compact", "NON_TERMINAL_JOB_STATES"]

NON_TERMINAL_JOB_STATES = frozenset({"awaiting_approval", "queued", "starting", "running", "stopping"})
MAX_PLAN_ROUNDS = 4
_SUMMARY_KEYS = 40


def _emit(event: dict[str, Any]) -> None:
    get_stream_writer()(event)


def _progress(stage: str) -> None:
    _emit({"type": "progress", "stage": stage})


def _err(exc: AgentError, state: AgentState) -> dict[str, Any]:
    return exc.to_envelope(state.get("correlation_id") or "corr-unknown-000")


def compact(result: Any, max_chars: int) -> tuple[Any, dict[str, Any]]:
    """(compact result kept in the checkpoint, scalar summary). Large results keep scalars and
    identifiers only; lists become their length (figures are re-read through tools by ID)."""
    summary: dict[str, Any] = {}
    if isinstance(result, dict):
        for k, v in result.items():
            if len(summary) >= _SUMMARY_KEYS:
                break
            if isinstance(v, (str, int, float, bool)) or v is None:
                summary[k] = v
            elif isinstance(v, list):
                summary[f"{k}_count"] = len(v)
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if (kk.endswith("_id") or kk in ("checksum", "state", "status", "kind")) and isinstance(vv, (str, int, float, bool)):
                        summary[f"{k}.{kk}"] = vv
    text = json.dumps(result, sort_keys=True, default=str)
    kept = result if len(text) <= max_chars else {"compacted": True, **summary}
    return kept, summary


def _usage_add(acc: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    out = dict(acc)
    for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        out[k] = int(out.get(k, 0)) + int(record.get(k, 0))
    out["estimated_cost_usd"] = round(float(out.get("estimated_cost_usd", 0.0)) + float(record.get("estimated_cost_usd", 0.0)), 8)
    out["provider_kind"] = record.get("provider_kind")
    out["model_id"] = record.get("model_id")
    out["invocations"] = int(out.get("invocations", 0)) + 1
    return out


def _invoke_provider(state: AgentState, ctx: AgentContext, request: GenerateRequest, *, stream_tokens: bool) -> tuple[Any, dict[str, Any]]:
    """Guarded provider call. Returns (result, state updates). Raises AgentError on refusal/failure."""
    if ctx.provider.kind != "fixture":
        # The fixture provider calls no model and costs nothing: only real model invocations are
        # checked (and every one of them is, before the call).
        est_in = ctx.provider.estimate_input_tokens(request)
        worst = ctx.guard.worst_case(est_in, request.max_tokens)
        ctx.guard.preflight(
            worst=worst,
            turn_tokens=int(state.get("turn_tokens", 0)),
            session_tokens=int(state.get("session_tokens", 0)),
            session_spent_usd=float(state.get("session_spent_usd", 0.0)),
            correlation_id=state.get("correlation_id", ""),
        )
    on_token = (lambda t: _emit({"type": "token", "text": t})) if stream_tokens else None
    result = ctx.provider.generate(request, on_token=on_token)
    rec = result.usage_record()
    if ctx.on_spend is not None and result.estimated_cost_usd:
        ctx.on_spend(result.estimated_cost_usd)
    tokens = result.usage.total
    updates = {
        "turn_tokens": int(state.get("turn_tokens", 0)) + tokens,
        "session_tokens": int(state.get("session_tokens", 0)) + tokens,
        "session_spent_usd": round(float(state.get("session_spent_usd", 0.0)) + result.estimated_cost_usd, 8),
        "turn_usage": _usage_add(state.get("turn_usage") or {}, rec),
    }
    return result, updates


# ============================================================================ nodes
def start_turn(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    return {
        **TURN_RESET,
        "graph_version": GRAPH_VERSION,
        "environment": ctx.environment,
        "turn": int(state.get("turn", 0)) + 1,
        "session_tokens": int(state.get("session_tokens", 0)),
        "session_spent_usd": float(state.get("session_spent_usd", 0.0)),
    }


def route(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    _progress("route")
    last = next((m for m in reversed(state.get("messages", [])) if m.get("role") == "user"), {})
    refusal = classify_request(text_of(last))
    if refusal is None:
        return {}
    err = AgentError(refusal["code"], refusal["message"], {"kind": refusal["kind"]}).to_envelope(state.get("correlation_id", ""))
    return {"refusal": dict(refusal), "error": err, "status": "refused", "narrative": refusal["message"], "narrative_status": "refused"}


def after_route(state: AgentState) -> str:
    if state.get("refusal"):
        return "respond"
    # A structured explanation request runs the deterministic explanation subflow (no model planning).
    return "explain_prepare" if state.get("explanation_request") else "plan"


def plan(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _progress("plan")
    rounds = int(state.get("plan_rounds", 0)) + 1
    updates: dict[str, Any] = {"plan_rounds": rounds, "pending_calls": []}
    if rounds > MAX_PLAN_ROUNDS:
        return updates
    # A recommendation is a single fresh, read-only inference call. Do not let subsequent
    # provider drafts replace a complete allocation or silently retry failed inference.
    if not state.get("portfolio_workflow") and any(r.get("tool") == "recommend_portfolio" for r in state.get("tool_results") or []):
        return updates
    try:
        offered = ctx.offered_tools()
    except AgentError as exc:
        updates.update(error=_err(exc, state), degraded="tools_unavailable")
        return updates
    request = GenerateRequest(
        purpose="plan",
        system=SYSTEM_PROMPT,
        stable_instructions=ctx.stable_instructions,
        messages=list(state.get("messages", [])),
        max_tokens=ctx.provider_config.max_tokens_invocation,
        tools=provider_tool_specs(offered),
        temperature=ctx.provider_config.temperature,
    )
    arguments = recommendation_arguments(request.messages)
    reference = recommendation_reference(request.messages)
    workflow = lifecycle_plan(state, ctx.session_id) or portfolio_plan(state, ctx.session_id)
    if workflow is not None:
        updates["portfolio_workflow"] = workflow["workflow"]
        if workflow.get("clarification"):
            updates["draft_text"] = workflow["clarification"]
        calls = workflow["calls"]
        missing = [c.name for c in calls if not any(t.name == c.name for t in offered)]
        if missing:
            updates.update(error=_err(AgentError.dependency("Required MCP tools are unavailable.", tools=missing), state), status="failed", draft_text="The required portfolio MCP tools are unavailable: " + ", ".join(missing) + ".")
            return updates
        skills = selected_portfolio_skills(calls, ctx.skills)
        existing = state.get("skills_used") or []
        updates["skills_used"] = existing + [s for s in skills if s not in existing]
        for skill in skills:
            _emit({"type": "progress", "stage": "skill_selected", "skill": skill})
        result = GenerateResult(text="", tool_calls=tuple(calls), usage=Usage(), stop_reason="tool_use", provider_kind="fixture", model_id=None)
    elif explanation_requested(request.messages) and reference is None and arguments is None:
        updates["draft_text"] = "I need a successful portfolio recommendation in this conversation before I can explain it. Ask for a recommendation first, or provide the complete original portfolio scenario. I will re-read its policy and market evidence rather than infer figures from conversation text."
        return updates
    elif arguments is not None and any(t.name == "recommend_portfolio" for t in offered):
        calls = [ToolCall(id=f"recommend-{state.get('turn', 1)}", name="recommend_portfolio", arguments=arguments)]
        if reference:
            if not any(t.name == "query_market_data" for t in offered):
                updates.update(error=_err(AgentError.dependency("The market-evidence MCP tool is unavailable."), state), status="failed")
                return updates
            old = reference["recommendation"]
            calls.append(ToolCall(id=f"market-{state.get('turn', 1)}", name="query_market_data", arguments={"input_snapshot_id": old["input_snapshot_id"], "start_date": old["as_of"], "end_date": old["as_of"]}))
            updates["recommendation_reference"] = reference
        skills = [dict(s) for s in ctx.skills if s["name"] == "recommend-portfolio"]
        updates["skills_used"] = skills
        for skill in skills:
            _emit({"type": "progress", "stage": "skill_selected", "skill": skill})
        result = GenerateResult(text="", tool_calls=tuple(calls), usage=Usage(), stop_reason="tool_use", provider_kind="fixture", model_id=None)
    else:
        try:
            result, usage_updates = _invoke_provider(state, ctx, request, stream_tokens=False)
            updates.update(usage_updates)
        except AgentError as exc:
            if exc.code != "BUDGET_EXCEEDED" and exc.code != "DEPENDENCY_UNAVAILABLE" and exc.code != "RATE_LIMITED":
                raise
            # Degrade to the deterministic planner: tool-only answer, no model call.
            updates.update(error=_err(exc, state), degraded="tool_only")
            result = FixtureProvider().generate(request)
    calls = list(result.tool_calls)
    if supplied_state_recommendation(request.messages) and any(c.name == "recommend_portfolio" and not isinstance(c.arguments.get("holdings"), dict) for c in calls):
        updates["draft_text"] = "For your explicitly supplied portfolio, please provide complete current holdings weights, cash weight, total portfolio value and historical high watermark, with an approved snapshot and completed session. I will not substitute the saved paper portfolio for your actual holdings."
        return updates
    if not calls:
        if result.text:
            updates["draft_text"] = result.text
        return updates
    try:
        ctx.guard.check_tool_calls(int(state.get("turn_tool_calls", 0)), len(calls), state.get("correlation_id", ""))
    except AgentError as exc:
        updates.update(error=_err(exc, state))
        return updates
    by_name: dict[str, ToolSpec] = {t.name: t for t in offered}
    pending: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    for c in calls:
        spec = by_name.get(c.name)
        args = dict(c.arguments)
        state_changing = True if spec is None else spec.state_changing
        if c.name == "run_portfolio_research" and args.get("dry_run", True) is True:
            state_changing = False
        if state_changing:
            args.setdefault("idempotency_key", idempotency_key(ctx.session_id, int(state.get("turn", 1)), c.name, args))
        pending.append({"id": c.id, "name": c.name, "arguments": args, "state_changing": state_changing, "offered": spec is not None})
        blocks.append({"tool_use": {"id": c.id, "name": c.name, "input": args}})
    updates["pending_calls"] = pending
    updates["messages"] = [{"role": "assistant", "content": blocks}]
    return updates


def after_plan(state: AgentState) -> str:
    pending = state.get("pending_calls") or []
    if not pending:
        return "narrate"
    return "confirm" if any(p["state_changing"] for p in pending) else "tool_call"


def confirm(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """LangGraph interrupt for state-changing calls: the exact arguments and idempotency keys are shown,
    and the session resumes with ``{"approve": true|false}``. Declining calls nothing."""
    pending = state.get("pending_calls") or []
    changing = [p for p in pending if p["state_changing"]]
    request = {"type": "confirmation_required", "calls": [{"tool": p["name"], "arguments": p["arguments"], "idempotency_key": p["arguments"].get("idempotency_key")} for p in changing]}
    paper = next((r.get("result", {}).get("decision") for r in reversed(state.get("tool_results") or []) if r.get("tool") == "get_portfolio_decision" and r.get("ok")), None)
    if paper and any(p["name"] == "resolve_portfolio_decision" for p in changing):
        request["paper_decision"] = paper
    answer = interrupt(request)
    approved = isinstance(answer, dict) and answer.get("approve") is True
    if approved:
        pending = [{**p, "arguments": {**p["arguments"], "confirmed_by_user": True}} if p["name"] == "resolve_portfolio_decision" else p for p in pending]
        return {"pending_calls": pending, "confirmation": {"decision": "approved", "calls": request["calls"]}}
    keep = [p for p in pending if not p["state_changing"]]
    results = [{"tool_result": {"id": p["id"], "name": p["name"], "content": {"declined": True}, "status": "error"}} for p in changing]
    return {
        "confirmation": {"decision": "declined", "calls": request["calls"]},
        "declined": [{"turn": state.get("turn"), "calls": request["calls"]}],
        "pending_calls": keep,
        "messages": [{"role": "user", "content": results}],
        "tool_results": list(state.get("tool_results") or []) + [{"id": p["id"], "tool": p["name"], "ok": False, "declined": True, "summary": {}, "error": None} for p in changing],
    }


def after_confirm(state: AgentState) -> str:
    return "tool_call" if state.get("pending_calls") else "narrate"


def tool_call(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _progress("tool_call")
    results = list(state.get("tool_results") or [])
    blocks: list[dict[str, Any]] = []
    in_progress = state.get("in_progress")
    count = int(state.get("turn_tool_calls", 0))
    for p in state.get("pending_calls") or []:
        name, args = p["name"], p["arguments"]
        refusal = tool_call_refusal(name, args, denied=ctx.catalog.is_denied(name) or not p.get("offered", True))
        _emit({"type": "tool_call", "tool": name, "arguments": args, "id": p["id"]})
        if refusal is not None:
            env = AgentError(refusal["code"], refusal["message"], {"kind": refusal["kind"], "tool": name}).to_envelope(state.get("correlation_id", ""))
            entry = {"id": p["id"], "tool": name, "ok": False, "summary": {}, "error": env, "numbers": []}
            content: Any = {"error": {"code": env["code"], "message": env["message"]}}
        else:
            outcome = ctx.tools.call_tool(name, args)
            count += 1
            if outcome.ok:
                kept, summary = compact(outcome.result, 65536 if name in CLASSICAL_TOOLS or name in LIFECYCLE_TOOLS or name in ("recommend_portfolio", "query_market_data") else ctx.max_result_chars)
                entry = {"id": p["id"], "tool": name, "ok": True, "summary": summary, "result": kept, "error": None}
                content = kept
                if isinstance(outcome.result, dict) and outcome.result.get("run_id") and outcome.result.get("state") in NON_TERMINAL_JOB_STATES:
                    in_progress = {"run_id": outcome.result["run_id"], "state": outcome.result["state"], "tool": name}
            else:
                err = outcome.error or {}
                entry = {"id": p["id"], "tool": name, "ok": False, "summary": {}, "error": {k: err.get(k) for k in ("code", "message", "retryable", "details", "correlation_id") if k in err}}
                content = {"error": {"code": err.get("code"), "message": err.get("message")}}
            if outcome.extra.get("gateway"):
                entry["gateway"] = outcome.extra["gateway"]
        results.append(entry)
        _emit({"type": "tool_result_summary", "tool": name, "id": p["id"], "ok": entry["ok"], "gateway": entry.get("gateway"), "summary": entry["summary"], "error_code": (entry.get("error") or {}).get("code")})
        blocks.append({"tool_result": {"id": p["id"], "name": name, "content": content, "status": "success" if entry["ok"] else "error"}})
    return {"tool_results": results, "messages": [{"role": "user", "content": blocks}], "pending_calls": [], "turn_tool_calls": count, "in_progress": in_progress}


def after_tool_call(state: AgentState) -> str:
    # A long-running job ends the turn with its run_id (spec "Long tool job"): no waiting.
    if state.get("in_progress"):
        return "narrate"
    return "plan"


def _evidence(state: AgentState) -> tuple[dict[str, Any], ...]:
    out = []
    for r in state.get("tool_results") or []:
        item: dict[str, Any] = {"tool": r["tool"], "summary": r.get("summary") or {}}
        if r.get("tool") == "recommend_portfolio" and r.get("ok"):
            item["recommendation"] = (r.get("result") or {}).get("recommendation")
        if r.get("error"):
            item["error"] = {"code": r["error"].get("code")}
        if r.get("declined"):
            item["declined"] = True
        out.append(item)
    return tuple(out)


def narrate(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    _progress("narrate")
    if state.get("portfolio_workflow"):
        text, _ = render_portfolio_results(state)
        _emit({"type": "token", "text": text})
        failed = next((r for r in state.get("tool_results") or [] if not r.get("ok") and not r.get("declined")), None)
        return {"narrative": text, "narrative_status": "generated", **({"status": "failed", "error": failed.get("error")} if failed else {})}
    for item in reversed(state.get("tool_results") or []):
        if item.get("tool") != "recommend_portfolio":
            continue
        rec = (item.get("result") or {}).get("recommendation") if item.get("ok") else None
        reference = state.get("recommendation_reference")
        if reference:
            market = next((r for r in state.get("tool_results", []) if r.get("tool") == "query_market_data"), {})
            snapshot = (market.get("result") or {}).get("snapshot") or {}
            if not rec or not market.get("ok") or market.get("result", {}).get("partial") or snapshot.get("status") != "approved" or snapshot.get("input_snapshot_id") != reference["recommendation"]["input_snapshot_id"]:
                error = item.get("error") if not rec else market.get("error")
                error = error or _err(AgentError("PRECONDITION_FAILED", "The original policy or market evidence is unavailable; the recommendation cannot be explained from fresh evidence."), state)
                return {"status": "failed", "error": error, "narrative": "I could not re-read the original recommendation's policy and approved market evidence. No explanation or portfolio change was inferred from old conversation text.", "narrative_status": "unavailable"}
            if not replay_matches(reference, rec):
                return {"status": "failed", "error": _err(AgentError("PRECONDITION_FAILED", "The selected policy or portfolio state changed; the original recommendation could not be reproduced.", {"reason": "recommendation_replay_mismatch"}), state), "narrative": "The selected policy or saved portfolio state changed. I cannot present the new result as an explanation of the original recommendation. Request a new daily recommendation to use the current state.", "narrative_status": "unavailable"}
            text = render_recommendation_explanation(rec)
            _emit({"type": "token", "text": text})
            return {"narrative": text, "narrative_status": "generated"}
        text = render_recommendation(rec) if rec else f"The portfolio policy could not produce a recommendation: {(item.get('error') or {}).get('code', 'DEPENDENCY_UNAVAILABLE')}. No portfolio changes were made."
        if rec and item.get("result", {}).get("decision_id"):
            text += "\n\nIssued decision: " + item["result"]["decision_id"] + ". You can inspect, accept or reject this paper recommendation."
        _emit({"type": "token", "text": text})
        return {"narrative": text, "narrative_status": "generated"}
    if state.get("draft_text"):
        _emit({"type": "token", "text": state["draft_text"]})
        return {"narrative": state["draft_text"], "narrative_status": "generated"}
    request = GenerateRequest(
        purpose="narrate",
        system=SYSTEM_PROMPT,
        stable_instructions=ctx.stable_instructions,
        messages=[m for m in state.get("messages", []) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))][-1:],
        max_tokens=ctx.provider_config.max_tokens_invocation,
        temperature=ctx.provider_config.temperature,
        evidence=_evidence(state),
    )
    try:
        result, updates = _invoke_provider(state, ctx, request, stream_tokens=True)
    except AgentError as exc:
        status = "budget_exceeded" if exc.code == "BUDGET_EXCEEDED" else "unavailable"
        return {"narrative": "", "narrative_status": status, "error": state.get("error") or _err(exc, state)}
    return {**updates, "narrative": result.text, "narrative_status": "generated"}


def check_claims(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    _progress("claim_check")
    if not state.get("narrative") or state.get("narrative_status") != "generated":
        return {"claim_check": {"passed": True, "checked_figures": 0, "removed_figures": []}}
    values = [r.get("result", r.get("summary")) for r in state.get("tool_results") or [] if r.get("ok")]
    if state.get("portfolio_workflow"):
        values.extend(render_portfolio_results(state)[1])
    # The cash dollar target is the one derived figure in the deterministic recommendation
    # renderer: verify the same exact multiplication of fresh producer value and target weight.
    for item in state.get("tool_results") or []:
        if item.get("ok") and item.get("tool") == "recommend_portfolio":
            rec = (item.get("result") or {}).get("recommendation")
            if rec:
                values.append({"computed_target_cash": target_cash_value(rec)})
    res = claim_check(state["narrative"], values)
    return {"narrative": res.text, "claim_check": res.record()}


def respond(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    status = state.get("status")
    if status == "running":
        status = "in_progress" if state.get("in_progress") else "completed"
    usage = dict(state.get("turn_usage") or {})
    usage.setdefault("provider_kind", ctx.provider.kind)
    usage.setdefault("model_id", ctx.provider.model_id)
    for key in ("invocations", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        usage.setdefault(key, 0)
    usage.setdefault("estimated_cost_usd", 0.0)
    usage["tool_calls"] = int(state.get("turn_tool_calls", 0))
    if ctx.on_usage is not None:
        ctx.on_usage(usage)
    reply = state.get("narrative") or ""
    msgs = [{"role": "assistant", "content": [{"text": reply}]}] if reply else []
    update = {"status": status, "turn_usage": usage, "messages": msgs}
    if ctx.durable_activity:
        from .activity import archive_turn
        archived = archive_turn(state, ctx, status, usage)
        if archived.ok:
            update["activity_receipt"] = archived.result
        else:
            update.update(status="failed", error=archived.error, degraded="activity_archive_unavailable")
    return update
