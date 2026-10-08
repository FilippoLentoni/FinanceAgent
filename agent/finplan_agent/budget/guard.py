"""Token, tool-call and Bedrock budget guards (design D4 "Budget"; tasks 3.4, 3.6).

Before EVERY ``bedrock`` provider invocation the graph calls :meth:`BudgetGuard.preflight`:

1. the invocation's worst case = (estimated input tokens + ``max_tokens_invocation``) priced at the
   configured rates (input priced at the higher of the input and cache-write rates when caching is
   effective, so cache writes cannot exceed the estimate);
2. refusal when the platform budget-state flag says ``enforced``;
3. refusal when the remaining ``bedrock_explanations`` allocation (allocation minus the month-to-date
   estimate summed over ALL environments from the agent's own usage metrics) is below the worst case
   (the contract's own :func:`finplan_contracts.budget.preflight` decides 2 and 3);
4. refusal when the remaining session budget (``max_cost_session_usd`` minus the session's spend) is
   below the worst case;
5. refusal when the turn or session token caps cannot absorb the worst case.

A refusal is ``BUDGET_EXCEEDED`` naming the allocation, the remaining amount, the estimate and the
rates' ``retrieved_at``; the graph then makes no Bedrock call and degrades to a tool-only or
evidence-only answer. The fixture provider costs nothing, so only the token and tool-call caps apply.

The month-to-date estimate is an approximation (metrics lag); the platform's AWS Budgets deny action
on the Runtime role (``budget-enforced-role-names``) is the backstop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..config.provider import ProviderConfig
from ..core.errors import AgentError

__all__ = ["BUDGET_CATEGORY", "BudgetGuard", "BudgetSnapshot", "SpendSource", "StaticSpendSource", "SsmBudgetReader", "Worst"]

BUDGET_CATEGORY = "bedrock_explanations"


class SpendSource(Protocol):
    def month_to_date_usd(self) -> float:
        """Estimated month-to-date Bedrock explanation spend across all environments."""
        ...


class StaticSpendSource:
    def __init__(self, usd: float = 0.0) -> None:
        self.usd = usd

    def month_to_date_usd(self) -> float:
        return self.usd


@dataclass(frozen=True)
class BudgetSnapshot:
    allocation: Mapping[str, float] | None
    budget_state: Any
    ceiling_usd: float | None


class SsmBudgetReader:
    """Reads the platform-owned allocation, budget-state flag and ceiling (read-only)."""

    ALLOCATION = "/finplan/shared/financialplanning/config/budget-allocation"
    STATE = "/finplan/shared/financialplanning/config/budget-state"
    CEILING = "/finplan/shared/financialplanning/config/cost-ceiling-usd"

    def __init__(self, ssm: Any) -> None:
        self._ssm = ssm

    def read(self) -> BudgetSnapshot:
        resp = self._ssm.get_parameters(Names=[self.ALLOCATION, self.STATE, self.CEILING])
        values = {p["Name"]: p["Value"] for p in resp.get("Parameters", [])}
        alloc = values.get(self.ALLOCATION)
        ceiling = values.get(self.CEILING)
        return BudgetSnapshot(
            allocation=json.loads(alloc) if alloc else None,
            budget_state=values.get(self.STATE),
            ceiling_usd=float(ceiling) if ceiling else None,
        )


@dataclass(frozen=True)
class Worst:
    input_tokens: int
    output_tokens: int
    usd: float


class BudgetGuard:
    def __init__(self, config: ProviderConfig, *, spend: SpendSource | None = None, budget_reader: Any = None) -> None:
        self.config = config
        self.spend = spend or StaticSpendSource(0.0)
        self.budget_reader = budget_reader

    # ------------------------------------------------------------------ estimates
    def worst_case(self, input_tokens: int, max_tokens: int | None = None) -> Worst:
        out = int(max_tokens if max_tokens is not None else self.config.max_tokens_invocation)
        rates = self.config.rates
        if self.config.kind != "bedrock" or rates is None:
            return Worst(input_tokens, out, 0.0)
        in_rate = rates.input_per_1k_usd
        if self.config.caching_effective and rates.cache_write_per_1k_usd is not None:
            in_rate = max(in_rate, rates.cache_write_per_1k_usd)
        return Worst(input_tokens, out, input_tokens / 1000 * in_rate + out / 1000 * rates.output_per_1k_usd)

    # ------------------------------------------------------------------ caps
    def check_tool_calls(self, used_in_turn: int, requested: int, correlation_id: str) -> None:
        limit = self.config.max_tool_calls_turn
        if used_in_turn + requested > limit:
            raise AgentError.budget(f"the per-turn tool-call limit ({limit}) would be exceeded", limit="max_tool_calls_turn", value=limit, used=used_in_turn, requested=requested)

    def preflight(self, *, worst: Worst, turn_tokens: int, session_tokens: int, session_spent_usd: float, correlation_id: str) -> dict[str, Any]:
        """Raise ``BUDGET_EXCEEDED`` unless the invocation's worst case fits every cap and budget.
        Returns the check record (kept in the session usage)."""
        cfg = self.config
        tokens = worst.input_tokens + worst.output_tokens
        if turn_tokens + tokens > cfg.max_tokens_turn:
            raise AgentError.budget(f"the per-turn token limit ({cfg.max_tokens_turn}) would be exceeded", limit="max_tokens_turn", value=cfg.max_tokens_turn, used=turn_tokens, estimate_tokens=tokens)
        if session_tokens + tokens > cfg.max_tokens_session:
            raise AgentError.budget(f"the per-session token limit ({cfg.max_tokens_session}) would be exceeded", limit="max_tokens_session", value=cfg.max_tokens_session, used=session_tokens, estimate_tokens=tokens)
        record: dict[str, Any] = {"estimated_usd_upper_bound": round(worst.usd, 8)}
        if cfg.kind != "bedrock":
            return record
        retrieved_at = cfg.rates.retrieved_at if cfg.rates else None
        session_remaining = cfg.max_cost_session_usd - session_spent_usd
        if worst.usd > session_remaining + 1e-12:
            raise AgentError.budget(
                "the remaining session budget cannot cover this explanation invocation",
                budget_category=BUDGET_CATEGORY,
                limit="max_cost_session_usd",
                session_remaining_usd=round(max(session_remaining, 0.0), 6),
                estimated_usd_upper_bound=round(worst.usd, 6),
                rates_retrieved_at=retrieved_at,
            )
        from finplan_contracts import budget as cb

        snap = self.budget_reader.read() if self.budget_reader is not None else BudgetSnapshot(None, None, None)
        mtd = float(self.spend.month_to_date_usd())
        result = cb.preflight(
            BUDGET_CATEGORY,
            worst.usd,
            allocation=snap.allocation,
            spend_records=[cb.CostRecord(usd=mtd, category=BUDGET_CATEGORY, description="FinanceAgent usage metrics, all environments")],
            ceiling_usd=snap.ceiling_usd,
            budget_state=snap.budget_state,
            correlation_id=correlation_id,
        )
        allocation_usd = float((snap.allocation or cb.DEFAULT_ALLOCATION).get(BUDGET_CATEGORY, 0.0))
        record.update(allocation_usd=allocation_usd, month_to_date_usd=round(mtd, 6), remaining_allocation_usd=result.remaining_allocation_usd, session_remaining_usd=round(session_remaining, 6), rates_retrieved_at=retrieved_at)
        if not result.allowed:
            err = result.error or {}
            details = {**err.get("details", {}), "budget_category": BUDGET_CATEGORY, "allocation_usd": allocation_usd, "month_to_date_usd": round(mtd, 6), "estimated_usd_upper_bound": round(worst.usd, 6), "rates_retrieved_at": retrieved_at}
            code = err.get("code", "BUDGET_EXCEEDED")
            if code != "BUDGET_EXCEEDED":
                # An invalid allocation refuses all paid work; surface it as a budget refusal of this call.
                details["allocation_problem"] = err.get("message")
            raise AgentError.budget(err.get("message") or "the bedrock_explanations allocation cannot cover this invocation", **details)
        return record
