"""Evidence-first orchestration for the independent traditional portfolio MCP."""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.news_evidence import render as render_news
from ..providers.base import ToolCall, text_of
from ..skills import CLASSICAL_SKILLS
from ..tools.portfolio import CLASSICAL_RECOMMENDATION_TOOLS, CLASSICAL_TOOLS, LIFECYCLE_TOOLS, MODEL_TOOLS, PPO_TOOL, RECOMMENDATION_TOOLS
from .lifecycle import render_lifecycle
from .recommendations import _SUPPLIED_STATE, INSTRUMENT_ALIASES, render_recommendation, saved_portfolio_reference

_CLASSICAL = re.compile(r"\b(?:traditional|classical|optimizer|optimisation|optimization|min(?:imum)?[ _-]?variance|mean[ _-]?variance|c[ _-]?var)\b", re.I)
_WHY = re.compile(r"\b(?:why|explain|reason|rationale)\b", re.I)
_COMPARE = re.compile(r"\b(?:compare|comparison|yesterday|previous|changed|changes|plan.over.plan)\b", re.I)
_PERFORMANCE = re.compile(r"\b(?:green|red|perform\w*|actuals?|realized|realised|discrepancy|trending|return gap)\b", re.I)
_RESEARCH = re.compile(r"\b(?:literature|research|retraining|retrain|improve|improvement|modeling approach|modelling approach)\b", re.I)
_PPO = re.compile(r"\b(?:ppo|reinforcement|rl)\b", re.I)
_ID = re.compile(r"\bca_[0-9a-f]{32}\b")
_LIST = re.compile(r"\b(?:list|show|display)\b[^.!?]{0,60}\b(?:analys[ie]s|stored|previous|past|issued|saved|history)\b"
                   r"|\b(?:stored|past|issued|saved)\b[^.!?]{0,40}\b(?:analys[ie]s|recommendations)\b", re.I)
_INSTRUMENTS = INSTRUMENT_ALIASES


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
            if result.get("name") in RECOMMENDATION_TOOLS:
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
        if workflow["mode"] == "publication_performance" and not any(r.get("tool") == "get_performance_evidence" for r in results):
            from .model_tools import latest_approved_snapshot
            snapshot = latest_approved_snapshot(next((r for r in results if r.get("tool") == "list_market_snapshots"), None))
            if snapshot is None:
                return {"workflow": workflow, "calls": [], "clarification": "No approved completed market snapshot is available to measure the publication's realized performance. Name a realized snapshot (snap_...) explicitly."}
            args = {**workflow["request"], "realized_snapshot_id": snapshot["input_snapshot_id"]}
            return {"workflow": workflow, "calls": [_call("get_performance_evidence", args, turn, 1)]}
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
    publication = _publication_performance(text, turn)
    if publication is not None:
        return publication
    history = _history(messages)
    family, successful = _latest_recommendation_family(messages)
    latest = _reference(history, "recommendation") if family in CLASSICAL_RECOMMENDATION_TOOLS and successful else None
    research = _reference(history, "research")
    ids = _ID.findall(text)
    classical = bool(_CLASSICAL.search(text))
    both = classical and bool(_PPO.search(text))
    current = latest["analysis_id"] if latest else None
    launch = bool(re.search(r"\b(?:run|launch|trigger|estimate)\b[^.!?]{0,80}\b(?:experiment|sandbox|research)\b", text, re.I))
    relevant = classical or bool(ids) or bool(family in CLASSICAL_RECOMMENDATION_TOOLS and not _PPO.search(text)) or bool(research and launch)
    if not relevant and not _RESEARCH.search(text):
        return None
    # Listing stored analyses is a read; it must never fall through to issuing a new recommendation.
    if _LIST.search(text) and not _WHY.search(text) and not re.search(r"\b(?:compare|comparison|changed|changes)\b", text, re.I):
        args: dict[str, Any] = {"limit": 10}
        kind = "recommendation" if re.search(r"\brecommend\w*\b", text, re.I) else "research" if _RESEARCH.search(text) else None
        if kind:
            args["kind"] = kind
        portfolio = saved_portfolio_reference(text)
        args.update(portfolio)
        return {"workflow": {"mode": "list"}, "calls": [_call("list_classical_analyses", args, turn)]}
    # Only an explicit instruction submits feedback (and the plan node asks for confirmation first).
    if re.search(r"\b(?:record|submit|note|log|save|send|add)\b[^.!?]{0,40}\bfeedback\b|^\s*feedback\s*:", text, re.I):
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
        from .model_tools import requested_instruments, requested_models
        models = requested_models(text)
        if "mean_variance" in models:
            return {"workflow": {"mode": "recommend"}, "calls": [], "clarification": "Mean-variance recommendations are no longer served. Ask for model min_variance, cvar or ppo; I have not substituted another objective."}
        algorithm = next((m for m in models if m in ("min_variance", "cvar")), "min_variance")
        portfolio = saved_portfolio_reference(text)
        instruments = requested_instruments(text)
        if instruments:
            portfolio = {**portfolio, "instruments": instruments}
        # The default universe (holdings, else the saved book) is resolved before the call by the plan node.
        calls = [_call(MODEL_TOOLS[algorithm], portfolio, turn)]
        if both:
            calls.insert(0, _call(PPO_TOOL, portfolio, turn, 1))
        return {"workflow": {"mode": "both" if both else "recommend"}, "calls": calls}
    if ids:
        return {"workflow": {"mode": "retrieve"}, "calls": [_call("get_classical_analysis", {"analysis_id": ids[0]}, turn)]}
    return None


