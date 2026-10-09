"""Saved-book routing and complete, evidence-only strategy recommendation presentation."""

from __future__ import annotations

import re
from typing import Any

from ..providers.base import text_of

_RECOMMENDATION = re.compile(
    r"\b(?:how|where|should|what)\b[^.!?]{0,100}\binvest\w*\b"
    r"|\bportfolio\b[^.!?]{0,70}\b(?:plan\w*|recommend\w*|allocat\w*|rebalanc\w*)\b"
    r"|\b(?:recommend\w*|rebalanc\w*|allocat\w*)\b[^.!?]{0,70}\b(?:portfolio|invest\w*|today|stocks?|shares?)\b"
    r"|\bpolicy\b[^.!?]{0,70}\b(?:recommend\w*|today|buy|sell)\b"
    r"|\b(?:buy|sell)\b[^.!?]{0,70}\b(?:google|googl?|nvidia|nvda|apple|aapl|netflix|nflx|voo)\b",
    re.I,
)
_SUPPLIED_STATE = re.compile(
    r"\bI\s+(?:currently\s+)?(?:hold|own|have)\b"
    r"|\bmy\s+(?:actual|real)\s+(?:holdings|portfolio)\b"
    r"|\b(?:portfolio value|cash balance|high.watermark)\s*(?:is|:|=)"
    r"|\$\s*\d|\b\d[\d,.]*\s*%|\b\d[\d,.]*\s*(?:shares?|stocks?|dollars?|USD)\b",
    re.I,
)


def recommendation_arguments(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return exact structured arguments, or {} for ordinary saved-paper policy questions.

    An explicit actual state stays with the provider, which can collect missing inputs. This
    deliberately never parses natural-language quantities into a silently fabricated portfolio.
    """
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    for block in user.get("content", []):
        request = block.get("tool_request")
        if isinstance(request, dict):
            return dict(request.get("arguments") or {}) if request.get("name") == "recommend_portfolio" else None
    text = text_of(user)
    return {} if _RECOMMENDATION.search(text) and not _SUPPLIED_STATE.search(text) else None


def supplied_state_recommendation(messages: list[dict[str, Any]]) -> bool:
    user = next((m for m in reversed(messages) if m.get("role") == "user" and any("text" in b for b in m.get("content", []))), {})
    if any(isinstance(b.get("tool_request"), dict) for b in user.get("content", [])):
        return False
    text = text_of(user)
    return bool(_RECOMMENDATION.search(text) and _SUPPLIED_STATE.search(text))


def _figure(value: Any, decimals: int = 4) -> str:
    return "—" if value is None else f"{float(value):,.{decimals}f}"


def target_cash_value(rec: dict[str, Any]) -> float | None:
    """Deterministic cash arithmetic on producer values, shared with the figure verifier."""
    state = rec.get("portfolio_state") or {}
    return None if state.get("portfolio_value") is None else float(state["portfolio_value"]) * float(rec["cash_weight"])


def render_recommendation(rec: dict[str, Any]) -> str:
    """Render every producer target and cash without allowing an LLM to drop or invent evidence."""
    state = rec.get("portfolio_state") or {}
    saved = state.get("source") == "saved_paper"
    source = "Saved paper portfolio" if saved else "Supplied portfolio state"
    parts = [f"{source}; advisory recommendation from the selected **{rec['strategy']}** strategy."]
    if state.get("portfolio_id"):
        parts.append(f"Portfolio: {state['portfolio_id']}; revision {state.get('revision', 'not supplied')}.")
    if state.get("portfolio_value") is not None:
        parts.append(f"Marked portfolio value: {_figure(state['portfolio_value'], 2)} {state.get('base_currency', 'USD')}; high watermark: {_figure(state['high_watermark'], 2)}.")
    parts.append(f"Market session: {rec.get('as_of', 'not supplied')}. Decisions use the completed close for the next session.")
    decisions = {d["instrument_id"]: d for d in rec["decisions"]}
    rows = ["| Instrument | Action | Current shares | Target shares | Change in shares | Change in value | Target weight |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for target in rec["target_weights"]:
        instrument = target["instrument_id"]
        decision = decisions[instrument]
        rows.append(f"| {instrument} | {decision['action']} | {_figure(decision.get('current_quantity'))} | {_figure(decision.get('target_quantity'))} | {_figure(decision.get('delta_quantity'))} | {_figure(decision['indicative_notional'], 2)} | {float(target['weight']) * 100:.2f}% |")
    parts.append("\n".join(rows))
    current_cash = f"current {_figure(state['current_cash'], 2)} {state.get('base_currency', 'USD')}; " if state.get('current_cash') is not None else ""
    cash_value = target_cash_value(rec)
    target_amount = f"{_figure(cash_value, 2)} {state.get('base_currency', 'USD')} / " if cash_value is not None else ""
    parts.append(f"**Cash: {current_cash}target {target_amount}{float(rec['cash_weight']) * 100:.2f}% of portfolio value.**")
    prices = [f"{d['instrument_id']} {_figure(d['reference_price'], 4)} ({d.get('price_as_of', rec.get('as_of', 'not supplied'))})" for d in rec['decisions'] if d.get('reference_price') is not None]
    if prices:
        parts.append("Reference prices: " + "; ".join(prices) + ". Share changes are indicative fractional quantities at those prices; rounding, fees and execution prices can change the final quantities.")
    policy_inputs = "The neural policy conditions its allocation on completed market features, current portfolio weights and drawdown. " if rec['strategy'] in ('ppo', 'sac') else "The strategy evaluates the completed market data and supplied portfolio state. "
    parts.append("Why: " + policy_inputs + "Each proposed buy or sell moves the current holding toward its target allocation after configured constraints. "
                 f"Reported solution status: {rec.get('solution_status', 'not supplied')}; constraint handling: {(rec.get('constraint_outcome') or {}).get('action', 'not supplied')}. "
                 "The policy output does not establish which news item or individual feature caused the action.")
    parts.append(f"Policy experiment: {rec['policy_source_run_id']}; configuration: {rec['configuration_id']}; aggregation: {rec.get('aggregation', 'not supplied')}.")
    provenance = [f"{name}: {rec[name]}" for name in ('input_snapshot_id', 'snapshot_checksum', 'policy_artifact_checksum', 'export_run_id') if rec.get(name)]
    if provenance:
        parts.append("Provenance: " + "; ".join(provenance) + ".")
    parts.append("These are proposed trades, not executions. Asking again revalues the saved holdings; previous recommendations do not change them." if saved else "These are proposed trades, not executions. The supplied state has not been saved as holdings.")
    if (rec.get('forecast') or {}).get('status') == 'not_available':
        parts.append("No calibrated return forecast is available.")
    if rec.get('limitations'):
        parts.append("Policy limitations: " + ", ".join(rec['limitations']) + ".")
    return "\n\n".join(parts)
