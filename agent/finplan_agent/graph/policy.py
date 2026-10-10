"""Deterministic in-graph refusals (spec agent-runtime-hosting "Confirmation before state-changing tool
calls" paid-job scenario, "No live financial actions"; FA-RT-05, FA-RT-06).

These run in the ``route`` node before any planning, model call or tool call, and again on every
planned tool call (defense in depth). They complement, never replace, the Gateway policy, which
denies the same operations for every channel.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["Refusal", "classify_request", "tool_call_refusal", "SYSTEM_PROMPT"]

SYSTEM_PROMPT = (
    "You are the FinanceAgent research assistant for paper and simulated portfolio planning only. "
    "Every number you state must come from a tool result in this conversation; never compute, estimate "
    "or recall financial figures yourself. Re-read plans, versions, publications and results through "
    "tools by their identifiers instead of relying on earlier messages. There is no live trading, "
    "brokerage, payment or wallet capability. You never approve paid jobs, raise budgets or change a "
    "user's stored risk preferences or constraints; present trade-offs instead. Results of re-solves, "
    "sweeps and attributions are modeled effects under the stated model, not real-world causes."
    " For ordinary portfolio planning, investment or buy/sell policy recommendations, call "
    "recommend_portfolio with {} to load the saved paper portfolio and latest approved market data. "
    "Do not ask for holdings unless the user explicitly supplies an actual portfolio or the tool "
    "reports missing saved state. Never treat prior recommendations as executed positions. "
    "For incomplete explicitly supplied actual holdings, ask for missing state; do not substitute "
    "the paper book. Include all returned instruments and cash, and do not invent causal explanations. "
    "Issued recommendations return decision_id. Explain, compare and evaluate those durable decisions through "
    "explain_portfolio_decision, compare_portfolio_decisions and evaluate_portfolio_decision. For acceptance or "
    "rejection, first read get_portfolio_decision, present the exact stored plan/revision for human confirmation, "
    "then call resolve_portfolio_decision. Only accepted simulated fills update paper holdings. "
    "Use get_portfolio_history, list_portfolio_decisions and list_market_snapshots for prior-day postmortems."
)

#: Requests are refused only when phrased as an INSTRUCTION (sentence start or "please", "can you",
#: "go ahead and", ...). Research questions and hypotheticals ("why did the plan sell 10% of SPY?",
#: "what if I lowered my risk tolerance?") are legitimate explanation requests and pass.
_DO = r"(?:^|[.!?;:]\s+|\b(?:please|pls|kindly|can you|could you|would you|will you|go ahead and|i want you to|i need you to|now)\s+)"
_LIVE = re.compile(
    _DO + r"(?:place|submit|send|execute|make|open)\b[^.?!]{0,40}\b(?:live|real|market|limit)?\s*(?:order|trade)s?\b(?!-)"
    rf"|{_DO}(?:buy|sell|short)\b[^.?!]{{0,30}}\b(?:shares?|units?|stocks?|etfs?|\$?\d)"
    r"|\b(?:live|real[- ]money)\s+(?:trad\w*|order\w*|execution)\b"
    r"|\b(?:brokerage|broker account|coinbase|exchange account|wallet|crypto payment)\b",
    re.I,
)
_PAID = re.compile(
    _DO + r"(?:approve|authori[sz]e|sign off)\b[^.?!]{0,40}\b(?:paid|gpu|job|run|experiment|spend|cost|budget)\b"
    rf"|{_DO}(?:raise|increase|lift|extend|bump)\b[^.?!]{{0,20}}\b(?:budget|allocation|spend(?:ing)? limit|cost ceiling)\b",
    re.I,
)
_RISK = re.compile(
    _DO + r"(?:change|update|modify|rewrite|relax|loosen|lower|raise|increase|decrease|edit|set|overwrite)\b[^.?!]{0,40}"
    r"\b(?:my|the|stored|saved)?\s*(?:risk (?:preference|tolerance|profile|limit)s?|constraints?|turnover limit|max(?:imum)? drawdown)\b",
    re.I,
)


class Refusal(dict):
    """``{"code", "message", "kind"}``; the code is a registered contract error code."""


def classify_request(text: str) -> Refusal | None:
    if _LIVE.search(text):
        return Refusal(code="OPERATION_NOT_PERMITTED", kind="live_financial_action", message="Live trading, brokerage, exchange, payment and wallet actions are not available; only paper or simulated execution exists.")
    if _PAID.search(text):
        return Refusal(
            code="OPERATION_NOT_PERMITTED", kind="paid_job_approval", message="The agent cannot approve paid FinanceModel jobs or raise budgets; use the human approval path (FinanceModel approval CLI and the platform budget owner)."
        )
    if _RISK.search(text):
        return Refusal(code="OPERATION_NOT_PERMITTED", kind="risk_preference_change", message="The agent does not change stored risk preferences or constraints. It can present the trade-offs of alternatives so that you decide.")
    return None


_LIVE_VALUES = {"live", "real", "production_trading"}


def _walk(value: Any) -> Any:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def tool_call_refusal(tool: str, arguments: dict[str, Any], *, denied: bool) -> Refusal | None:
    if denied:
        return Refusal(code="OPERATION_NOT_PERMITTED", kind="denied_tool", message=f"The tool {tool} is not permitted (no live, payment or wallet capability).")
    for k, v in _walk(arguments):
        if k in ("mode", "execution_mode", "venue") and isinstance(v, str) and v.lower() in _LIVE_VALUES:
            return Refusal(code="OPERATION_NOT_PERMITTED", kind="live_mode_argument", message="Execution mode live is not permitted; only paper or simulated modes exist.")
    return None
