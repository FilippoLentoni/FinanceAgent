"""Graph state and per-invocation context (design D1).

``AgentState`` is what LangGraph checkpoints after every step. It holds ONLY conversation messages,
graph bookkeeping, tool-call arguments and COMPACT tool results with identifiers (spec
"Checkpoints are non-authoritative", "No secrets or private holdings in checkpoints"): never tokens,
credentials, raw storage locations or full tool payloads.

``AgentContext`` is the per-invocation dependency bundle passed as LangGraph runtime context. It is NOT
checkpointed: the caller's bearer token lives only inside its tool client.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from ..budget.guard import BudgetGuard
from ..config.provider import ProviderConfig
from ..providers.base import ExplanationProvider, ToolSpec
from ..tools.catalog import ToolCatalog
from ..tools.mcp_client import ToolClient

__all__ = ["AgentState", "AgentContext", "TURN_RESET"]


class AgentState(TypedDict, total=False):
    graph_version: int
    environment: str
    caller: dict[str, Any]
    turn: int
    correlation_id: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    # per-turn bookkeeping
    pending_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    recommendation_reference: dict[str, Any] | None
    portfolio_workflow: dict[str, Any] | None
    skills_used: list[dict[str, Any]]
    turn_tool_calls: int
    turn_tokens: int
    turn_usage: dict[str, Any]
    plan_rounds: int
    draft_text: str
    narrative: str
    narrative_status: str
    claim_check: dict[str, Any]
    status: str
    error: dict[str, Any] | None
    refusal: dict[str, Any] | None
    in_progress: dict[str, Any] | None
    confirmation: dict[str, Any] | None
    degraded: str | None
    # explanation subflow (add-explanation-workflows): validated request, working state, result
    explanation_request: dict[str, Any] | None
    explanation: dict[str, Any] | None
    explanation_result: dict[str, Any] | None
    # session bookkeeping (across turns)
    session_tokens: int
    session_spent_usd: float
    declined: Annotated[list[dict[str, Any]], operator.add]


#: Fields reset at the start of every turn.
TURN_RESET: dict[str, Any] = {
    "pending_calls": [],
    "tool_results": [],
    "recommendation_reference": None,
    "portfolio_workflow": None,
    "skills_used": [],
    "turn_tool_calls": 0,
    "turn_tokens": 0,
    "turn_usage": {},
    "plan_rounds": 0,
    "draft_text": "",
    "narrative": "",
    "narrative_status": "not_needed",
    "claim_check": {},
    "status": "running",
    "error": None,
    "refusal": None,
    "in_progress": None,
    "confirmation": None,
    "degraded": None,
    "explanation": None,
    "explanation_result": None,
}


@dataclass
class AgentContext:
    environment: str
    release_id: str | None
    provider: ExplanationProvider
    provider_config: ProviderConfig
    guard: BudgetGuard
    tools: ToolClient
    catalog: ToolCatalog
    session_id: str
    #: Stable instruction blocks (skills, output contract) placed after the system prompt.
    stable_instructions: tuple[str, ...] = ()
    skills: tuple[dict[str, Any], ...] = ()
    #: Called once per completed turn with the turn's usage record (metrics emission).
    on_usage: Callable[[dict[str, Any]], None] | None = None
    #: Called with every Bedrock cost estimate (local month-to-date accounting).
    on_spend: Callable[[float], None] | None = None
    max_result_chars: int = 4000
    #: Explanation settings (``finplan_agent.explanations.config.ExplanationSettings``).
    explanations: Any = None
    _offered: list[ToolSpec] | None = field(default=None, repr=False)

    def offered_tools(self) -> list[ToolSpec]:
        if self._offered is None:
            self._offered = self.catalog.annotate(self.tools.list_tools())
        return self._offered
