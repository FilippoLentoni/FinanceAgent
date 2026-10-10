"""Evidence-first orchestration for the independent traditional portfolio MCP."""
from __future__ import annotations

import json
import re
from typing import Any

from ..providers.base import ToolCall, text_of
from ..skills import CLASSICAL_SKILLS
from ..tools.portfolio import CLASSICAL_TOOLS, LIFECYCLE_TOOLS
from .lifecycle import render_lifecycle
from .recommendations import _SUPPLIED_STATE, render_recommendation, saved_portfolio_reference

_CLASSICAL = re.compile(r"\b(?:traditional|classical|optimizer|optimisation|optimization|min(?:imum)?[ -]?variance|mean[ -]?variance|cvar)\b", re.I)
_WHY = re.compile(r"\b(?:why|explain|reason|rationale)\b", re.I)
_COMPARE = re.compile(r"\b(?:compare|comparison|yesterday|previous|changed|changes|plan.over.plan)\b", re.I)
_PERFORMANCE = re.compile(r"\b(?:green|red|perform\w*|actuals?|realized|realised|discrepancy|trending|return gap)\b", re.I)
_RESEARCH = re.compile(r"\b(?:literature|research|retraining|retrain|improve|improvement|modeling approach|modelling approach)\b", re.I)
_PPO = re.compile(r"\b(?:ppo|reinforcement|rl)\b", re.I)
_ID = re.compile(r"\bca_[0-9a-f]{32}\b")
_INSTRUMENTS = {"GOOGL": r"\b(?:google|googl)\b", "NVDA": r"\b(?:nvidia|nvda)\b", "AAPL": r"\b(?:apple|aapl)\b", "NFLX": r"\b(?:netflix|nflx)\b", "VOO": r"\bvoo\b"}


def _history(messages: list[dict]) -> list[dict]:
    out = []
    for message in messages:
        for block in message.get("content", []):
            result = block.get("tool_result", {})
            if result.get("name") in CLASSICAL_TOOLS and result.get("status") == "success" and isinstance(result.get("content"), dict):
                out.append(result["content"])
    return out


def _call(name: str, arguments: dict, turn: int, index: int = 0) -> ToolCall:
    return ToolCall(id=f"portfolio-{turn}-{index}", name=name, arguments=arguments)


def _reference(history: list[dict], kind: str) -> dict | None:
    return next((row for row in reversed(history) if row.get("analysis_kind") == kind), None)


def _latest_recommendation_family(messages: list[dict]) -> tuple[str | None, bool]:
    for message in reversed(messages):
        for block in reversed(message.get("content", [])):
            result = block.get("tool_result", {})
            if result.get("name") in ("recommend_classical_portfolio", "recommend_portfolio"):
                return result["name"], result.get("status") == "success"
    return None, False


def _plan_ids(rows: list[dict], *, distinct_dates: bool) -> tuple[str, str] | None:
    plans = [row for row in rows if row.get("analysis_kind") == "recommendation" and row.get("analysis_id")]
    seen = set()
    plans = [r for r in reversed(plans) if not (r["analysis_id"] in seen or seen.add(r["analysis_id"]))]
    for i, current in enumerate(plans):
        for previous in plans[i+1:]:
            a, b = previous.get("recommendation", previous), current.get("recommendation", current)
            a_algorithm, b_algorithm = a.get("strategy", a.get("algorithm")), b.get("strategy", b.get("algorithm"))
            if a_algorithm != b_algorithm or previous.get("portfolio_id") != current.get("portfolio_id"):
                continue
            if distinct_dates and (not a.get("as_of") or a.get("as_of") == b.get("as_of")):
                continue
            if a.get("as_of", "") > b.get("as_of", ""):
                previous, current = current, previous
            return previous["analysis_id"], current["analysis_id"]
    return None


