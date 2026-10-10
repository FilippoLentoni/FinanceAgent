"""Route independent portfolio tool families through their own authenticated MCP gateways."""
from __future__ import annotations

from dataclasses import replace

from ..core.errors import AgentError
from ..providers.base import ToolSpec
from .mcp_client import ToolClient, ToolOutcome

LIFECYCLE_TOOLS = frozenset({
    "get_portfolio_history", "list_portfolio_decisions", "get_portfolio_decision", "resolve_portfolio_decision", "list_market_snapshots", "record_agent_activity", "list_agent_activity", "explain_portfolio_decision", "compare_portfolio_decisions", "evaluate_portfolio_decision",
})

CLASSICAL_TOOLS = frozenset({
    "recommend_classical_portfolio", "explain_classical_recommendation", "compare_classical_plans",
    "evaluate_classical_performance", "get_classical_analysis", "list_classical_analyses",
    "research_portfolio_models", "run_portfolio_research", "submit_portfolio_feedback", "research_market_events",
})


class PortfolioMcpClient:
    """Classical names never fall back to the PPO gateway, even during a partial outage."""

    def __init__(self, primary: ToolClient, classical: ToolClient) -> None:
        self.primary = primary
        self.classical = classical
        self._offered: dict[str, tuple[ToolSpec, str]] | None = None

    def list_tools(self) -> list[ToolSpec]:
        if self._offered is None:
            offered: dict[str, tuple[ToolSpec, str]] = {}
            failures = []
            for family, client in (("primary", self.primary), ("classical", self.classical)):
                try:
                    specs = client.list_tools()
                except AgentError as exc:
                    failures.append(exc)
                    continue
                for spec in specs:
                    if family == "primary" and spec.name in CLASSICAL_TOOLS:
                        continue
                    if spec.name in CLASSICAL_TOOLS or spec.name not in offered:
                        offered[spec.name] = spec, family
            if not offered and failures:
                raise failures[0]
            self._offered = offered
        return [value[0] for value in self._offered.values()]

    def call_tool(self, name: str, arguments: dict) -> ToolOutcome:
        self.list_tools()
        entry = (self._offered or {}).get(name)
        if entry is None:
            return ToolOutcome(tool=name, ok=False, error=AgentError("NOT_FOUND", "The requested MCP tool is not available to this caller.", {"tool": name}).to_envelope("corr-mcp-unavailable"))
        family = "classical" if name in CLASSICAL_TOOLS else entry[1]
        outcome = (self.classical if family == "classical" else self.primary).call_tool(name, arguments)
        return replace(outcome, extra={**outcome.extra, "gateway": family})
