"""Deterministic correctness checks over evidence (spec explanation-evidence "Deterministic correctness
checks"; FA-EV-04; task 1.3).

Every check is plain arithmetic over numbers that a tool computed; nothing here produces a figure that
is shown to the user (a check record carries the tool's own values and the absolute difference only).
A check with status ``failed`` blocks narration and the explanation returns ``VALIDATION_FAILED``
naming it. LLM judging is never a correctness gate.

Check record (``checks[]`` of the explanation result)::

    {"name": str, "status": "passed" | "failed" | "not_applicable",
     "tolerance": float, "observed": float | None, "evidence": "<citation key>", "detail": str}
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Check",
    "allocation_delta_check",
    "allocation_match",
    "close",
    "constraint_check",
    "failed",
    "identity_check",
    "sum_check",
    "weights_sum_check",
]

PASSED, FAILED, NA = "passed", "failed", "not_applicable"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    tolerance: float
    observed: float | None = None
    evidence: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "status": self.status, "tolerance": self.tolerance, "observed": self.observed, "evidence": self.evidence}
        if self.detail:
            out["detail"] = self.detail
        if self.extra:
            out.update(self.extra)
        return out


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def close(a: Any, b: Any, tol: float) -> bool:
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(x - y) <= tol + 1e-12


def failed(checks: Iterable[Check]) -> list[Check]:
    return [c for c in checks if c.status == FAILED]


def _diff(a: float, b: float) -> float:
    return round(abs(a - b), 12)


def identity_check(name: str, *, lhs_terms: Mapping[str, Any], signs: Mapping[str, int], total: Any, tol: float, evidence: str = "") -> Check:
    """``total == Σ sign * term`` within ``tol`` (accounting identity, e.g. start + flows + P&L - fees = end).
    A missing or non-numeric term fails the check (nothing is invented)."""
    acc = 0.0
    for key, sign in signs.items():
        v = _num(lhs_terms.get(key))
        if v is None:
            return Check(name, FAILED, tol, None, evidence, f"term {key} is missing or not a number")
        acc += sign * v
    t = _num(total)
    if t is None:
        return Check(name, FAILED, tol, None, evidence, "total is missing or not a number")
    d = _diff(acc, t)
    return Check(name, PASSED if d <= tol + 1e-12 else FAILED, tol, d, evidence)


def sum_check(name: str, *, components: Mapping[str, Any], total: Any, tol: float, evidence: str = "", skip: Iterable[str] = ()) -> Check:
    """Components (minus ``skip``ped, i.e. ``not_available``) sum to the total within ``tol``."""
    return identity_check(name, lhs_terms=components, signs={k: 1 for k in components if k not in set(skip)}, total=total, tol=tol, evidence=evidence)


def weights_sum_check(name: str, allocation: Mapping[str, Any] | None, *, tol: float, evidence: str = "", cash_weight: Any = None) -> Check:
    if not allocation:
        return Check(name, FAILED, tol, None, evidence, "allocation is missing")
    vals = [_num(v) for v in allocation.values()]
    if any(v is None for v in vals):
        return Check(name, FAILED, tol, None, evidence, "a weight is not a number")
    total = sum(v for v in vals if v is not None) + (_num(cash_weight) or 0.0)
    d = _diff(total, 1.0)
    return Check(name, PASSED if d <= tol + 1e-12 else FAILED, tol, d, evidence)


def allocation_match(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None, tol: float) -> tuple[bool, float | None]:
    """Same holdings set and every weight within ``tol``; returns (match, max absolute difference)."""
    if not a or not b:
        return False, None
    keys = set(a) | set(b)
    worst = 0.0
    for k in keys:
        x, y = _num(a.get(k, 0.0)), _num(b.get(k, 0.0))
        if x is None or y is None:
            return False, None
        worst = max(worst, abs(x - y))
    return worst <= tol + 1e-12, round(worst, 12)


def allocation_delta_check(name: str, *, base: Mapping[str, Any], point: Mapping[str, Any], reported_change: Mapping[str, Any] | None, tol: float, evidence: str = "") -> Check:
    """The reported per-holding change equals ``point - base`` for EVERY holding of either allocation
    (portfolio-wide effects: a change reported only for the constrained asset fails)."""
    if reported_change is None:
        return Check(name, FAILED, tol, None, evidence, "per-holding change is missing")
    holdings = set(base) | set(point)
    missing = sorted(h for h in holdings if h not in reported_change)
    if missing:
        return Check(name, FAILED, tol, None, evidence, "change not reported for every holding", {"missing_holdings": missing})
    worst = 0.0
    for h in holdings:
        x, y, c = _num(point.get(h, 0.0)), _num(base.get(h, 0.0)), _num(reported_change.get(h))
        if x is None or y is None or c is None:
            return Check(name, FAILED, tol, None, evidence, f"holding {h} has a non-numeric value")
        worst = max(worst, abs((x - y) - c))
    worst = round(worst, 12)
    return Check(name, PASSED if worst <= tol + 1e-12 else FAILED, tol, worst, evidence)


def constraint_check(name: str, reported: Mapping[str, Any] | None, *, tol: float, evidence: str = "") -> Check:
    """Constraint satisfaction as REPORTED by the tool: every reported slack must be >= -tol."""
    if not reported:
        return Check(name, NA, tol, None, evidence, "no constraint slack reported")
    worst = 0.0
    for k, v in reported.items():
        s = _num(v)
        if s is None:
            return Check(name, FAILED, tol, None, evidence, f"slack {k} is not a number")
        worst = min(worst, s)
    return Check(name, PASSED if worst >= -tol - 1e-12 else FAILED, tol, round(worst, 12), evidence)