def portfolio_plan(state: dict, session_id: str) -> dict[str, Any] | None:
    """Return exact tool calls or clarification; None retains the existing PPO/research planner."""
    messages = state.get("messages", [])
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    text = text_of(user)
    turn = state.get("turn", 1)
    workflow = state.get("portfolio_workflow")
    results = state.get("tool_results") or []
    if workflow:
        if any(not r.get("ok") for r in results):
            return {"workflow": workflow, "calls": []}
        if workflow["mode"] == "compare_lookup" and results:
            rows = (results[-1].get("result") or {}).get("analyses", [])
            # The backend lists newest first; history is oldest first.
            pair = _plan_ids(list(reversed(rows)), distinct_dates=workflow.get("distinct_dates", False))
            if pair:
                return {"workflow": {"mode": "compare"}, "calls": [_call("compare_classical_plans", {"previous_analysis_id": pair[0], "current_analysis_id": pair[1]}, turn, 1)]}
            return {"workflow": workflow, "calls": [], "clarification": "I could not find two compatible stored recommendations for the requested dates. Provide both analysis IDs, or generate the missing dated plan. I will not invent yesterday's recommendation."}
        if workflow["mode"] == "performance_context" and results and not any(r.get("tool") == "research_market_events" for r in results):
            report = next((r.get("result", {}) for r in results if r.get("tool") == "evaluate_classical_performance"), {})
            if report.get("analysis_id"):
                return {"workflow": workflow, "calls": [_call("research_market_events", {"analysis_id": report["analysis_id"]}, turn, 1)]}
        return {"workflow": workflow, "calls": []}
    for block in user.get("content", []):
        request = block.get("tool_request") or {}
        if request.get("name") in CLASSICAL_TOOLS:
            return {"workflow": {"mode": "direct"}, "calls": [_call(request["name"], dict(request.get("arguments") or {}), turn)]}
        if request:
            return None
    history = _history(messages)
    family, successful = _latest_recommendation_family(messages)
    latest = _reference(history, "recommendation") if family == "recommend_classical_portfolio" and successful else None
    research = _reference(history, "research")
    ids = _ID.findall(text)
    classical = bool(_CLASSICAL.search(text))
    both = classical and bool(_PPO.search(text))
    current = latest["analysis_id"] if latest else None
    launch = bool(re.search(r"\b(?:run|launch|trigger|estimate)\b[^.!?]{0,80}\b(?:experiment|sandbox|research)\b", text, re.I))
    relevant = classical or bool(ids) or bool(family == "recommend_classical_portfolio" and not _PPO.search(text)) or bool(research and launch)
    if not relevant and not _RESEARCH.search(text):
        return None
    if re.search(r"\b(?:feedback|record my feedback|note my feedback)\b", text, re.I):
        from ..core.ids import idempotency_key
        aid = ids[0] if ids else current
        if not aid:
            return {"workflow": {"mode": "feedback"}, "calls": [], "clarification": "Provide the analysis ID this feedback concerns, or ask for a traditional recommendation first."}
        args = {"analysis_id": aid, "text": text[:8000]}
        args["idempotency_key"] = idempotency_key(session_id, turn, "submit_portfolio_feedback", args)
        return {"workflow": {"mode": "feedback"}, "calls": [_call("submit_portfolio_feedback", args, turn)]}
    if re.search(r"\b(?:run|launch|trigger|estimate)\b[^.!?]{0,80}\b(?:experiment|sandbox|research)\b", text, re.I):
        review = ids[0] if ids else (research or {}).get("analysis_id")
        if not review:
            return {"workflow": {"mode": "research"}, "calls": [_call("research_portfolio_models", {"query": text[:2000]}, turn)]}
        prior = _reference(history, "research_run")
        paid = bool(prior and prior.get("review_id") == review and prior.get("dry_run") is True and not re.search(r"\b(?:estimate|dry.run)\b", text, re.I))
        args = {"review_id": review, "dry_run": not paid}
        if paid:
            args["confirmed_by_user"] = True
        return {"workflow": {"mode": "research_run"}, "calls": [_call("run_portfolio_research", args, turn)]}
    if _RESEARCH.search(text) and not _WHY.search(text):
        return {"workflow": {"mode": "research"}, "calls": [_call("research_portfolio_models", {"query": text[:2000]}, turn)]}
    if _PERFORMANCE.search(text) and not both:
        aid = ids[0] if ids else current
        if not aid:
            return {"workflow": {"mode": "performance"}, "calls": [], "clarification": "I need the issued traditional recommendation's analysis ID to compare its plan with observed performance."}
        mode = "performance_context" if _WHY.search(text) or re.search(r"\b(?:world|news|deep dive)\b", text, re.I) else "performance"
        return {"workflow": {"mode": mode}, "calls": [_call("evaluate_classical_performance", {"analysis_id": aid}, turn)]}
    if _COMPARE.search(text) and not both:
        distinct = bool(re.search(r"\b(?:yesterday|today|previous day)\b", text, re.I))
        pair = tuple(ids[:2]) if len(ids) >= 2 else _plan_ids(history, distinct_dates=distinct)
        if pair:
            return {"workflow": {"mode": "compare"}, "calls": [_call("compare_classical_plans", {"previous_analysis_id": pair[0], "current_analysis_id": pair[1]}, turn)]}
        args = {"kind": "recommendation", "limit": 30}
        if latest and latest.get("portfolio_id"):
            args["portfolio_id"] = latest["portfolio_id"]
        return {"workflow": {"mode": "compare_lookup", "distinct_dates": distinct}, "calls": [_call("list_classical_analyses", args, turn)]}
    if _WHY.search(text) and not both:
        aid = ids[0] if ids else current
        if not aid:
            return {"workflow": {"mode": "why"}, "calls": [], "clarification": "I need an issued traditional recommendation's analysis ID before I can compute its keep counterfactual and attribution."}
        args = {"analysis_id": aid}
        for instrument, pattern in _INSTRUMENTS.items():
            if re.search(pattern, text, re.I):
                args["instrument_id"] = instrument
                break
        return {"workflow": {"mode": "why"}, "calls": [_call("explain_classical_recommendation", args, turn)]}
    if classical and re.search(r"\b(?:recommend\w*|invest\w*|allocat\w*|buy|sell|plan\w*)\b", text, re.I):
        if _SUPPLIED_STATE.search(text):
            return {"workflow": {"mode": "recommend"}, "calls": [], "clarification": "Please supply your complete portfolio state and approved snapshot/date in structured tool arguments. I will not replace your stated holdings with the saved paper book."}
        algorithm = "mean_variance" if re.search(r"\bmean[ -]?variance\b", text, re.I) else "cvar" if re.search(r"\bcvar\b", text, re.I) else "min_variance"
        portfolio = saved_portfolio_reference(text)
        calls = [_call("recommend_classical_portfolio", {"algorithm": algorithm, **portfolio}, turn)]
        if both:
            calls.insert(0, _call("recommend_portfolio", portfolio, turn, 1))
        return {"workflow": {"mode": "both" if both else "recommend"}, "calls": calls}
    if ids:
        return {"workflow": {"mode": "retrieve"}, "calls": [_call("get_classical_analysis", {"analysis_id": ids[0]}, turn)]}
    return None


