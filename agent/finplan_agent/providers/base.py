"""The pluggable explanation-provider interface (design D4; spec "Pluggable provider interface").

Every provider implements ONE call, :meth:`ExplanationProvider.generate`, used for both graph roles:

* ``purpose="plan"``: tools are offered and the result may carry tool calls (the tool-calling loop);
* ``purpose="narrate"``: no tools; the result is narrative text over the compact evidence summary.

The graph, the claim check and the budget guards depend only on this module. Messages are
provider-neutral (``{"role": "user"|"assistant", "content": [block, ...]}`` with blocks
``{"text": str}``, ``{"tool_use": {"id", "name", "input"}}``, ``{"tool_result": {"id", "name",
"content", "status"}}``); each adapter converts them to its wire format.

Errors: an adapter raises :class:`~finplan_agent.core.errors.AgentError` with a registered code only
(``DEPENDENCY_UNAVAILABLE``, ``RATE_LIMITED``, ``BUDGET_EXCEEDED``, ``INTERNAL``). It never falls
back silently to another provider.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ExplanationProvider",
    "GenerateRequest",
    "GenerateResult",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "estimate_tokens",
    "text_of",
]


@dataclass(frozen=True)
class ToolSpec:
    """A tool as offered to the model: the Gateway tool name without its ``<target>___`` prefix."""

    name: str
    description: str
    input_schema: dict[str, Any]
    state_changing: bool = True


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(a + b for a, b in zip(asdict(self).values(), asdict(other).values(), strict=True)))

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class GenerateRequest:
    purpose: str  # "plan" | "narrate"
    system: str
    messages: list[dict[str, Any]]
    max_tokens: int
    #: Stable instruction blocks placed after the system prompt (skill instructions, output contract).
    #: Together with ``system`` and ``tools`` they form the cacheable prefix.
    stable_instructions: tuple[str, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    temperature: float | None = None
    #: Compact evidence summary for narration (tool results by tool name); never full artifacts.
    evidence: tuple[dict[str, Any], ...] = ()

    def prompt_chars(self) -> int:
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in self.tools]
        return len(self.system) + sum(len(s) for s in self.stable_instructions) + len(json.dumps(self.messages, default=str)) + len(json.dumps(tools)) + len(json.dumps(list(self.evidence), default=str))


@dataclass(frozen=True)
class GenerateResult:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    stop_reason: str
    provider_kind: str
    model_id: str | None
    estimated_cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def usage_record(self, *, budget_category: str = "bedrock_explanations") -> dict[str, Any]:
        """The usage record (spec "Token and cost guards"): kind, model, tokens and estimated cost."""
        return {
            "provider_kind": self.provider_kind,
            "model_id": self.model_id,
            **self.usage.to_dict(),
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "budget_category": budget_category,
        }


@runtime_checkable
class ExplanationProvider(Protocol):
    kind: str
    model_id: str | None

    def generate(self, request: GenerateRequest, on_token: Callable[[str], None] | None = None) -> GenerateResult:
        """Run one model invocation. ``on_token`` receives text deltas as they arrive (streaming)."""
        ...

    def estimate_input_tokens(self, request: GenerateRequest) -> int:
        """A conservative (over-)estimate of the input tokens of ``request`` for the budget check."""
        ...


def estimate_tokens(chars: int) -> int:
    """Conservative token estimate: one token per three characters (over-counts typical English/JSON)."""
    return max(1, math.ceil(chars / 3))


def text_of(message: dict[str, Any]) -> str:
    return "\n".join(b["text"] for b in message.get("content", []) if isinstance(b, dict) and isinstance(b.get("text"), str))
