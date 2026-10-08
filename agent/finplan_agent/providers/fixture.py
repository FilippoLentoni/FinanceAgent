"""The deterministic fixture provider (design D4; task 3.2). No network, no model, no cost.

Used by the build stage, beta and every offline test, and wherever ``explanation-provider`` is
``fixture``. The same inputs always produce the same output.

* ``plan``: a rule-based planner. An explicit structured tool request in the latest user message
  (``{"tool_request": {"name", "arguments"}}``) wins; otherwise contract identifiers in the prompt
  select read tools (``pv_`` -> ``get_plan_version``, ``pl_`` -> ``get_plan`` / ``list_plan_versions``,
  ``run_`` -> ``get_job_status`` / ``get_experiment_result``), and capability questions select
  ``describe_capabilities``. Only offered tools are called; after tool results arrive in the current
  turn it plans nothing more.
* ``narrate``: a template narrative quoting the compact evidence values verbatim, so the
  deterministic claim check always passes on fixture output.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .base import GenerateRequest, GenerateResult, ToolCall, Usage, estimate_tokens, text_of

__all__ = ["FixtureProvider", "fixture_narrative"]

_ID = r"[0-7][0-9A-HJKMNP-TV-Z]{25}"
_PV = re.compile(rf"\b(pv_{_ID})\b")
_PL = re.compile(rf"\b(pl_{_ID})\b")
_RUN = re.compile(rf"\b(run_{_ID})\b")
_CAPS = re.compile(r"\b(capabilit\w*|what can you|which tools|available tools|describe)\b", re.I)
_VERSIONS = re.compile(r"\b(versions|history|list)\b", re.I)
_RESULT = re.compile(r"\b(result|results|outcome|report)\b", re.I)


def _turn_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages of the current turn: from the last user TEXT message on."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "user" and any("text" in b or "tool_request" in b for b in m.get("content", [])):
            return messages[i:]
    return messages


def _plan(request: GenerateRequest) -> list[ToolCall]:
    turn = _turn_messages(request.messages)
    if any("tool_result" in b for m in turn for b in m.get("content", [])):
        return []
    offered = {t.name for t in request.tools}
    user = turn[0] if turn else {}
    calls: list[tuple[str, dict[str, Any]]] = []
    for b in user.get("content", []):
        tr = b.get("tool_request") if isinstance(b, dict) else None
        if isinstance(tr, dict) and isinstance(tr.get("name"), str):
            calls.append((tr["name"], dict(tr.get("arguments") or {})))
    if not calls:
        text = text_of(user)
        for pv in _PV.findall(text):
            calls.append(("get_plan_version", {"plan_version_id": pv}))
        for pl in _PL.findall(text):
            calls.append(("list_plan_versions" if _VERSIONS.search(text) else "get_plan", {"plan_id": pl}))
        for run in _RUN.findall(text):
            calls.append(("get_experiment_result" if _RESULT.search(text) else "get_job_status", {"run_id": run}))
        if not calls and _CAPS.search(text):
            calls.append(("describe_capabilities", {}))
    out: list[ToolCall] = []
    for i, (name, args) in enumerate(calls):
        if name in offered:
            out.append(ToolCall(id=f"fx-{len(turn)}-{i}", name=name, arguments=args))
    return out


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def fixture_narrative(evidence: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """Deterministic narrative quoting each evidence item's scalar summary values verbatim."""
    if not evidence:
        return "No tool results were needed or available for this request, so there are no figures to report."
    parts = []
    for item in evidence:
        tool = item.get("tool", "tool")
        if item.get("error"):
            parts.append(f"{tool} returned {item['error'].get('code', 'an error')}.")
            continue
        if isinstance(item.get("statements"), list):
            # Explanation evidence: the deterministic statements already quote the evidence verbatim
            # with their citation keys, so the fixture narrative is exactly those statements.
            parts.extend(str(s) for s in item["statements"])
            continue
        summary = item.get("summary") or {}
        fields = [f"{k} {_fmt(v)}" for k, v in sorted(summary.items()) if isinstance(v, (str, int, float, bool)) and v is not None]
        parts.append(f"{tool} reported " + ("; ".join(fields) if fields else "no scalar fields") + ".")
    if all(isinstance(item.get("statements"), list) for item in evidence):
        return " ".join(parts)
    return "Based on the tool results in this session: " + " ".join(parts)


class FixtureProvider:
    kind = "fixture"
    model_id: str | None = None

    def __init__(self) -> None:
        self.calls = 0

    def estimate_input_tokens(self, request: GenerateRequest) -> int:
        return estimate_tokens(request.prompt_chars())

    def generate(self, request: GenerateRequest, on_token: Callable[[str], None] | None = None) -> GenerateResult:
        self.calls += 1
        if request.purpose == "plan":
            calls = _plan(request)
            return GenerateResult(text="", tool_calls=tuple(calls), usage=Usage(), stop_reason="tool_use" if calls else "end_turn", provider_kind=self.kind, model_id=None)
        text = fixture_narrative(request.evidence)
        words = text.split(" ")
        if on_token is not None:
            for i, w in enumerate(words):
                on_token(w if i == 0 else " " + w)
        # Token accounting is deterministic (word counts); the fixture provider costs nothing.
        usage = Usage(input_tokens=self.estimate_input_tokens(request), output_tokens=len(words))
        return GenerateResult(text=text, tool_calls=(), usage=usage, stop_reason="end_turn", provider_kind=self.kind, model_id=None)