def selected_portfolio_skills(calls: list[ToolCall], inventory: tuple) -> list[dict]:
    names = {CLASSICAL_SKILLS.get(c.name, "recommend-portfolio" if c.name == "recommend_portfolio" else "") for c in calls}
    return [dict(skill) for skill in inventory if skill["name"] in names]


def _number(value: Any, digits: int = 6) -> str:
    return "not_available" if value is None else f"{float(value):,.{digits}f}"


def render_classical(doc: dict) -> tuple[str, list[dict]]:
    """Return the presentation and declared derived arithmetic for figure checking."""
    kind = doc.get("analysis_kind")
    parts = [doc.get("summary", "Stored traditional portfolio analysis."), f"Analysis: {doc.get('analysis_id', 'not_available')}; kind: {kind}."]
    derived = []
    rec = doc.get("recommendation")
    if rec:
        parts.append(f"Strategy: {rec['strategy']}; completed market session: {rec['as_of']}; source: {rec.get('portfolio_state', {}).get('source', 'not_available')}.")
        weights = {w["instrument_id"]: w["weight"] for w in rec["target_weights"]}
        rows = ["| Instrument | Action | Current shares | Target shares | Change in shares | Value change USD | Target weight |", "|---|---|---:|---:|---:|---:|---:|"]
        for d in rec["decisions"]:
            rows.append(f"| {d['instrument_id']} | {d['action']} | {_number(d.get('current_quantity'),4)} | {_number(d.get('target_quantity'),4)} | {_number(d.get('delta_quantity'),4)} | {_number(d['indicative_notional'],2)} | {weights[d['instrument_id']]*100:.2f}% |")
        parts.append("\n".join(rows))
        nav = rec.get("portfolio_state", {}).get("portfolio_value")
        target_cash = float(nav)*rec["cash_weight"] if nav is not None else None
        derived.append({"computed_target_cash": target_cash})
        parts.append(f"Target cash: {_number(target_cash,2)} USD / {rec['cash_weight']*100:.2f}%. Proposed trades; holdings are unchanged.")
        cfg = rec.get("settings") or rec.get("diagnostics", {}).get("settings", {})
        parts.append("Optimizer assumptions: " + json.dumps(cfg, sort_keys=True) + ".")
    issued_explanation = doc.get("explanation") if doc.get("source_decision_ref") else None
    if issued_explanation:
        parts.append("Issued decision: " + doc["source_decision_ref"]["decision_id"] + "; status: " + doc.get("decision_status", "not_available") + ".")
        parts.append("Frozen decision explanation:\n```json\n" + json.dumps({"explanation": issued_explanation, "resolution": doc.get("resolution")}, indent=2, sort_keys=True) + "\n```")
    explanation = None if issued_explanation else doc.get("explanation")
    if explanation:
        parts.append("Model objective: " + explanation.get("objective_definition", "See stored evidence") + ".")
        parts.append(f"Objective at unchanged holdings: {_number(explanation.get('hold_objective'))}; optimized objective: {_number(explanation.get('optimized_objective'))}; modeled improvement: {_number(explanation.get('objective_gain'))}. Units: {explanation.get('objective_units', 'see evidence')}.")
        forced = explanation.get("force_keep", [])
        for row in forced:
            loss = f"; modeled objective loss from keeping it: {_number(row.get('objective_loss_from_keep'))}" if row.get("status") == "optimal" else f"; {row.get('reason', 'counterfactual unavailable')}"
            parts.append(f"Keep {row['instrument_id']} at its current weight: {row['status']}{loss}.")
        shapley = explanation.get("shapley", {})
        if shapley:
            parts.append("Shapley attribution game: " + shapley.get("game", "see stored evidence") + ".")
            parts.extend(f"- {c['group']}: {_number(c['value'])} modeled objective contribution." for c in shapley.get("contributions", []))
            parts.append("Binding constraints: " + ", ".join(explanation.get("binding_constraints", [])) + ".")
            parts.append("Coalition/reconciliation evidence: " + json.dumps({k: shapley[k] for k in ("coalition_count", "reconciliation_residual", "tolerance", "hybrid_constraint_handling", "infeasible_hybrids") if k in shapley}, sort_keys=True))
    if kind == "comparison":
        # Implementation identity remains in the raw analysis for reproducibility;
        # the recommendation narrative shows the decision's comparison context.
        alignment = doc.get("alignment", {})
        comparison_context = {k: alignment[k] for k in ("algorithm", "previous_algorithm", "current_algorithm", "previous_as_of", "current_as_of", "horizon_sessions", "instruments", "snapshot_changed", "portfolio_revision_changed", "model_provenance_changed") if k in alignment}
        parts.append("Comparison alignment: " + json.dumps(comparison_context, sort_keys=True))
        for row in doc.get("changes", []):
            parts.append(f"{row['instrument_id']}: {row['previous_action']} → {row['current_action']}; target weight {_number(row['previous_target_weight'])} → {_number(row['current_target_weight'])}. Grouped modeled effects: " + json.dumps(row.get("attribution", []), sort_keys=True))
        parts.append("Exact grouped Shapley evidence: " + json.dumps(doc.get("shapley", {}), sort_keys=True))
        if doc.get("previous_decision_ref"):
            parts.append("Stored decision comparison evidence:\n```json\n" + json.dumps({k: doc[k] for k in ("previous_decision_ref", "current_decision_ref", "attribution", "policy_replay", "input_differences") if k in doc}, indent=2, sort_keys=True) + "\n```")
    if kind == "performance":
        parts.append(f"Daily/accounting outcome: {doc.get('trend', 'not_available')}; observed source: {doc.get('actual_source', 'not_available')}. {doc.get('trend_definition', doc.get('reason', ''))}")
        parts.append("Reconciled performance evidence:\n```json\n" + json.dumps({k: v for k, v in doc.items() if k in ("window", "planned_allocation_hold", "observed_paper", "gap", "forecast", "real_execution", "whys", "feedback", "limitations", "partial_horizon", "thresholds", "issued_plan_trend", "gap_trend", "source_decision_ref", "observed_snapshot_id", "observed_snapshot_checksum", "decision_status", "unchanged_holdings_benchmark", "paper_execution_evidence", "instrument_contributions")}, indent=2) + "\n```")
        horizon = doc.get("horizon_evaluation")
        if isinstance(horizon, dict):
            protocol = horizon.get("protocol") or {}
            objective = protocol.get("objective") or {}
            parts.append("Strategy evaluation: " + str(horizon.get("status", "not_available"))
                         + "; contract: " + str(horizon.get("protocol_status", "not_available"))
                         + "; objective: " + str(objective.get("kind", "not_available")) + ".")
            if horizon.get("protocol_status") == "retrospective_historical_request":
                parts.append("This recommendation was issued for a historical market date after its outcomes were already observable. Its replay is a historical counterfactual, not prospective validation.")
            if "available_forward_sessions" in horizon and "primary_horizon_sessions" in protocol:
                parts.append(f"Available forward sessions: {horizon['available_forward_sessions']}; declared primary horizon: {protocol['primary_horizon_sessions']}; primary horizon mature: {horizon.get('primary_horizon_mature', False)}.")
            assessment = horizon.get("evidence_assessment") or {}
            if assessment:
                parts.append("Evidence assessment: " + str(assessment.get("status", "not_available")) + ". " + str(assessment.get("recommendation", "")))
            parts.append("Strategy objective and horizon evaluation (separate from the daily outcome):\n```json\n" + json.dumps(horizon, indent=2, sort_keys=True) + "\n```")
            parts.append("A short-term loss alone does not establish policy failure. Frozen sequential-policy replay is a hypothetical experiment on observed prices, independent of accepted paper accounting. It evaluates subsequent decisions; holding the first allocation is a different counterfactual. A single observed path cannot establish optimality.")
        else:
            parts.append("Strategy-horizon evidence is unavailable in this analysis. Daily accounting and a frozen first-allocation hold do not establish the performance of the ongoing policy.")
    if kind in ("research", "research_run", "feedback", "market_events", "recursive_cycle", "recursive_iteration"):
        details = {k: v for k, v in doc.items() if k not in ("analysis_ref", "analysis_id", "created_at", "summary", "contract_version", "synthetic")}
        parts.append("Stored evidence:\n```json\n" + json.dumps(details, indent=2, sort_keys=True) + "\n```")
        if kind in ("recursive_cycle", "recursive_iteration"):
            parts.append("Research cycle state: " + doc.get("state", "not_available") + ". Strategy changes remain proposals for review; paper holdings are unchanged.")
    if doc.get("sources"):
        parts.append("Sources: " + "; ".join(f"[{s.get('title', s['url'])}]({s['url']})" for s in doc["sources"]))
    if doc.get("analysis_ref"):
        parts.append("Evidence checksum: " + doc["analysis_ref"]["checksum"] + ".")
    if kind in ("recommendation", "explanation", "comparison", "performance", "market_events"):
        parts.append("Attribution describes model counterfactuals. Dated market events provide context; they do not establish real-world causality. Missing execution and model evidence remain unavailable.")
    return "\n\n".join(parts), derived