_PUB = re.compile(r"\bpub_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_SNAP = re.compile(r"\bsnap_[0-7][0-9A-HJKMNP-TV-Z]{25}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _publication_performance(text: str, turn: int) -> dict[str, Any] | None:
    """A published plan's realized performance is explained from stored evidence: get_performance_evidence
    (skills/explain-performance), never from a traditional analysis of this session."""
    pub = _PUB.search(text)
    if not pub or not re.search(r"\b(?:perform\w*|realized|realised|actuals?|results?|gap|outcomes?|returns?)\b", text, re.I):
        return None
    dates = sorted(_DATE.findall(text))
    if len(dates) < 2:
        return {"workflow": {"mode": "publication_performance"}, "calls": [], "clarification": f"State the evaluation window (start and end dates, YYYY-MM-DD) for publication {pub.group()}; I will then read its stored performance evidence."}
    request = {"publication_id": pub.group(), "window": {"start": dates[0], "end": dates[-1]}, "actual_source": "simulated" if re.search(r"\bsimulat\w*\b", text, re.I) else "paper"}
    snap = _SNAP.search(text)
    if snap:
        return {"workflow": {"mode": "publication_performance", "request": request}, "calls": [_call("get_performance_evidence", {**request, "realized_snapshot_id": snap.group()}, turn)]}
    return {"workflow": {"mode": "publication_performance", "request": request}, "calls": [ToolCall(id=f"snapshot-{turn}", name="list_market_snapshots", arguments={"limit": 3})]}


def render_performance_evidence(doc: dict) -> str:
    ev = doc.get("evidence") or {}
    ids = ev.get("identifiers") or {}
    rec = ev.get("reconciliation") or {}
    gap = ev.get("gap") or {}
    parts = [f"Stored performance evidence for publication {ids.get('publication_id', 'not_available')} (plan version {ids.get('plan_version_id', 'not_available')}), "
             f"{ev.get('actual_source', 'not_available')} records, window {(ev.get('window') or {}).get('start')} to {(ev.get('window') or {}).get('end')}, realized snapshot {ids.get('realized_snapshot_id', 'not_available')}."]
    for path, label in (("plan_path", "Published-allocation hold"), ("executed_path", "Recorded account")):
        terms = rec.get(path) or {}
        if terms:
            parts.append(f"{label}: start {_number(terms.get('start_value'), 2)}; end {_number(terms.get('end_value'), 2)}; fees {_number(terms.get('fees'), 2)}.")
    comps = gap.get("components") or {}
    if "total" in gap:
        parts.append(f"Total gap {_number(gap.get('total'), 2)}: execution/exposure {_number(comps.get('execution'), 2)}; cost {_number(comps.get('cost'), 2)}; market versus forecast {_number(comps.get('market_vs_forecast'), 2)}; residual {_number(comps.get('residual'))}.")
    for why in ev.get("whys") or []:
        parts.append(f"Why (level {why.get('level')}): {why.get('question')} {why.get('answer')}.")
    for action in ev.get("feedback") or []:
        parts.append(f"Feedback: {action.get('action')}: {action.get('reason')}.")
    if ev.get("limitations"):
        parts.append("Limitations: " + ", ".join(ev["limitations"]) + ".")
    ref = doc.get("evidence_ref") or {}
    if ref.get("checksum"):
        parts.append("Evidence checksum: " + ref["checksum"] + ".")
    return "\n\n".join(parts)


def selected_portfolio_skills(calls: list[ToolCall], inventory: tuple) -> list[dict]:
    from ..skills import PPO_SKILL
    names = {CLASSICAL_SKILLS.get(c.name, PPO_SKILL if c.name == PPO_TOOL else "explain-performance" if c.name == "get_performance_evidence" else "") for c in calls}
    return [dict(skill) for skill in inventory if skill["name"] in names]


def _number(value: Any, digits: int = 6) -> str:
    return "not_available" if value is None else f"{float(value):,.{digits}f}"


#: Optimizer settings each served classical model uses (others in a producer echo are not shown).
_MODEL_SETTINGS = {
    "min_variance": ("lookback_days", "max_weight", "cash_weight", "horizon_sessions", "turnover_penalty"),
    "cvar": ("lookback_days", "cvar_alpha", "max_weight", "cash_weight", "horizon_sessions", "turnover_penalty"),
}
_RISK_AVERSION_UNITS = re.compile(r";?\s*risk aversion supplies their trade-off", re.I)


def _units(text: Any, strategy: Any) -> str:
    text = str(text or "see evidence")
    return text if strategy == "mean_variance" else _RISK_AVERSION_UNITS.sub("", text).strip() or "see evidence"


def _risk_block(rec: dict | None, explanation: dict | None) -> list[str]:
    """Stored model risk/return estimates and per-instrument risk contributions (verbatim values)."""
    out: list[str] = []
    rec = rec or {}
    diag = rec.get("diagnostics") or {}
    metrics = [(label, diag.get(key)) for key, label in (("variance", "portfolio variance"), ("cvar_loss", "CVaR loss"), ("estimated_return", "estimated return"), ("turnover", "turnover"))
               if isinstance(diag.get(key), (int, float)) and not isinstance(diag.get(key), bool)]
    if metrics:
        alpha = (rec.get("settings") or diag.get("settings") or {}).get("cvar_alpha") if rec.get("strategy") == "cvar" else None
        out.append("Model risk and return estimates from the stored solve (decimal, over the model horizon): " + "; ".join(f"{label} {_number(v)}" for label, v in metrics)
                   + (f"; CVaR confidence level {_number(alpha, 2)}" if alpha is not None else "") + ".")
    contributions = [r for r in (explanation or {}).get("risk_contributions") or [] if isinstance(r, dict) and isinstance(r.get("variance_contribution"), (int, float))]
    if contributions:
        rows = ["| Instrument | Variance contribution |", "|---|---:|"]
        rows += [f"| {r['instrument_id']} | {_number(r['variance_contribution'])} |" for r in sorted(contributions, key=lambda r: -float(r["variance_contribution"]))]
        out.append("Per-instrument contribution to modeled portfolio variance (largest first):\n" + "\n".join(rows))
    binding = (explanation or {}).get("binding_constraints")
    if binding:
        out.append("Binding constraints: " + ", ".join(binding) + ".")
    return out


def render_classical(doc: dict, focus: str | None = None) -> tuple[str, list[dict]]:
    """Return the presentation and declared derived arithmetic for figure checking.

    ``focus`` (``risk`` / ``drivers``) puts the stored evidence for that question first."""
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
        if rec.get("strategy") in _MODEL_SETTINGS:
            cfg = {k: v for k, v in cfg.items() if k in _MODEL_SETTINGS[rec["strategy"]]}
        parts.append("Optimizer assumptions: " + json.dumps(cfg, sort_keys=True) + ".")
        news=(rec.get('diagnostics') or {}).get('news_evidence') or {}
        parts.append(render_news(news))
        if rec.get('news_timing') == 'eligible_news_at_decision_with_completed_prices':
            parts.append('Current eligible news is evaluated at its declared decision cutoff; reference prices remain the latest completed session rather than live intraday prices.')
    issued_explanation = doc.get("explanation") if doc.get("source_decision_ref") else None
    if issued_explanation:
        parts.append("Issued decision: " + doc["source_decision_ref"]["decision_id"] + "; status: " + doc.get("decision_status", "not_available") + ".")
        for trade in issued_explanation.get("trade_logic", []):
            parts.append(f"{trade['instrument_id']}: {trade['action']}; {trade.get('reason', 'See frozen trade evidence')}.")
        replay = issued_explanation.get("policy_replay") or {}
        if replay:
            parts.append("Frozen policy replay: " + replay.get("status", "not_available") + ". " + (issued_explanation.get("attribution") or {}).get("reason", ""))
        optimizer = issued_explanation.get("optimizer") or {}
        if optimizer:
            parts.append("Objective: " + optimizer.get("objective_definition", "See stored evidence") + ".")
            parts.append(f"Unchanged-holdings objective: {_number(optimizer.get('hold_objective'))}; optimized: {_number(optimizer.get('optimized_objective'))}; modeled improvement: {_number(optimizer.get('objective_gain'))}. Units: {optimizer.get('objective_units', 'see evidence')}.")
            shapley = optimizer.get("shapley") or {}
            parts.extend(f"{c['group']}: {_number(c['value'])} modeled objective contribution." for c in shapley.get("contributions", []))
            parts.append("Binding constraints: " + ", ".join(optimizer.get("binding_constraints", [])) + ".")
        forecast = issued_explanation.get("forecast") or {}
        parts.append("Forecast: " + forecast.get("status", "not_available") + ". " + forecast.get("reason", ""))
    explanation = None if issued_explanation else doc.get("explanation")
    if rec and not issued_explanation:
        risk = _risk_block(rec, explanation)
        drivers: list[str] = []
        if explanation:
            drivers.append("Model objective: " + explanation.get("objective_definition", "See stored evidence") + ".")
            drivers.append(f"Objective at unchanged holdings: {_number(explanation.get('hold_objective'))}; optimized objective: {_number(explanation.get('optimized_objective'))}; modeled improvement: {_number(explanation.get('objective_gain'))}. Units: {_units(explanation.get('objective_units'), rec.get('strategy'))}.")
            shapley = explanation.get("shapley", {})
            if shapley:
                drivers.append("Main drivers - Shapley attribution of the modeled objective improvement (" + shapley.get("game", "see stored evidence") + "), largest first:")
                drivers.extend(f"- {c['group']}: {_number(c['value'])} modeled objective contribution." for c in sorted(shapley.get("contributions", []), key=lambda c: -abs(float(c.get("value") or 0))))
                drivers.append("Coalition/reconciliation evidence: " + json.dumps({k: shapley[k] for k in ("coalition_count", "reconciliation_residual", "tolerance", "hybrid_constraint_handling", "infeasible_hybrids") if k in shapley}, sort_keys=True))
            for row in explanation.get("force_keep", []):
                loss = f"; modeled objective loss from keeping it: {_number(row.get('objective_loss_from_keep'))}" if row.get("status") == "optimal" else f"; {row.get('reason', 'counterfactual unavailable')}"
                drivers.append(f"Keep {row['instrument_id']} at its current weight: {row['status']}{loss}.")
        if focus == "risk":
            parts[2:2] = ["Risk of this recommendation, from the stored analysis:"] + risk
            parts.extend(drivers)
        elif focus == "drivers":
            parts[2:2] = ["Main drivers of this recommendation, from the stored analysis:"] + drivers
            parts.extend(risk)
        else:
            parts.extend(drivers + risk)
    elif explanation:
        parts.append("Model objective: " + explanation.get("objective_definition", "See stored evidence") + ".")
        parts.append(f"Objective at unchanged holdings: {_number(explanation.get('hold_objective'))}; optimized objective: {_number(explanation.get('optimized_objective'))}; modeled improvement: {_number(explanation.get('objective_gain'))}. Units: {explanation.get('objective_units', 'see evidence')}.")
    if kind == "comparison":
        # Implementation identity remains in the raw analysis for reproducibility;
        # the recommendation narrative shows the decision's comparison context.
        alignment = doc.get("alignment", {})
        comparison_context = {k: alignment[k] for k in ("algorithm", "previous_algorithm", "current_algorithm", "previous_as_of", "current_as_of", "horizon_sessions", "instruments", "snapshot_changed", "portfolio_revision_changed", "model_provenance_changed", "previous_decision_at", "current_decision_at", "previous_news_protocol", "current_news_protocol", "previous_news_source_checksum", "current_news_source_checksum", "previous_news_window", "current_news_window", "news_state_changed", "news_protocol_changed", "formation_changed", "holdings_state_changed", "same_price_as_of", "same_universe", "temporal_news_change", "identical_state_replay") if k in alignment}
        parts.append("Comparison alignment: " + json.dumps(comparison_context, sort_keys=True))
        if alignment.get('identical_state_replay'):
            parts.append('These decisions replay the same frozen state; they are not two daily news formations.')
        elif alignment.get('same_price_as_of') and alignment.get('temporal_news_change'):
            parts.append('Completed prices are unchanged; the retained eligible news formations differ. Model and holdings alignment remain explicit above; this is not causal market attribution.')
        for row in doc.get("changes", []):
            parts.append(f"{row['instrument_id']}: {row['previous_action']} → {row['current_action']}; target weight {_number(row['previous_target_weight'])} → {_number(row['current_target_weight'])}. Grouped modeled effects: " + json.dumps(row.get("attribution", []), sort_keys=True))
        shapley = doc.get("shapley") or {}
        if shapley:
            parts.append("Grouped attribution method: " + shapley.get("method", "not_available") + "; full reconciliation retained in the structured analysis.")
        attribution = doc.get("attribution") or {}
        if attribution:
            parts.append("Change attribution: " + str(attribution.get("status", "not_available")) + ". " + str(attribution.get("reason", "")))
    if kind == "performance":
        parts.append(f"Daily/accounting outcome: {doc.get('trend', 'not_available')}; observed source: {doc.get('actual_source', 'not_available')}. {doc.get('trend_definition', doc.get('reason', ''))}")
        window = doc.get("window") or {}
        parts.append("Observed window: " + str(window.get("start", "not_available")) + " to " + str(window.get("end", "not_available")) + ".")
        planned = doc.get("planned_allocation_hold") or {}
        if planned:
            parts.append(f"Hypothetical allocation hold: starting value {_number(planned.get('start_value'),2)}; ending value {_number(planned.get('end_value'),2)}; PnL {_number(planned.get('pnl'),2)}. This is not a forecast or an executed portfolio.")
        observed = doc.get("observed_paper") or {}
        parts.append("Observed paper accounting: " + str(observed.get("status", "not_available")) + ". " + str(observed.get("reason", "")))
        if observed.get("status") == "available" or "pnl" in observed:
            for key in ("start_value", "end_value", "pnl", "return", "transaction_cost"):
                if key in observed:
                    parts.append("Observed " + key.replace("_", " ") + ": " + _number(observed[key]) + ".")
        forecast = doc.get("forecast") or {}
        parts.append("Forecast uncertainty: " + str(forecast.get("status", "not_available")) + ". " + str(forecast.get("reason", "")))
        gap = doc.get("gap") or {}
        if gap:
            parts.append("Implementation gap evidence: " + json.dumps(gap, sort_keys=True) + ".")
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
            parts.append("Strategy objective and horizon evaluation: full replay windows, controls and limitations remain in the structured analysis. " + str(horizon.get("reason", "")))
            parts.append("A short-term loss alone does not establish policy failure. Frozen sequential-policy replay is a hypothetical experiment on observed prices, independent of accepted paper accounting. It evaluates subsequent decisions; holding the first allocation is a different counterfactual. A single observed path cannot establish optimality.")
        else:
            parts.append("Strategy-horizon evidence is unavailable in this analysis. Daily accounting and a frozen first-allocation hold do not establish the performance of the ongoing policy.")
    if kind == "market_events":
        parts.append("Market context: " + str(doc.get("status", doc.get("source_status", "not_available"))) + ". " + str(doc.get("reason", "")))
        parts.append("News metadata provides dated context, not causal attribution. Full query and source records remain in the structured analysis.")
        if doc.get("news_training_evidence"):
            parts.append("Collected news is available for research only from its actual collection time. Historical training coverage is unavailable; the headline extractor is an uncalibrated research hypothesis.")
    if kind in ("research", "research_run", "feedback", "recursive_cycle", "recursive_iteration"):
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
    from .model_tools import SCENARIO_NOTE, SCENARIO_PORTFOLIO_VALUE, is_scenario
    from .recommendations import explanation_focus
    parts, derived = [], []
    user = next((m for m in reversed(state.get("messages", [])) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    focus = explanation_focus(text_of(user))
    for row in state.get("tool_results") or []:
        name = row["tool"]
        if name not in CLASSICAL_TOOLS and name not in LIFECYCLE_TOOLS and name not in {PPO_TOOL, "submit_experiment", "get_performance_evidence"}:
            continue
        if str(row.get("id", "")).startswith(("universe-", "snapshot-")) and row.get("ok"):
            continue  # the saved book's universe lookup only feeds the model call's instruments
        if not row.get("ok"):
            if row.get("declined"):
                parts.append("The requested paper decision resolution was cancelled at confirmation. Saved holdings are unchanged."
                             if name == "resolve_portfolio_decision" else "The feedback was not submitted: it was cancelled at confirmation. Nothing was recorded."
                             if name == "submit_portfolio_feedback" else "The requested research operation was cancelled at confirmation. No experiment was launched.")
                continue
            error = row.get("error") or {}
            parts.append(f"{name} failed: {error.get('code', 'DEPENDENCY_UNAVAILABLE')}: {error.get('message', 'Evidence unavailable')}.")
            continue
        doc = row.get("result", {})
        if name == "get_performance_evidence":
            parts.append(render_performance_evidence(doc))
            continue
        if name == "submit_experiment":
            parts.append("Sandbox benchmark evidence:\n```json\n" + json.dumps(doc, indent=2, sort_keys=True) + "\n```")
            continue
        if is_scenario(row) and isinstance(doc.get("recommendation"), dict):
            rec = doc["recommendation"]
            parts.append(SCENARIO_NOTE.format(snapshot=rec.get("input_snapshot_id", "not_available"), as_of=rec.get("as_of", "not_available")))
            derived.append({"scenario_portfolio_value": SCENARIO_PORTFOLIO_VALUE})
        if doc.get("decision_id") and doc.get("recommendation"):
            parts.append("Issued decision: " + doc["decision_id"] + ". Review this decision before accepting it into the paper portfolio.")
        if name == PPO_TOOL:
            parts.append(render_recommendation(doc["recommendation"]))
            value = doc["recommendation"].get("portfolio_state", {}).get("portfolio_value")
            derived.append({"computed_target_cash": None if value is None else value*doc["recommendation"]["cash_weight"]})
        elif name == "list_classical_analyses" and isinstance(doc.get("analyses"), list) and (state.get("portfolio_workflow") or {}).get("mode") in ("list", "direct"):
            rows = [{k: r[k] for k in ("analysis_id", "analysis_kind", "created_at", "portfolio_id") if k in r} | ({"strategy": (r.get("recommendation") or {}).get("strategy")} if (r.get("recommendation") or {}).get("strategy") else {}) for r in doc["analyses"] if isinstance(r, dict)]
            parts.append(f"Stored traditional analyses: {len(rows)} in this page.\n```json\n" + json.dumps(rows, indent=2, sort_keys=True) + "\n```" + ("\n\nMore analyses are available with the continuation token in the structured response." if doc.get("next_token") else ""))
            derived.append({"returned_record_count": len(rows)})
        elif doc.get("analysis_id"):
            text, figures = render_classical(doc, focus)
            parts.append(text)
            derived.extend(figures)
        elif name in LIFECYCLE_TOOLS:
            parts.append(render_lifecycle(doc))
            for key in ("history", "decisions", "snapshots"):
                if isinstance(doc.get(key), list):
                    derived.append({"returned_record_count": len(doc[key])})
    workflow = state.get("portfolio_workflow") or {}
    if workflow.get("mode") == "lifecycle_sensitivity":
        parts.insert(0, "Controlled hypothetical sensitivity using the original snapshot, date and frozen holdings. Each alternative changes only its named setting; no holdings update or paid experiment is requested.")
        parts.append("Original-policy eligibility is not established by these optimizer alternatives: a complete investor risk policy is not recorded. Tighter weight bounds can be checked against the original cap; changed objective parameters require separate review.")
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
