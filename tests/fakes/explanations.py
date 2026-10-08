"""Fixture tool backends for the explanation workflows (add-explanation-workflows design E8).

:class:`ExplanationBackend` stands in for the Gateway tools an explanation uses, including the ones
that do not exist yet (EX-OQ-9: ``get_publication``, ``list_publications``, ``list_executions``,
``compare_plan_versions``) and the FinanceModel evidence job types (GAP-E1). The evidence it returns is
computed by small deterministic toy models with PLANTED ground truth:

* :func:`toy_resolve_set` / :func:`toy_shapley`: value(S) = base + sum of planted single effects of the
  switched groups + a planted interaction term applied only when ALL groups are switched. Additive
  model (interaction 0): Shapley values equal the single effects. Exact Shapley is enumerated over all
  2^k coalitions, as FinanceModel would.
* :func:`performance_evidence`: planted execution slippage, fee overrun, market-versus-forecast and
  residual, with reconciling plan and executed paths.
* :func:`sweep_evidence`: a sweep with configurable infeasible, flat (no-effect) and non-monotonic points.

Every evidence document gets a trusted ``explanation_evidence`` reference whose checksum is the SHA-256
of its canonical JSON (so recomputation from the same inputs reproduces the checksum).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from finplan_agent.explanations.config import ExplanationSettings
from finplan_agent.providers.base import ToolSpec
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.mcp_client import ToolOutcome
from tests.fakes.agent import TOOLS, make_service

BASE = "01JABCDEFGHJKMNPQRSTVWXY"


def uid(prefix: str, n: int) -> str:
    return f"{prefix}_{BASE}{n:02d}"


PV_PREV, PV_NEW, PV_C = uid("pv", 1), uid("pv", 2), uid("pv", 3)
PL = uid("pl", 1)
PUB = uid("pub", 1)
EXE = uid("exe", 1)
SNAP_PREV, SNAP_NEW, SNAP_REAL = uid("snap", 1), uid("snap", 2), uid("snap", 3)
MV_PREV, MV_NEW = uid("mv", 1), uid("mv", 2)
CFG_A = "cfg_" + "a1" * 32
CFG_B = "cfg_" + "b2" * 32
CHK_PREV = "sha256:" + "11" * 32
CHK_NEW = "sha256:" + "22" * 32
WINDOW = {"start": "2026-01-02", "end": "2026-03-31"}
EXPLAIN_READ_TOOLS = ("get_publication", "list_publications", "list_executions", "compare_plan_versions")


def checksum(doc: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_ref(doc: Any, n: int) -> dict[str, Any]:
    return {"artifact_id": f"art_{BASE}{n:02d}", "owner": "financemodel", "kind": "explanation_evidence", "checksum": checksum(doc), "content_type": "application/json", "domain": "finance", "synthetic": True}


def r(x: float) -> float:
    return round(x, 10)


# ============================================================================ toy models
def _value(base: dict[str, Any], effects: dict[str, dict[str, Any]], interaction: dict[str, Any], groups: list[str], switched: set[str]) -> dict[str, Any]:
    out = {q: base[q] for q in ("objective", "expected_return", "risk")}
    alloc = dict(base["allocation"])
    for g in switched:
        for q in out:
            out[q] += effects[g].get(q, 0.0)
        for i, d in (effects[g].get("weights") or {}).items():
            alloc[i] = alloc.get(i, 0.0) + d
    if switched == set(groups):
        for q in out:
            out[q] += interaction.get(q, 0.0)
        for i, d in (interaction.get("weights") or {}).items():
            alloc[i] = alloc.get(i, 0.0) + d
    return {**{q: r(v) for q, v in out.items()}, "allocation": {i: r(w) for i, w in alloc.items()}}


def toy_resolve_set(groups: list[str], effects: dict[str, dict[str, Any]], *, interaction: dict[str, Any] | None = None, infeasible: tuple[str, ...] = (), base_drift: float = 0.0, deterministic: bool = True) -> dict[str, Any]:
    interaction = interaction or {}
    base = {"objective": 0.5, "expected_return": 0.06, "risk": 0.12, "allocation": {"SPY": 0.6, "AGG": 0.4}}
    prev = dict(base)
    new = _value(base, effects, interaction, groups, set(groups))
    base_run = _value(base, effects, interaction, groups, set())
    if base_drift:
        base_run["allocation"] = {"SPY": r(0.6 + base_drift), "AGG": r(0.4 - base_drift)}
    runs = [{"label": "base", "switched": [], "solution_status": "optimal", **base_run}]
    eff_out: dict[str, Any] = {}
    for g in groups:
        if g in infeasible:
            runs.append({"label": f"switch:{g}", "switched": [g], "solution_status": "infeasible"})
            eff_out[g] = None
            continue
        v = _value(base, effects, interaction, groups, {g})
        runs.append({"label": f"switch:{g}", "switched": [g], "solution_status": "optimal", **v})
        eff_out[g] = {q: r(v[q] - base[q]) for q in ("objective", "expected_return", "risk")} | {"weights": {i: r(v["allocation"][i] - base["allocation"][i]) for i in v["allocation"]}}
    runs.append({"label": "all", "switched": list(groups), "solution_status": "optimal", **new})
    total = {q: r(new[q] - base[q]) for q in ("objective", "expected_return", "risk")}
    rem = None if infeasible else {q: r(total[q] - sum(eff_out[g][q] for g in groups)) for q in total}
    lineage = {"solver": "clarabel", "solver_version": "0.9.0", "tolerance": 1e-8, "seeds": [7] if deterministic else [7, 11, 13], "code_version": "fm-1.4.0", "deterministic": deterministic}
    doc = {
        "evidence_kind": "controlled_resolve_set",
        "subject": {"plan_version_id": PV_NEW, "compare_to_plan_version_id": PV_PREV},
        "groups": groups,
        "tolerance": 1e-6,
        "previous": {k: prev[k] for k in ("objective", "expected_return", "risk", "allocation")},
        "new": new,
        "runs": runs,
        "effects": eff_out,
        "total": total,
        "interaction_remainder": rem,
        "lineage": lineage,
        "evaluator_version": "fm-1.4.0",
        "identifiers": {"input_snapshot_id": SNAP_PREV, "configuration_id": CFG_A, "model_version": MV_PREV},
    }
    if not deterministic:
        doc["spread"] = {g: {"objective": 0.004} for g in groups}
    return doc


def toy_shapley(groups: list[str], effects: dict[str, dict[str, Any]], *, interaction: dict[str, Any] | None = None, tamper: float = 0.0) -> dict[str, Any]:
    interaction = interaction or {}
    base = {"objective": 0.5, "expected_return": 0.06, "risk": 0.12, "allocation": {"SPY": 0.6, "AGG": 0.4}}
    k = len(groups)
    phi: dict[str, dict[str, float]] = {g: {"objective": 0.0, "expected_return": 0.0, "risk": 0.0} for g in groups}
    for g in groups:
        others = [x for x in groups if x != g]
        for size in range(k):
            for coal in itertools.combinations(others, size):
                wgt = math.factorial(size) * math.factorial(k - size - 1) / math.factorial(k)
                with_g = _value(base, effects, interaction, groups, set(coal) | {g})
                without = _value(base, effects, interaction, groups, set(coal))
                for q in phi[g]:
                    phi[g][q] += wgt * (with_g[q] - without[q])
    phi = {g: {q: r(v) for q, v in d.items()} for g, d in phi.items()}
    if tamper:
        phi[groups[0]]["objective"] = r(phi[groups[0]]["objective"] + tamper)
    full = _value(base, effects, interaction, groups, set(groups))
    total = {q: r(full[q] - base[q]) for q in ("objective", "expected_return", "risk")}
    return {"evidence_kind": "grouped_shapley", "subject": {"plan_version_id": PV_NEW}, "groups": groups, "k": k, "resolves": 2**k, "ordering_invariant": True, "tolerance": 1e-6, "phi": phi, "total": total, "evaluator_version": "fm-1.4.0"}


def performance_evidence(
    *,
    slippage: float = -0.004,
    fee_overrun: float = -0.0015,
    market: float = 0.012,
    residual: float = 0.0,
    total_override: float | None = None,
    executed_end_override: float | None = None,
    forecast: bool = True,
    partial_day: str | None = None,
    revision: bool = False,
) -> dict[str, Any]:
    total = r(slippage + fee_overrun + market + residual) if total_override is None else total_override
    plan_path = {"start_value": 100000.0, "cash_flows": 0.0, "realized_pnl": 1800.0, "unrealized_pnl": 700.0, "fees": 50.0, "end_value": 102450.0}
    executed = {"start_value": 100000.0, "cash_flows": 0.0, "realized_pnl": 1500.0, "unrealized_pnl": 600.0, "fees": 200.0, "end_value": 101900.0 if executed_end_override is None else executed_end_override}
    doc: dict[str, Any] = {
        "evidence_kind": "performance_decomposition",
        "subject": {"plan_version_id": PV_PREV},
        "identifiers": {"publication_id": PUB, "plan_version_id": PV_PREV, "plan_version_checksum": CHK_PREV, "execution_ids": [EXE], "input_snapshot_id": SNAP_PREV, "realized_snapshot_id": SNAP_REAL},
        "window": dict(WINDOW),
        "actual_source": "paper",
        "excluded_days": [{"date": partial_day, "reason": "intraday_partial"}] if partial_day else [],
        "reconciliation": {"tolerance": 0.01, "plan_path": plan_path, "executed_path": executed},
        "gap": {"tolerance": 1e-6, "total": total, "components": {"execution": slippage, "cost": fee_overrun, "market_vs_forecast": market, "residual": residual}, "not_available": []},
        "forecast": {"status": "available", "level": 0.9, "lower": 0.01, "upper": 0.09, "mean": 0.05, "realized_return": 0.0245, "percentile": 31.5, "inside_interval": True} if forecast else {"status": "not_available"},
        "data_quality": {"flags": [], "revisions": []},
        "evaluator_version": "fm-1.4.0",
        "seeds": [],
    }
    if revision:
        doc["data_quality"]["revisions"] = [{"instrument": "SPY", "date": "2026-01-15", "previous_snapshot_id": SNAP_PREV, "new_snapshot_id": SNAP_REAL, "previous_value": 472.31, "new_value": 472.8, "modeled_effect": 0.0003}]
        doc["data_quality"]["flags"] = [{"snapshot_id": SNAP_REAL, "flag": "revised_observation", "instrument": "SPY"}]
    return doc


def sweep_evidence(
    values: list[float], *, name: str = "turnover_limit", infeasible: tuple[int, ...] = (), flat: tuple[int, ...] = (), risks: list[float] | None = None, drop_change_for: str | None = None, turnovers: list[float] | None = None
) -> dict[str, Any]:
    base_alloc = {"SPY": 0.6, "AGG": 0.4, "GLD": 0.0}
    points = []
    for i, v in enumerate(values):
        if i in infeasible:
            points.append({"value": v, "completion_status": "succeeded", "solution_status": "infeasible"})
            continue
        shift = 0.0 if i in flat else r(0.02 * (i + 1))
        alloc = {"SPY": r(0.6 - shift), "AGG": r(0.4 - shift / 2), "GLD": r(shift * 1.5)}
        change = {h: r(alloc[h] - base_alloc[h]) for h in alloc}
        if drop_change_for:
            change = {drop_change_for: change[drop_change_for]}
        metrics = {
            "expected_return": r(0.06 + shift / 10),
            "risk": (risks[i] if risks else r(0.12 + shift / 5)),
            "turnover": turnovers[i] if turnovers else r(shift * 2),
            "cost_estimate": r(shift / 100),
        }
        points.append(
            {
                "value": v,
                "completion_status": "succeeded",
                "solution_status": "no_effect" if i in flat else "optimal",
                "allocation": alloc,
                "allocation_change": change,
                "metrics": metrics,
                "constraint_slack": {"budget": 0.0, "max_weight": r(0.7 - max(alloc.values()))},
            }
        )
    return {
        "evidence_kind": "sensitivity_sweep",
        "subject": {"plan_version_id": PV_PREV},
        "base_plan_version_id": PV_PREV,
        "parameter": {"name": name, "values": values},
        "fixed_inputs": {},
        "tolerance": 1e-6,
        "base": {"allocation": base_alloc, "metrics": {"expected_return": 0.06, "risk": 0.12}},
        "points": points,
        "evaluator_version": "fm-1.4.0",
    }


def inventory(
    *,
    differences: list[dict[str, Any]] | None = None,
    identical: bool = False,
    origin: str = "model_run",
    horizon_new: dict[str, Any] | None = None,
    family: str = "classical",
    spec_hashes: tuple[str, str] = ("h1", "h1"),
    deterministic: bool = True,
) -> dict[str, Any]:
    horizon = {"holding_period": "P1M", "rebalance": "monthly"}
    prev = {
        "plan_version_id": PV_PREV,
        "checksum": CHK_PREV,
        "configuration_id": CFG_A,
        "input_snapshot_id": SNAP_PREV,
        "model_version": MV_PREV,
        "origin": "model_run",
        "horizon": horizon,
        "model_family": family,
        "model_spec_hash": spec_hashes[0],
        "evaluation_window": dict(WINDOW),
        "lineage": {"solver": "clarabel", "seeds": [7], "deterministic": deterministic},
    }
    if identical:
        new = {**prev, "plan_version_id": PV_NEW}
        diffs: list[dict[str, Any]] = []
    else:
        new = {
            "plan_version_id": PV_NEW,
            "checksum": CHK_NEW,
            "configuration_id": CFG_B,
            "input_snapshot_id": SNAP_NEW,
            "model_version": MV_NEW,
            "origin": origin,
            "horizon": horizon_new or horizon,
            "model_family": family,
            "model_spec_hash": spec_hashes[1],
            "lineage": prev["lineage"],
        }
        diffs = (
            differences
            if differences is not None
            else [
                {"group": "data", "previous": SNAP_PREV, "new": SNAP_NEW},
                {"group": "configuration", "pointer": "/payload/risk_aversion", "previous": 2.0, "new": 2.5, "previous_configuration_id": CFG_A, "new_configuration_id": CFG_B},
            ]
        )
    return {"evidence_kind": "difference_inventory", "subject": {"plan_version_id": PV_NEW, "compare_to_plan_version_id": PV_PREV}, "previous": prev, "new": new, "differences": diffs, "observation_changes": []}


# ============================================================================ backend
@dataclass
class ExplanationBackend:
    """Fake Gateway tools for explanations. ``evidence`` maps job types to evidence documents."""

    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    inventory_doc: dict[str, Any] | None = None
    estimate_usd: float = 0.02
    remaining_usd: float = 6.5
    category: str = "cpu_research"
    job_state: str = "succeeded"
    deny: tuple[str, ...] = ()
    publications: list[dict[str, Any]] | None = None
    executions: list[dict[str, Any]] | None = None
    constraints: dict[str, Any] = field(default_factory=lambda: {"max_turnover": 0.2, "max_weight": 0.7})
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    run_ids: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- ToolClient protocol
    def list_tools(self) -> list[ToolSpec]:
        names = [n for n, _, _ in TOOLS] + list(EXPLAIN_READ_TOOLS)
        return [ToolSpec(name=n, description=f"{n} tool", input_schema={"type": "object"}) for n in names]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, json.loads(json.dumps(arguments))))
        if name in self.deny:
            return ToolOutcome(tool=name, ok=False, error={"code": "FORBIDDEN", "message": "denied by the Gateway policy for the caller's groups", "retryable": False, "details": {}})
        fn: Callable[[dict[str, Any]], Any] | None = getattr(self, f"_t_{name}", None)
        if fn is None:
            return ToolOutcome(tool=name, ok=False, error={"code": "NOT_FOUND", "message": f"tool {name} is not offered by the Gateway", "retryable": False, "details": {}})
        try:
            return ToolOutcome(tool=name, ok=True, result=fn(arguments))
        except KeyError as exc:
            return ToolOutcome(tool=name, ok=False, error={"code": "NOT_FOUND", "message": f"unknown {exc}", "retryable": False, "details": {}})

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    # ---------------------------------------------------------------- tools
    def _t_get_publication(self, a: dict[str, Any]) -> Any:
        pubs = {p["publication_id"]: p for p in self._pubs()}
        return pubs[a["publication_id"]]

    def _pubs(self) -> list[dict[str, Any]]:
        if self.publications is not None:
            return self.publications
        return [{"publication_id": PUB, "plan_id": PL, "plan_version_id": PV_PREV, "plan_version_checksum": CHK_PREV, "plan_version_status": "validated", "published_at": "2026-01-02T14:00:00Z"}]

    def _t_list_publications(self, a: dict[str, Any]) -> Any:
        return {"plan_id": a["plan_id"], "publications": [p for p in self._pubs() if p["plan_id"] == a["plan_id"]]}

    def _t_list_executions(self, a: dict[str, Any]) -> Any:
        execs = self.executions if self.executions is not None else [{"execution_id": EXE, "publication_id": PUB, "mode": "paper", "requested_at": "2026-01-02T15:00:00Z"}]
        return {"executions": [e for e in execs if e["publication_id"] == a["publication_id"]]}

    def _t_get_plan_version(self, a: dict[str, Any]) -> Any:
        pv = a["plan_version_id"]
        chk = {PV_PREV: CHK_PREV, PV_NEW: CHK_NEW}.get(pv, "sha256:" + "33" * 32)
        content = {"base_currency": "USD", "allocation": {"weights": [{"instrument_id": "SPY", "weight": 0.6}, {"instrument_id": "AGG", "weight": 0.4}]}, "constraints": dict(self.constraints)}
        return {
            "plan_version": {
                "plan_version_id": pv,
                "plan_id": PL,
                "parent_plan_version_id": None,
                "input_snapshot_id": SNAP_PREV,
                "configuration_id": CFG_A,
                "model_version": MV_PREV,
                "run_id": uid("run", 90),
                "origin": "model_run",
                "status": "validated",
                "checksum": chk,
                "domain": "finance",
                "domain_schema_version": "1.0",
                "content": content,
                "evaluation_window": dict(WINDOW),
            }
        }

    def _t_get_plan(self, a: dict[str, Any]) -> Any:
        return {"plan": {"plan_id": a["plan_id"], "revision": 4}, "plan_id": a["plan_id"], "revision": 4}

    def _t_compare_plan_versions(self, a: dict[str, Any]) -> Any:
        doc = self.inventory_doc or inventory()
        return {"evidence": doc, "evidence_ref": evidence_ref(doc, 1)}

    def _t_submit_experiment(self, a: dict[str, Any]) -> Any:
        est = {"estimated_usd_upper_bound": self.estimate_usd, "price_retrieved_at": "2026-10-01T00:00:00Z", "remaining_allocation_usd": self.remaining_usd, "budget_category": self.category, "synthetic": True}
        cfg = "cfg_" + hashlib.sha256(json.dumps(a["configuration"], sort_keys=True).encode()).hexdigest()
        if a.get("dry_run"):
            return {"run_id": None, "state": None, "dry_run": True, "configuration_id": cfg, "cost_estimate": est}
        run = uid("run", 10 + len(self.run_ids))
        self.run_ids[run] = a["job_type"]
        return {"run_id": run, "state": "queued", "dry_run": False, "configuration_id": cfg, "cost_estimate": est}

    def _t_get_job_status(self, a: dict[str, Any]) -> Any:
        return {"run_id": a["run_id"], "state": self.job_state, "purpose": "research"}

    def _t_get_experiment_result(self, a: dict[str, Any]) -> Any:
        job_type = self.run_ids[a["run_id"]]
        doc = self.evidence[job_type]
        n = 2 + sorted(self.run_ids).index(a["run_id"])
        solution = "no_effect" if job_type == "sensitivity_sweep" and all(p.get("solution_status") == "no_effect" for p in doc.get("points", [])) else "optimal"
        return {
            "run_id": a["run_id"],
            "completion_status": "succeeded",
            "solution_status": solution,
            "artifacts": [evidence_ref(doc, n)],
            "artifacts_complete": True,
            "payload": {"performance": {}, "accuracy": {}, "compute_cost": {"estimated_usd": self.estimate_usd}, "evidence": doc},
            "synthetic": True,
        }

    def _t_create_override_version(self, a: dict[str, Any]) -> Any:
        return {"plan_version_id": uid("pv", 50), "parent_plan_version_id": a["parent_plan_version_id"], "status": "pending_validation"}


def catalog() -> ToolCatalog:
    entries = [CatalogEntry(n, sc, rc) for n, sc, rc in TOOLS] + [CatalogEntry(n, False, "reader") for n in EXPLAIN_READ_TOOLS]
    return ToolCatalog(entries, environment="beta")


def explain_service(backend: ExplanationBackend, *, settings: ExplanationSettings | None = None, **kw: Any) -> Any:
    svc, _ = make_service(tools=backend, **kw)  # type: ignore[arg-type]
    svc.deps.catalog_loader = catalog
    svc.deps.explanation_settings = settings or ExplanationSettings(enabled=True)
    return svc
