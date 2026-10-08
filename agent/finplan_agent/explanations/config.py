"""Explanation settings: the per-environment flag, the request limits and the narration cap (design E7).

Sources, in order (later wins, field by field):

1. ``config/<env>.json`` ``explanations`` block (repository defaults; conservative);
2. ``/finplan/<env>/financeagent/config/explanation-limits`` (JSON, FinanceAgent-owned, optional;
   CONTRACT GAP: not yet a registered contract key).

Fields::

    {"enabled": bool,                          # explanations.enabled (design Migration Plan step 2)
     "max_resolves_per_request": int,          # controlled re-solves (k + 2) and sweep points together
     "max_shapley_groups": int,                # exact grouped Shapley: 2^k re-solves
     "max_sweep_points": int,
     "max_estimated_usd_per_request": float,   # sum of the dry-run upper bounds of one request
     "narration_max_tokens_invocation": int,   # explanation skills' per-invocation output cap
     "status_polls": int,                      # get_job_status reads per turn before in_progress
     "default_tolerance": float}

No model identifier and no price lives here: narration uses the phase 1 provider configuration.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..core.errors import AgentError

__all__ = ["DEFAULTS", "ExplanationSettings", "load_explanation_settings", "EVIDENCE_BUDGET_CATEGORY"]

#: Evidence jobs are CPU research jobs (design E7); narration is ``bedrock_explanations``.
EVIDENCE_BUDGET_CATEGORY = "cpu_research"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "max_resolves_per_request": 12,
    "max_shapley_groups": 4,
    "max_sweep_points": 11,
    "max_estimated_usd_per_request": 0.5,
    "narration_max_tokens_invocation": 600,
    "status_polls": 1,
    "default_tolerance": 1e-6,
}
_INTS = ("max_resolves_per_request", "max_shapley_groups", "max_sweep_points", "narration_max_tokens_invocation", "status_polls")


@dataclass(frozen=True)
class ExplanationSettings:
    enabled: bool = False
    max_resolves_per_request: int = 12
    max_shapley_groups: int = 4
    max_sweep_points: int = 11
    max_estimated_usd_per_request: float = 0.5
    narration_max_tokens_invocation: int = 600
    status_polls: int = 1
    default_tolerance: float = 1e-6

    @classmethod
    def from_mapping(cls, doc: Mapping[str, Any]) -> ExplanationSettings:
        merged = {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}
        for k in _INTS:
            v = merged[k]
            if isinstance(v, bool) or not isinstance(v, int) or v < (0 if k == "status_polls" else 1):
                raise AgentError.validation(f"explanation setting {k} must be a positive integer", pointer=f"/{k}")
        for k in ("max_estimated_usd_per_request", "default_tolerance"):
            v = merged[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise AgentError.validation(f"explanation setting {k} must be a non-negative number", pointer=f"/{k}")
        if not isinstance(merged["enabled"], bool):
            raise AgentError.validation("explanations.enabled must be a boolean", pointer="/enabled")
        return cls(**{k: merged[k] for k in DEFAULTS})

    def narration_cap(self, provider_cap: int) -> int:
        """The narration call's ``max_tokens``: the skill cap, never above the provider's own cap."""
        return max(1, min(self.narration_max_tokens_invocation, provider_cap))

    def with_overrides(self, **kw: Any) -> ExplanationSettings:
        return replace(self, **kw)


def load_explanation_settings(repo_config: Mapping[str, Any], ssm: Any = None, parameter: str | None = None) -> ExplanationSettings:
    doc: dict[str, Any] = dict(repo_config.get("explanations") or {})
    if ssm is not None and parameter:
        try:
            value = ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
        except Exception:  # noqa: BLE001 - optional parameter: repository defaults apply
            value = None
        if value:
            try:
                over = json.loads(value)
            except json.JSONDecodeError:
                raise AgentError.validation("explanation-limits is not JSON", pointer="/explanation-limits") from None
            if not isinstance(over, dict):
                raise AgentError.validation("explanation-limits must be a JSON object", pointer="/explanation-limits")
            doc.update(over)
    return ExplanationSettings.from_mapping(doc)
