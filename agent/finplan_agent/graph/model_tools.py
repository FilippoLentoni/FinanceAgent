"""Per-model recommendation tools (contracts 1.14.0): model words, instrument universes and defaults.

Every ``generate_portfolio_recommendation_*`` request names its ``instruments``. When a planner (free
text, the structured ``recommendation`` payload, a ``tool_request`` without ``instruments``) does not
supply them, the universe defaults, in order, to

1. the instrument IDs of the request's explicit ``holdings.weights``;
2. the saved paper portfolio's instruments (positions of its latest holdings revision, read through
   ``get_portfolio_history`` before the model call);
3. otherwise a ``VALIDATION_FAILED`` error naming ``instruments`` (nothing is invented).
"""
from __future__ import annotations

import re
from typing import Any

from ..core.errors import AgentError
from ..providers.base import ToolCall
from ..tools.portfolio import MODEL_TOOLS, RECOMMENDATION_TOOLS

_SYMBOL = r"[A-Z][A-Z0-9]{1,5}(?:[.-][A-Z]{1,2})?"
_TICKER_LIST = re.compile(rf"(?<![A-Za-z0-9_$.-]){_SYMBOL}(?:\s*(?:,\s*(?:and\s+|&\s*)?|\band\b\s*|&\s*|/\s*){_SYMBOL})+(?![A-Za-z0-9_])")

__all__ = ["UNIVERSE_CALL_PREFIX", "requested_model", "requested_instruments", "resolve_instruments", "is_universe_lookup"]

#: ``get_portfolio_history`` calls made only to learn the saved book's universe use this id prefix.
UNIVERSE_CALL_PREFIX = "universe-"
_MODEL_WORDS = (
    ("min_variance", re.compile(r"\bmin(?:imum)?[ _-]?variance\b", re.I)),
    ("mean_variance", re.compile(r"\bmean[ _-]?variance\b", re.I)),
    ("cvar", re.compile(r"\bc[ _-]?var\b", re.I)),
    ("ppo", re.compile(r"\b(?:ppo|reinforcement[ -]learning)\b", re.I)),
)
#: Upper-case words that are never ticker symbols in a request.
_NOT_TICKERS = frozenset({
    "I", "A", "AN", "AND", "OR", "THE", "FOR", "MY", "ME", "OF", "TO", "IN", "ON", "AT", "IS", "IT", "BY", "AS", "BE", "WITH",
    "PPO", "CVAR", "RL", "SAC", "ETF", "ETFS", "USD", "EUR", "US", "USA", "NY", "NYSE", "UTC", "AI", "ML", "LLM", "API", "MCP",
    "OK", "ID", "IDS", "PNL", "NAV", "ROI", "CEO", "FAQ", "TODO", "NOTE", "MODEL", "PLEASE",
})
_TICKER = re.compile(r"(?<![A-Za-z0-9_$.-])[A-Z][A-Z0-9]{1,5}(?:[.-][A-Z]{1,2})?(?![A-Za-z0-9_])")


def requested_model(text: str) -> str | None:
    """``min_variance`` / ``mean_variance`` / ``cvar`` / ``ppo`` named in the text (first match wins), else None.

    ``mean_variance`` is recognised only so that it can be refused clearly: no tool serves it.
    """
    found = [(m.start(), model) for model, pattern in _MODEL_WORDS for m in [pattern.search(text)] if m]
    return min(found)[1] if found else None


def requested_models(text: str) -> list[str]:
    return [model for model, pattern in _MODEL_WORDS if pattern.search(text)]


def requested_instruments(text: str) -> list[str]:
    """The explicit instrument universe in the user's words: the longest LIST of two or more ticker
    symbols (``AAPL, AMZN and VOO``; separators ``,`` ``and`` ``&`` ``/``), in order, without duplicates.

    A single symbol or company names in a question ("Should I buy Google?") do not define a universe:
    the saved book's instruments apply instead. An all-upper-case message defines none.
    """
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return []
    best: list[str] = []
    for match in _TICKER_LIST.finditer(text):
        symbols = [m.group() for m in _TICKER.finditer(match.group()) if m.group() not in _NOT_TICKERS]
        unique = list(dict.fromkeys(symbols))
        if len(unique) >= 2 and len(unique) > len(best):
            best = unique
    return best[:100]


def is_universe_lookup(row: dict[str, Any]) -> bool:
    return row.get("tool") == "get_portfolio_history" and str(row.get("id", "")).startswith(UNIVERSE_CALL_PREFIX)


def _holdings_instruments(arguments: dict[str, Any]) -> list[str]:
    holdings = arguments.get("holdings")
    if not isinstance(holdings, dict) or not isinstance(holdings.get("weights"), list):
        return []
    out: list[str] = []
    for row in holdings["weights"]:
        if isinstance(row, dict) and isinstance(row.get("instrument_id"), str) and row["instrument_id"] not in out:
            out.append(row["instrument_id"])
    return out


def _saved_instruments(row: dict[str, Any]) -> list[str]:
    doc = row.get("result") or {}
    history = [h for h in doc.get("history") or [] if isinstance(h, dict)]
    if not history:
        return []
    latest = max(history, key=lambda h: h.get("revision") or 0)
    out: list[str] = []
    for position in (latest.get("paper_state") or {}).get("positions") or []:
        if isinstance(position, dict) and isinstance(position.get("instrument_id"), str) and position["instrument_id"] not in out:
            out.append(position["instrument_id"])
    return out


def resolve_instruments(calls: list[ToolCall], results: list[dict[str, Any]], turn: int) -> tuple[list[ToolCall], ToolCall | None, AgentError | None]:
    """(calls with ``instruments`` filled, universe lookup to run first or None, validation error or None).

    A lookup defers ALL ``calls`` (the caller keeps them and re-resolves after the lookup returned).
    """
    lookup = next((r for r in reversed(results) if is_universe_lookup(r)), None)
    out: list[ToolCall] = []
    for call in calls:
        if call.name not in RECOMMENDATION_TOOLS or call.arguments.get("instruments"):
            out.append(call)
            continue
        args = dict(call.arguments)
        instruments = _holdings_instruments(args)
        if not instruments:
            if lookup is None:
                portfolio = {"portfolio_id": args["portfolio_id"]} if args.get("portfolio_id") else {}
                return calls, ToolCall(id=f"{UNIVERSE_CALL_PREFIX}{turn}", name="get_portfolio_history", arguments={**portfolio, "limit": 1}), None
            instruments = _saved_instruments(lookup) if lookup.get("ok") else []
        if not instruments:
            return calls, None, AgentError.validation(
                "instruments are required: name the symbols to optimise over, supply holdings, or keep a saved paper portfolio with positions",
                pointer="/arguments/instruments", tool=call.name)
        out.append(ToolCall(id=call.id, name=call.name, arguments={**args, "instruments": instruments}))
    return out, None, None


def model_tool(model: str) -> str | None:
    return MODEL_TOOLS.get(model)