def render_portfolio_results(state: dict) -> tuple[str, list[dict]]:
    parts, derived = [], []
    for row in state.get("tool_results") or []:
        name = row["tool"]
        if name not in CLASSICAL_TOOLS and name not in LIFECYCLE_TOOLS and name not in {"recommend_portfolio", "submit_experiment"}:
            continue
        if not row.get("ok"):
            if row.get("declined"):
                parts.append("The requested paper decision resolution was cancelled at confirmation. Saved holdings are unchanged."
                             if name == "resolve_portfolio_decision" else "The requested research operation was cancelled at confirmation. No experiment was launched.")
                continue
            error = row.get("error") or {}
            parts.append(f"{name} failed: {error.get('code', 'DEPENDENCY_UNAVAILABLE')}: {error.get('message', 'Evidence unavailable')}.")
            continue
        doc = row.get("result", {})
        if name == "submit_experiment":
            parts.append("Sandbox benchmark evidence:\n```json\n" + json.dumps(doc, indent=2, sort_keys=True) + "\n```")
            continue
        if doc.get("decision_id") and doc.get("recommendation"):
            parts.append("Issued decision: " + doc["decision_id"] + ". Review this decision before accepting it into the paper portfolio.")
        if name == "recommend_portfolio":
            parts.append(render_recommendation(doc["recommendation"]))
            value = doc["recommendation"].get("portfolio_state", {}).get("portfolio_value")
            derived.append({"computed_target_cash": None if value is None else value*doc["recommendation"]["cash_weight"]})
        elif doc.get("analysis_id"):
            text, figures = render_classical(doc)
            parts.append(text)
            derived.extend(figures)
        elif name in LIFECYCLE_TOOLS:
            parts.append(render_lifecycle(doc))
    if state.get("draft_text"):
        parts.append(state["draft_text"])
    if not parts:
        parts.append("No compatible stored numerical evidence was available for this request.")
    if (state.get("portfolio_workflow") or {}).get("mode") == "both":
        recs = [r["result"]["recommendation"] for r in state.get("tool_results", []) if r.get("ok") and r.get("result", {}).get("recommendation")]
        if len(recs) == 2 and any(recs[0].get(k) != recs[1].get(k) for k in ("as_of", "input_snapshot_id", "portfolio_state")):
            parts.insert(0, "Comparison limitation: the two strategies used different market snapshots/dates or portfolio states; their allocations are not a controlled comparison.")
        parts.insert(0, "PPO and traditional optimizer recommendations are shown separately with their own assumptions and provenance.")
    return "\n\n".join(parts), derived
