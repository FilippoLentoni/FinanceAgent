"""The three explanation workflows plus adopt-alternative (design E3-E5; specs
plan-performance-explanation, recommendation-change-explanation, sensitivity-explanation).

Each workflow has two deterministic halves:

* ``prepare``: validate the request, read the anchoring records through tools (as the caller), build
  the difference inventory or policy, decide which evidence jobs are needed (:class:`JobSpec`) and
  refuse early (``PRECONDITION_FAILED``, ``VALIDATION_FAILED``);
* ``analyze``: given the evidence items, run the deterministic checks, attach labels and render the
  deterministic statements that quote evidence values verbatim with their citation key.

No function here computes a figure that reaches the user: statements quote tool values; checks only
compare them.

Tools assumed beyond the phase 1 catalog (EX-OQ-9, a FinanceLambdasTool follow-up): ``get_publication``,
``list_publications``, ``list_executions`` and ``compare_plan_versions``. Evidence job types assumed
in FinanceModel (GAP-E1): ``performance_decomposition``, ``controlled_resolve``, ``grouped_shapley``,
``sensitivity_sweep``. Both are exercised against fixture tool backends until they ship; a missing
tool surfaces as ``DEPENDENCY_UNAVAILABLE``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import AgentError
from .checks import (
    FAILED,
    NA,
    PASSED,
    Check,
    allocation_delta_check,
    allocation_match,
    close,
    constraint_check,
    identity_check,
    sum_check,
    weights_sum_check,
)
from .config import ExplanationSettings
from .evidence import EvidenceItem, fmt
from .tools import GuardedTools, JobSpec

__all__ = [
    "EXPLANATION_TYPES",
    "DISCLAIMER",
    "FACTOR_GROUPS",
    "Analysis",
    "Prepared",
    "SWEEP_PARAMETERS",
    "analyze",
    "classify_point",
    "prepare",
    "validate_request",
]

EXPLANATION_TYPES = ("plan_performance", "recommendation_change", "sensitivity", "adopt_alternative")
FACTOR_GROUPS = ("data", "configuration", "constraints", "model")
SWEEP_PARAMETERS = ("risk_aversion", "risk_target", "turnover_limit", "max_weight", "min_weight")
DOMAIN = "finance"
DOMAIN_SCHEMA_VERSION = "1.0"
DISCLAIMER = "Attributions, re-solves and sweeps are modeled effects under the stated model and inputs, not real-world causes."
_ID = r"[0-7][0-9A-HJKMNP-TV-Z]{25}"
_PATTERNS = {
    "plan_version_id": re.compile(rf"^pv_{_ID}\Z"),
    "compare_to_plan_version_id": re.compile(rf"^pv_{_ID}\Z"),
    "publication_id": re.compile(rf"^pub_{_ID}\Z"),
    "plan_id": re.compile(rf"^pl_{_ID}\Z"),
    "realized_snapshot_id": re.compile(rf"^snap_{_ID}\Z"),
    "sweep_run_id": re.compile(rf"^run_{_ID}\Z"),
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
#: Model families whose differences are reported in the inventory only (design Non-Goals).
INVENTORY_ONLY_FAMILIES = ("qwen_swarm", "typesafe_jev")
#: Original-policy items checked for sweep points (interim EX-OQ-6: base configuration constraints).
POLICY_ITEMS = {"max_turnover": "turnover_limit", "max_weight": "max_weight", "min_weight": "min_weight", "max_risk": "risk_limit"}


# ============================================================================ data
@dataclass
class Prepared:
    explanation_type: str
    subject: dict[str, Any]
    request_ids: dict[str, Any]
    jobs: list[JobSpec] = field(default_factory=list)
    sync_evidence: list[EvidenceItem] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    #: adopt-alternative: the single confirmed plan-tool call.
    plan_call: dict[str, Any] | None = None
    findings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Analysis:
    checks: list[Check] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    mandatory: list[str] = field(default_factory=list)
    findings: dict[str, Any] = field(default_factory=dict)
    solution_status: str | None = None

    def label(self, *labels: str) -> None:
        for lb in labels:
            if lb not in self.labels:
                self.labels.append(lb)


# ============================================================================ request validation
def _id(req: Mapping[str, Any], key: str, *, required: bool) -> str | None:
    v = req.get(key)
    if v is None:
        if required:
            raise AgentError.validation(f"explanation.{key} is required", pointer=f"/explanation/{key}")
        return None
    if not isinstance(v, str) or not _PATTERNS[key].match(v):
        raise AgentError.validation(f"explanation.{key} is not a valid identifier", pointer=f"/explanation/{key}")
    return v


def _window(req: Mapping[str, Any], key: str, *, required: bool) -> dict[str, str] | None:
    w = req.get(key)
    if w is None:
        if required:
            raise AgentError.validation(f"explanation.{key} is required ({{start, end}} dates)", pointer=f"/explanation/{key}")
        return None
    if not isinstance(w, Mapping) or not all(isinstance(w.get(k), str) and _DATE.match(w[k]) for k in ("start", "end")) or w["start"] > w["end"]:
        raise AgentError.validation(f"explanation.{key} must be {{start, end}} ISO dates with start <= end", pointer=f"/explanation/{key}")
    return {"start": w["start"], "end": w["end"]}


def validate_request(req: Any, settings: ExplanationSettings) -> dict[str, Any]:
    if not isinstance(req, Mapping):
        raise AgentError.validation("explanation must be an object", pointer="/explanation")
    t = req.get("type")
    if t not in EXPLANATION_TYPES:
        raise AgentError.validation(f"explanation.type must be one of {', '.join(EXPLANATION_TYPES)}", pointer="/explanation/type", allowed=list(EXPLANATION_TYPES))
    out: dict[str, Any] = {"type": t}
    if t == "plan_performance":
        out["publication_id"] = _id(req, "publication_id", required=False)
        out["plan_id"] = _id(req, "plan_id", required=False)
        out["plan_version_id"] = _id(req, "plan_version_id", required=False)
        if not (out["publication_id"] or out["plan_id"] or out["plan_version_id"]):
            raise AgentError.validation("explanation needs publication_id (or plan_id / plan_version_id resolved to one publication)", pointer="/explanation/publication_id")
        out["window"] = _window(req, "window", required=True)
        out["realized_snapshot_id"] = _id(req, "realized_snapshot_id", required=True)
        src = req.get("actual_source")
        if src is not None and src not in ("paper", "simulated"):
            raise AgentError.not_permitted("actual results are paper or simulated execution records only", kind="live_actual_source")
        out["actual_source"] = src
    elif t == "recommendation_change":
        out["plan_version_id"] = _id(req, "plan_version_id", required=True)
        out["compare_to_plan_version_id"] = _id(req, "compare_to_plan_version_id", required=True)
        groups = req.get("groups", list(FACTOR_GROUPS))
        if not isinstance(groups, list) or not groups or any(g not in FACTOR_GROUPS for g in groups) or len(set(groups)) != len(groups):
            raise AgentError.validation(f"explanation.groups must be distinct values of {', '.join(FACTOR_GROUPS)}", pointer="/explanation/groups")
        out["groups"] = groups
        out["shapley"] = bool(req.get("shapley", False))
        out["evaluation_window"] = _window(req, "evaluation_window", required=False)
    elif t == "sensitivity":
        out["plan_version_id"] = _id(req, "plan_version_id", required=True)
        p = req.get("parameter")
        if not isinstance(p, Mapping) or p.get("name") not in SWEEP_PARAMETERS:
            raise AgentError.validation(f"explanation.parameter.name must be one of {', '.join(SWEEP_PARAMETERS)}", pointer="/explanation/parameter/name")
        values = p.get("values")
        if not isinstance(values, list) or not values or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values) or len(set(values)) != len(values):
            raise AgentError.validation("explanation.parameter.values must be a non-empty list of distinct numbers", pointer="/explanation/parameter/values")
        if len(values) > settings.max_sweep_points:
            raise AgentError.validation(f"the sweep has {len(values)} points; the maximum is {settings.max_sweep_points}", pointer="/explanation/parameter/values", max_sweep_points=settings.max_sweep_points, requested_points=len(values))
        inst = p.get("instrument")
        if inst is not None and (not isinstance(inst, str) or not re.match(r"^[A-Z0-9][A-Z0-9._-]{0,31}\Z", inst)):
            raise AgentError.validation("explanation.parameter.instrument is not an instrument_id", pointer="/explanation/parameter/instrument")
        out["parameter"] = {"name": p["name"], "values": list(values), **({"instrument": inst} if inst else {})}
        fixed = req.get("fixed_inputs", {})
        if not isinstance(fixed, Mapping):
            raise AgentError.validation("explanation.fixed_inputs must be an object", pointer="/explanation/fixed_inputs")
        out["fixed_inputs"] = dict(fixed)
        out["evaluation_window"] = _window(req, "evaluation_window", required=False)
    else:  # adopt_alternative
        out["plan_version_id"] = _id(req, "plan_version_id", required=True)
        out["sweep_run_id"] = _id(req, "sweep_run_id", required=True)
        idx = req.get("point_index")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise AgentError.validation("explanation.point_index must be a non-negative integer", pointer="/explanation/point_index")
        out["point_index"] = idx
    evidence_runs = req.get("evidence_run_ids") or {}
    if not isinstance(evidence_runs, Mapping) or any(not isinstance(v, str) or not _PATTERNS["sweep_run_id"].match(v) for v in evidence_runs.values()):
        raise AgentError.validation("explanation.evidence_run_ids maps evidence kinds to run_ids", pointer="/explanation/evidence_run_ids")
    out["evidence_run_ids"] = dict(evidence_runs)
    return out


def _plan_version(tools: GuardedTools, pv: str) -> dict[str, Any]:
    doc = tools.read("get_plan_version", {"plan_version_id": pv}) or {}
    v = doc.get("plan_version", doc) if isinstance(doc, Mapping) else {}
    if not isinstance(v, Mapping) or v.get("plan_version_id") != pv:
        raise AgentError.dependency("get_plan_version returned another version than requested", plan_version_id=pv)
    return dict(v)


def _submit_base(snapshot: str, window: Mapping[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": DOMAIN,
        "domain_schema_version": DOMAIN_SCHEMA_VERSION,
        "purpose": "research",
        "input_snapshot_id": snapshot,
        "evaluation_window": dict(window),
        "configuration": {"domain": DOMAIN, "domain_schema_version": DOMAIN_SCHEMA_VERSION, "payload": payload},
    }


def _job(kind: str, job_type: str, args: dict[str, Any], resolves: int, existing: Mapping[str, str]) -> JobSpec:
    return JobSpec(evidence_kind=kind, job_type=job_type, arguments=args, resolves=resolves, run_id=existing.get(kind))


# ============================================================================ workflow 1
def _prepare_performance(req: dict[str, Any], tools: GuardedTools) -> Prepared:
    pub_id = req.get("publication_id")
    resolved_from = None
    if not pub_id:
        plan_id = req.get("plan_id")
        pv_target = req.get("plan_version_id")
        if pv_target and not plan_id:
            plan_id = _plan_version(tools, pv_target).get("plan_id")
        pubs = (tools.read("list_publications", {"plan_id": plan_id}) or {}).get("publications") or []
        if pv_target:
            matching = [p for p in pubs if p.get("plan_version_id") == pv_target]
            if not matching:
                raise AgentError(
                    "PRECONDITION_FAILED",
                    f"plan version {pv_target} was never published; actual results are compared only against a published plan",
                    {"plan_version_id": pv_target, "available_publications": [{"publication_id": p.get("publication_id"), "plan_version_id": p.get("plan_version_id")} for p in pubs]},
                )
            pubs = matching
        if not pubs:
            raise AgentError("PRECONDITION_FAILED", "the plan has no publication to explain", {"plan_id": plan_id, "available_publications": []})
        pub_id = max(pubs, key=lambda p: str(p.get("published_at", "")))["publication_id"]
        resolved_from = "plan_version_id" if pv_target else "plan_id"
    pub = tools.read("get_publication", {"publication_id": pub_id}) or {}
    pv_id, checksum = pub.get("plan_version_id"), pub.get("plan_version_checksum")
    if pub.get("publication_id") != pub_id or not pv_id or not checksum:
        raise AgentError.dependency("get_publication returned no plan_version_id and checksum", publication_id=pub_id)
    version = _plan_version(tools, pv_id)
    if version.get("checksum") != checksum:
        raise AgentError("PRECONDITION_FAILED", "the plan version checksum differs from the checksum the publication recorded", {"publication_id": pub_id, "plan_version_id": pv_id})
    execs = (tools.read("list_executions", {"publication_id": pub_id}) or {}).get("executions") or []
    execs = [e for e in execs if e.get("mode") in ("paper", "simulated") and (req.get("actual_source") in (None, e.get("mode")))]
    if not execs:
        raise AgentError("PRECONDITION_FAILED", "the publication has no paper or simulated execution records for this source", {"publication_id": pub_id, "actual_source": req.get("actual_source")})
    sources = sorted({e["mode"] for e in execs})
    source = req.get("actual_source") or (sources[0] if len(sources) == 1 else None)
    if source is None:
        raise AgentError.validation("the publication has paper and simulated executions; state actual_source", pointer="/explanation/actual_source", available=sources)
    exec_ids = sorted(e["execution_id"] for e in execs if e.get("mode") == source)
    payload = {
        "evidence_kind": "performance_decomposition",
        "publication_id": pub_id,
        "plan_version_id": pv_id,
        "plan_version_checksum": checksum,
        "execution_ids": exec_ids,
        "actual_source": source,
        "plan_input_snapshot_id": version.get("input_snapshot_id"),
    }
    jobs = [_job("performance_decomposition", "performance_decomposition", _submit_base(req["realized_snapshot_id"], req["window"], payload), 0, req["evidence_run_ids"])]
    ids = {"publication_id": pub_id, "plan_version_id": pv_id, "plan_version_checksum": checksum, "execution_ids": exec_ids, "realized_snapshot_id": req["realized_snapshot_id"], "window": req["window"], "actual_source": source}
    if resolved_from:
        ids["resolved_from"] = resolved_from
    return Prepared("plan_performance", {"plan_version_id": pv_id}, ids, jobs=jobs, context={"anchor": ids})


def _analyze_performance(prep: Prepared, items: Mapping[str, EvidenceItem], settings: ExplanationSettings) -> Analysis:
    a = Analysis()
    ev = items["performance_decomposition"]
    p, key = ev.payload, ev.key
    anchor = prep.context["anchor"]
    ids = p.get("identifiers") or {}
    mismatch = [k for k in ("publication_id", "plan_version_id", "plan_version_checksum") if ids.get(k) != anchor.get(k)]
    if sorted(ids.get("execution_ids") or []) != anchor["execution_ids"]:
        mismatch.append("execution_ids")
    a.checks.append(Check("anchor_identifiers", FAILED if mismatch else PASSED, 0.0, None, key, f"evidence identifiers differ: {', '.join(mismatch)}" if mismatch else ""))
    rec = p.get("reconciliation") or {}
    tol_r = float(rec.get("tolerance", settings.default_tolerance))
    signs = {"start_value": 1, "cash_flows": 1, "realized_pnl": 1, "unrealized_pnl": 1, "fees": -1}
    for path in ("plan_path", "executed_path"):
        terms = rec.get(path) or {}
        a.checks.append(identity_check(f"reconciliation_{path}", lhs_terms=terms, signs=signs, total=terms.get("end_value"), tol=tol_r, evidence=key))
    gap = p.get("gap") or {}
    comps = gap.get("components") or {}
    unavailable = [c for c in gap.get("not_available") or [] if c in ("execution", "cost", "market_vs_forecast", "residual")]
    a.checks.append(
        sum_check(
            "gap_components_sum_to_total",
            components={k: comps.get(k) for k in ("execution", "cost", "market_vs_forecast", "residual")},
            total=gap.get("total"),
            tol=float(gap.get("tolerance", settings.default_tolerance)),
            evidence=key,
            skip=unavailable,
        )
    )
    fc = p.get("forecast") or {"status": "not_available"}
    if fc.get("status") == "available":
        pct = fc.get("percentile")
        lo, hi, rr = fc.get("lower"), fc.get("upper"), fc.get("realized_return")
        ok = isinstance(pct, (int, float)) and 0 <= pct <= 100 and all(isinstance(x, (int, float)) for x in (lo, hi, rr)) and lo <= hi
        inside = ok and lo <= rr <= hi
        if ok and fc.get("inside_interval") is not None and bool(fc["inside_interval"]) != inside:
            ok = False
        a.checks.append(Check("forecast_position_consistent", PASSED if ok else FAILED, 0.0, None, key))
        if ok:
            a.label("within_forecast_interval" if inside else "outside_forecast_interval")
    else:
        a.checks.append(Check("forecast_position_consistent", NA, 0.0, None, key, "no published forecast distribution"))
        a.label("forecast_not_available")
    if unavailable:
        a.label("components_not_available")
    ev.label("deterministic_evidence")
    # statements (verbatim evidence values)
    st = ev.statements
    st.append(f"Publication {anchor['publication_id']} anchors plan version {anchor['plan_version_id']} with checksum {anchor['plan_version_checksum']} [{key}].")
    w = p.get("window") or anchor["window"]
    st.append(
        f"The evaluation window is {w.get('start')} to {w.get('end')}, using {p.get('actual_source', anchor['actual_source'])} execution records and realized-data snapshot {ids.get('realized_snapshot_id', anchor['realized_snapshot_id'])} [{key}]."
    )
    for path, label in (("plan_path", "plan path"), ("executed_path", "executed path")):
        terms = rec.get(path) or {}
        if "end_value" in terms:
            st.append(f"The {label} reconciles from a starting value of {fmt(terms.get('start_value'))} to an ending value of {fmt(terms.get('end_value'))} within the declared tolerance [{key}].")
    if "total" in gap:
        parts = [f"{name} {fmt(comps[c])}" for c, name in (("execution", "execution gap"), ("cost", "cost gap"), ("market_vs_forecast", "market versus forecast"), ("residual", "residual")) if c in comps and c not in unavailable]
        st.append(f"The total gap between the plan path and the executed path is {fmt(gap['total'])}: " + ", ".join(parts) + f" [{key}].")
        for c in unavailable:
            st.append(f"The {c.replace('_', ' ')} component is not_available because the execution records do not hold it [{key}].")
    if fc.get("status") == "available" and "within_forecast_interval" in a.labels:
        st.append(
            f"The realized return {fmt(fc['realized_return'])} lies at percentile {fmt(fc['percentile'])} of the published forecast distribution, inside its {fmt(fc.get('level'))} interval from {fmt(fc['lower'])} to {fmt(fc['upper'])}; the outcome is within forecast uncertainty and is not attributed to model error [{key}]."
        )
    elif fc.get("status") == "available" and "outside_forecast_interval" in a.labels:
        st.append(f"The realized return {fmt(fc['realized_return'])} lies at percentile {fmt(fc['percentile'])}, outside the published {fmt(fc.get('level'))} interval from {fmt(fc['lower'])} to {fmt(fc['upper'])} [{key}].")
    else:
        st.append(f"Forecast uncertainty is not_available: the plan version carries no published forecast distribution [{key}].")
    dq = p.get("data_quality") or {}
    for flag in dq.get("flags") or []:
        st.append(f"Snapshot {flag.get('snapshot_id')} carries data-quality flag {flag.get('flag')}" + (f" for {flag['instrument']}" if flag.get("instrument") else "") + f" [{key}].")
    for r in dq.get("revisions") or []:
        base = f"The {r.get('instrument')} observation of {r.get('date')} was revised from {fmt(r.get('previous_value'))} to {fmt(r.get('new_value'))} between snapshots {r.get('previous_snapshot_id')} and {r.get('new_snapshot_id')}"
        if isinstance(r.get("modeled_effect"), (int, float)) and not isinstance(r.get("modeled_effect"), bool):
            st.append(f"{base}; its modeled effect is {fmt(r['modeled_effect'])} (modeled_effect) [{key}].")
            ev.label("modeled_effect")
        else:
            st.append(f"{base}; its effect was not quantified by a deterministic tool [{key}].")
    for d in p.get("excluded_days") or []:
        if d.get("reason") == "intraday_partial":
            msg = f"The observation of {d.get('date')} is intraday_partial and is excluded from the evaluation [{key}]."
            a.mandatory.append(msg)
            a.label("partial_period")
    a.findings["forecast_uncertainty"] = "not_available" if "forecast_not_available" in a.labels else "available"
    return a


# ============================================================================ workflow 2
def _prepare_change(req: dict[str, Any], tools: GuardedTools, settings: ExplanationSettings) -> Prepared:
    new, prev = req["plan_version_id"], req["compare_to_plan_version_id"]
    doc = tools.read("compare_plan_versions", {"plan_version_id": new, "compare_to_plan_version_id": prev})
    from .evidence import evidence_from_tool

    inv = evidence_from_tool("ev1", "compare_plan_versions", doc, expected_kind="difference_inventory")
    p = inv.payload
    pv_prev, pv_new = p.get("previous") or {}, p.get("new") or {}
    if pv_prev.get("plan_version_id") != prev or pv_new.get("plan_version_id") != new:
        raise AgentError.dependency("compare_plan_versions returned other versions than requested")
    if pv_prev.get("horizon") != pv_new.get("horizon"):
        aligned = p.get("common_horizon")
        if not aligned:
            raise AgentError(
                "PRECONDITION_FAILED",
                "the two recommendations do not share an effective horizon and no alignment is defined for this model",
                {"previous_horizon": pv_prev.get("horizon"), "new_horizon": pv_new.get("horizon")},
            )
        inv.label("aligned_horizon")
    prep = Prepared("recommendation_change", {"plan_version_id": new, "compare_to_plan_version_id": prev}, {"plan_version_id": new, "compare_to_plan_version_id": prev}, sync_evidence=[inv])
    diffs = [d for d in p.get("differences") or [] if isinstance(d, Mapping)]
    changed = [g for g in FACTOR_GROUPS if any(d.get("group") == g for d in diffs)]
    prep.context.update(changed_groups=changed, requested_groups=req["groups"])
    if not diffs:
        prep.labels.append("no_effect")
        inv.label("no_effect")
        return prep
    origin = pv_new.get("origin")
    if origin in ("manual_override", "excel_import"):
        inv.label(origin)
        prep.labels.append(origin)
        return prep  # the manual change is not re-solved
    groups = [g for g in changed if g in req["groups"]]
    if "model" in groups:
        fam = {pv_prev.get("model_family"), pv_new.get("model_family")}
        if "rl" in fam and pv_prev.get("model_spec_hash") != pv_new.get("model_spec_hash"):
            groups.remove("model")
            inv.label("requires_retraining")
            prep.labels.append("requires_retraining")
            prep.findings["experiment_proposal"] = {
                "kind": "retraining_experiment",
                "factor": "model",
                "reason": "the reward, state or action definition differs between the two RL model versions",
                "requires": ["human_approval", "budget"],
                "submitted": False,
            }
        elif fam & set(INVENTORY_ONLY_FAMILIES):
            groups.remove("model")
            inv.label("inventory_only_model")
            prep.labels.append("inventory_only_model")
    prep.context["resolve_groups"] = groups
    if not groups:
        return prep
    k = len(groups)
    if req["shapley"] and k > settings.max_shapley_groups:
        raise AgentError.validation(
            f"grouped Shapley over {k} groups needs {2**k} re-solves; the maximum is {settings.max_shapley_groups} groups ({2**settings.max_shapley_groups} re-solves)",
            pointer="/explanation/groups",
            max_shapley_groups=settings.max_shapley_groups,
            requested_groups=k,
            required_resolves=2**k,
        )
    window = req.get("evaluation_window") or pv_prev.get("evaluation_window")
    if not window:
        raise AgentError.validation("explanation.evaluation_window is required: the previous version records none", pointer="/explanation/evaluation_window")
    base = {"previous_plan_version_id": prev, "new_plan_version_id": new, "groups": groups, "lineage": pv_prev.get("lineage") or {}}
    prep.jobs.append(_job("controlled_resolve_set", "controlled_resolve", _submit_base(pv_prev["input_snapshot_id"], window, {"evidence_kind": "controlled_resolve_set", **base}), k + 2, req["evidence_run_ids"]))
    if req["shapley"]:
        prep.jobs.append(_job("grouped_shapley", "grouped_shapley", _submit_base(pv_prev["input_snapshot_id"], window, {"evidence_kind": "grouped_shapley", **base}), 2**k, req["evidence_run_ids"]))
    return prep


_QUANTITIES = ("objective", "expected_return", "risk")


def _analyze_change(prep: Prepared, items: Mapping[str, EvidenceItem], settings: ExplanationSettings) -> Analysis:
    a = Analysis()
    inv = prep.sync_evidence[0]
    p = inv.payload
    pv_prev, pv_new = p.get("previous") or {}, p.get("new") or {}
    st = inv.statements
    st.append(f"Previous version {pv_prev.get('plan_version_id')} (checksum {pv_prev.get('checksum')}, configuration {pv_prev.get('configuration_id')}, snapshot {pv_prev.get('input_snapshot_id')}) [{inv.key}].")
    st.append(f"New version {pv_new.get('plan_version_id')} (checksum {pv_new.get('checksum')}, configuration {pv_new.get('configuration_id')}, snapshot {pv_new.get('input_snapshot_id')}) [{inv.key}].")
    diffs = [d for d in p.get("differences") or [] if isinstance(d, Mapping)]
    if not diffs:
        same = pv_prev.get("checksum") == pv_new.get("checksum") and pv_prev.get("configuration_id") == pv_new.get("configuration_id") and pv_prev.get("input_snapshot_id") == pv_new.get("input_snapshot_id")
        a.checks.append(Check("no_effect_identical_lineage", PASSED if same else FAILED, 0.0, None, inv.key, "" if same else "an empty inventory requires identical configuration, snapshot and checksum"))
        a.label("no_effect")
        a.solution_status = "no_effect"
        st.append(
            f"Both versions share configuration {pv_new.get('configuration_id')}, snapshot {pv_new.get('input_snapshot_id')} and checksum {pv_new.get('checksum')}: the difference inventory is empty and the change has no effect (no_effect) [{inv.key}]."
        )
        return a
    for d in diffs:
        where = f" at {d['pointer']}" if d.get("pointer") else ""
        st.append(f"Difference in {d.get('group')}{where}: {fmt(d.get('previous'))} to {fmt(d.get('new'))} [{inv.key}].")
    if "manual_override" in inv.labels or "excel_import" in inv.labels:
        st.append(f"The new version has origin {pv_new.get('origin')}; the manual change is reported from the inventory and is not re-solved [{inv.key}].")
        a.label(pv_new.get("origin"))
    if "requires_retraining" in inv.labels:
        st.append(f"The model factor requires_retraining: the RL reward, state or action definition changed, so it is not attributed by re-solving and no training job was submitted [{inv.key}].")
        a.label("requires_retraining")
    if "inventory_only_model" in inv.labels:
        st.append(f"The model factor is reported from the inventory only; its strategy family is not re-solved in an explanation [{inv.key}].")
    a.checks.append(Check("difference_inventory_present", PASSED, 0.0, None, inv.key))
    cr = items.get("controlled_resolve_set")
    if cr is not None:
        _analyze_resolves(cr, pv_prev, pv_new, a, settings)
    sh = items.get("grouped_shapley")
    if sh is not None:
        _analyze_shapley(sh, cr, a, settings)
    return a


def _run(runs: list[Mapping[str, Any]], label: str) -> Mapping[str, Any] | None:
    return next((r for r in runs if r.get("label") == label), None)


def _analyze_resolves(cr: EvidenceItem, pv_prev: Mapping[str, Any], pv_new: Mapping[str, Any], a: Analysis, settings: ExplanationSettings) -> None:
    p, key = cr.payload, cr.key
    tol = float(p.get("tolerance", settings.default_tolerance))
    runs = [r for r in p.get("runs") or [] if isinstance(r, Mapping)]
    groups = list(p.get("groups") or [])
    base, allr = _run(runs, "base"), _run(runs, "all")
    prev_out, new_out = p.get("previous") or {}, p.get("new") or {}
    ok, worst = allocation_match((base or {}).get("allocation"), prev_out.get("allocation"), tol)
    ok = ok and close((base or {}).get("objective"), prev_out.get("objective"), tol)
    a.checks.append(Check("base_reproduction", PASSED if ok else FAILED, tol, worst, key, "" if ok else "re-solving the previous inputs does not reproduce the previous version"))
    ok, worst = allocation_match((allr or {}).get("allocation"), new_out.get("allocation"), tol)
    ok = ok and close((allr or {}).get("objective"), new_out.get("objective"), tol)
    a.checks.append(Check("all_switched_reproduction", PASSED if ok else FAILED, tol, worst, key, "" if ok else "switching every group does not reproduce the new version"))
    effects = p.get("effects") or {}
    total = p.get("total") or {}
    infeasible = []
    for g in groups:
        r = _run(runs, f"switch:{g}")
        if r is None:
            a.checks.append(Check(f"single_switch_{g}_present", FAILED, tol, None, key, "single-switch re-solve missing"))
            continue
        if r.get("solution_status") == "infeasible":
            infeasible.append(g)
            continue
        a.checks.append(weights_sum_check(f"weights_sum_switch_{g}", r.get("allocation"), tol=tol, evidence=key))
        eff = effects.get(g) or {}
        for q in _QUANTITIES:
            if q in eff and base is not None:
                a.checks.append(identity_check(f"effect_{g}_{q}", lhs_terms={"run": r.get(q), "base": base.get(q)}, signs={"run": 1, "base": -1}, total=eff.get(q), tol=tol, evidence=key))
    for q in _QUANTITIES:
        if q not in total:
            continue
        if infeasible:
            a.checks.append(Check(f"decomposition_sum_{q}", NA, tol, None, key, "an infeasible single switch has no effect to sum"))
            continue
        comps = {g: (effects.get(g) or {}).get(q) for g in groups}
        comps["interaction_remainder"] = (p.get("interaction_remainder") or {}).get(q)
        a.checks.append(sum_check(f"decomposition_sum_{q}", components=comps, total=total.get(q), tol=tol, evidence=key))
    cr.label("modeled_effect")
    a.label("modeled_effect")
    st = cr.statements
    for g in groups:
        if g in infeasible:
            st.append(f"The single-switch re-solve for {g} is infeasible (solution_status infeasible, completion_status succeeded) [{key}].")
            a.label(f"infeasible_switch_{g}")
            continue
        eff = effects.get(g) or {}
        parts = [f"{q.replace('_', ' ')} by {fmt(eff[q])}" for q in _QUANTITIES if q in eff]
        if parts:
            st.append(f"Under the stated model, switching only {g} changes the " + ", ".join(parts) + f" (modeled_effect) [{key}].")
        w = eff.get("weights") or {}
        if w:
            st.append(f"Per-asset weight changes from switching {g}: " + ", ".join(f"{i} {fmt(v)}" for i, v in sorted(w.items())) + f" [{key}].")
    if "objective" in total:
        st.append(
            f"The total modeled change in the objective is {fmt(total['objective'])}"
            + (f", with an interaction remainder of {fmt((p.get('interaction_remainder') or {}).get('objective'))}" if (p.get("interaction_remainder") or {}).get("objective") is not None else "")
            + f" [{key}]."
        )
    lineage = p.get("lineage") or {}
    if not lineage or not lineage.get("solver"):
        a.label("lineage_unavailable")
        a.mandatory.append(f"The original solver settings were not recorded in lineage, so the re-solves may not use them [{key}].")
    if lineage.get("deterministic") is False:
        a.label("uncertain_attribution")
        spread = p.get("spread") or {}
        seeds = lineage.get("seeds") or []
        detail = "; ".join(f"{g} objective spread {fmt((spread.get(g) or {}).get('objective'))}" for g in groups if (spread.get(g) or {}).get("objective") is not None)
        a.checks.append(Check("seed_spread_reported", PASSED if detail else FAILED, tol, None, key, "" if detail else "a stochastic model must report the run-to-run spread"))
        a.mandatory.append(f"The model is stochastic: effects are means over seeds {', '.join(str(s) for s in seeds)}" + (f" ({detail})" if detail else "") + f", so the attribution is uncertain [{key}].")


def _analyze_shapley(sh: EvidenceItem, cr: EvidenceItem | None, a: Analysis, settings: ExplanationSettings) -> None:
    p, key = sh.payload, sh.key
    tol = float(p.get("tolerance", settings.default_tolerance))
    groups = list(p.get("groups") or [])
    k = len(groups)
    a.checks.append(Check("shapley_group_limit", PASSED if k <= settings.max_shapley_groups else FAILED, 0.0, None, key))
    a.checks.append(Check("shapley_resolve_count", PASSED if p.get("resolves") == 2**k else FAILED, 0.0, None, key, "" if p.get("resolves") == 2**k else "exact grouped Shapley needs 2^k re-solves"))
    phi = p.get("phi") or {}
    total = p.get("total") or {}
    for q in _QUANTITIES:
        if q in total:
            a.checks.append(sum_check(f"shapley_efficiency_{q}", components={g: (phi.get(g) or {}).get(q) for g in groups}, total=total.get(q), tol=tol, evidence=key))
    if cr is not None and "objective" in total:
        a.checks.append(Check("shapley_total_matches_resolves", PASSED if close(total.get("objective"), (cr.payload.get("total") or {}).get("objective"), tol) else FAILED, tol, None, key))
    sh.label("modeled_effect")
    sh.statements.append(f"Exact grouped Shapley attribution over {k} groups uses {fmt(p.get('resolves'))} re-solves and is ordering-invariant [{key}].")
    for g in groups:
        if "objective" in (phi.get(g) or {}):
            sh.statements.append(f"Grouped Shapley assigns {g} {fmt(phi[g]['objective'])} of the modeled objective change (modeled_effect) [{key}].")


# ============================================================================ workflow 3
def _policy(version: Mapping[str, Any]) -> dict[str, float]:
    cons = ((version.get("content") or {}).get("constraints")) or {}
    return {k: float(cons[k]) for k in POLICY_ITEMS if isinstance(cons.get(k), (int, float)) and not isinstance(cons.get(k), bool)}


def _prepare_sensitivity(req: dict[str, Any], tools: GuardedTools) -> Prepared:
    pv = req["plan_version_id"]
    version = _plan_version(tools, pv)
    window = req.get("evaluation_window") or version.get("evaluation_window")
    if not window:
        raise AgentError.validation("explanation.evaluation_window is required: the base version records none", pointer="/explanation/evaluation_window")
    policy = _policy(version)
    payload = {"evidence_kind": "sensitivity_sweep", "base_plan_version_id": pv, "parameter": req["parameter"], "fixed_inputs": req["fixed_inputs"]}
    jobs = [_job("sensitivity_sweep", "sensitivity_sweep", _submit_base(version["input_snapshot_id"], window, payload), len(req["parameter"]["values"]), req["evidence_run_ids"])]
    prep = Prepared("sensitivity", {"plan_version_id": pv}, {"plan_version_id": pv, "configuration_id": version.get("configuration_id"), "parameter": req["parameter"]}, jobs=jobs)
    prep.context.update(policy=policy, policy_source="base_configuration_constraints", parameter=req["parameter"])
    return prep


def classify_point(point: Mapping[str, Any], policy: Mapping[str, float], tol: float) -> dict[str, Any]:
    """``permitted`` / ``concession`` / ``infeasible`` against the original policy (E5). Each
    concession item carries the original and the required value, both read from inputs."""
    if point.get("solution_status") == "infeasible":
        return {"classification": "infeasible", "concessions": []}
    weights = {k: float(v) for k, v in (point.get("allocation") or {}).items()}
    metrics = point.get("metrics") or {}
    required: dict[str, Any] = {}
    if "max_turnover" in policy and isinstance(metrics.get("turnover"), (int, float)):
        required["max_turnover"] = metrics["turnover"]
    if "max_risk" in policy and isinstance(metrics.get("risk"), (int, float)):
        required["max_risk"] = metrics["risk"]
    if weights and "max_weight" in policy:
        required["max_weight"] = max(weights.values())
    if weights and "min_weight" in policy:
        required["min_weight"] = min(weights.values())
    concessions = []
    for k, original in policy.items():
        if k not in required:
            continue
        req_v = float(required[k])
        violated = req_v < original - tol if k == "min_weight" else req_v > original + tol
        if violated:
            concessions.append({"item": POLICY_ITEMS[k], "original": original, "required": required[k]})
    return {"classification": "concession" if concessions else "permitted", "concessions": concessions}


def _analyze_sensitivity(prep: Prepared, items: Mapping[str, EvidenceItem], settings: ExplanationSettings) -> Analysis:
    from .claims import is_monotonic

    a = Analysis()
    ev = items["sensitivity_sweep"]
    p, key = ev.payload, ev.key
    tol = float(p.get("tolerance", settings.default_tolerance))
    param = prep.context["parameter"]
    points = [x for x in p.get("points") or [] if isinstance(x, Mapping)]
    values = [x.get("value") for x in points]
    complete = values == param["values"]
    a.checks.append(Check("sweep_points_complete", PASSED if complete else FAILED, 0.0, None, key, "" if complete else "every requested point must be reported in order (none dropped)"))
    if p.get("base_plan_version_id") != prep.subject["plan_version_id"]:
        a.checks.append(Check("sweep_base_version", FAILED, 0.0, None, key, "the sweep was computed from another base version"))
    base_alloc = (p.get("base") or {}).get("allocation") or {}
    policy = prep.context.get("policy") or {}
    classified = []
    for i, pt in enumerate(points):
        status = pt.get("solution_status")
        if pt.get("completion_status", "succeeded") != "succeeded":
            a.checks.append(Check(f"point_{i}_completed", FAILED, 0.0, None, key, "a sweep point did not complete"))
        if status != "infeasible":
            a.checks.append(weights_sum_check(f"point_{i}_weights_sum", pt.get("allocation"), tol=tol, evidence=key))
            a.checks.append(allocation_delta_check(f"point_{i}_portfolio_wide_change", base=base_alloc, point=pt.get("allocation") or {}, reported_change=pt.get("allocation_change"), tol=tol, evidence=key))
            c = constraint_check(f"point_{i}_constraints", pt.get("constraint_slack"), tol=tol, evidence=key)
            if c.status != NA:
                a.checks.append(c)
            same, _ = allocation_match(base_alloc, pt.get("allocation"), tol)
            if status == "no_effect" and not same:
                a.checks.append(Check(f"point_{i}_no_effect_consistent", FAILED, tol, None, key, "reported no_effect but the allocation differs from the base"))
            effective = "no_effect" if same else status
        else:
            effective = "infeasible"
        cls = classify_point(pt, policy, tol)
        classified.append({"index": i, "value": pt.get("value"), "solution_status": effective, "completion_status": pt.get("completion_status", "succeeded"), **cls})
    ev.label("modeled_effect")
    a.label("modeled_effect")
    if any(c["solution_status"] == "no_effect" for c in classified):
        ev.label("no_effect")
    if any(c["classification"] == "concession" for c in classified):
        a.label("concession")
    a.findings["points"] = classified
    a.findings["policy"] = {"items": {POLICY_ITEMS[k]: v for k, v in policy.items()}, "source": prep.context.get("policy_source")}
    name = param["name"] + (f" for {param['instrument']}" if param.get("instrument") else "")
    st = ev.statements
    for c, pt in zip(classified, points, strict=False):
        m = pt.get("metrics") or {}
        head = f"At {name} {fmt(c['value'])}: solution_status {c['solution_status']}"
        if c["solution_status"] == "infeasible":
            st.append(f"{head}, completion_status succeeded; the point is kept as infeasible [{key}].")
            continue
        mparts = [f"{lbl} {fmt(m[q])}" for q, lbl in (("expected_return", "expected return"), ("risk", "risk"), ("turnover", "turnover"), ("cost_estimate", "cost estimate")) if q in m]
        cls = c["classification"]
        if cls == "concession":
            cls += " (" + "; ".join(f"{x['item']} original {fmt(x['original'])}, required {fmt(x['required'])}" for x in c["concessions"]) + ")"
        st.append(f"{head}; " + ", ".join(mparts) + f"; classified {cls} [{key}].")
        ch = pt.get("allocation_change") or {}
        if ch and c["solution_status"] != "no_effect":
            st.append(f"Allocation changes versus the base at {name} {fmt(c['value'])}: " + ", ".join(f"{h} {fmt(v)}" for h, v in sorted(ch.items())) + f" [{key}].")
    runs: list[list[Any]] = []
    for c in classified:
        if c["solution_status"] == "no_effect":
            if runs and runs[-1][-1]["index"] == c["index"] - 1:
                runs[-1].append(c)
            else:
                runs.append([c])
    for r in runs:
        if len(r) > 1:
            st.append(f"The recommendation is insensitive under the model for {name} from {fmt(r[0]['value'])} to {fmt(r[-1]['value'])}: each of these points is no_effect [{key}].")
    feasible = [pt for pt, c in zip(points, classified, strict=False) if c["solution_status"] != "infeasible"]
    for q, lbl in (("risk", "risk"), ("expected_return", "expected return"), ("turnover", "turnover")):
        series = [float(pt["metrics"][q]) for pt in feasible if isinstance((pt.get("metrics") or {}).get(q), (int, float))]
        if len(series) >= 3 and not is_monotonic(series):
            st.append(f"Computed {lbl} is non-monotonic across the swept values; no trend is assumed between points [{key}].")
    return a


# ============================================================================ adopt alternative
def _prepare_adopt(req: dict[str, Any], tools: GuardedTools, settings: ExplanationSettings, session_id: str, turn: int) -> Prepared:
    from ..core.ids import idempotency_key
    from .evidence import evidence_from_job_result

    pv = req["plan_version_id"]
    version = _plan_version(tools, pv)
    plan = tools.read("get_plan", {"plan_id": version.get("plan_id")}) or {}
    sweep = evidence_from_job_result("ev1", tools.read("get_experiment_result", {"run_id": req["sweep_run_id"]}), expected_kind="sensitivity_sweep")
    if sweep.payload.get("base_plan_version_id") != pv:
        raise AgentError("PRECONDITION_FAILED", "the sweep was computed from another base version", {"plan_version_id": pv, "sweep_run_id": req["sweep_run_id"]})
    points = sweep.payload.get("points") or []
    if req["point_index"] >= len(points):
        raise AgentError.validation("point_index is outside the sweep", pointer="/explanation/point_index", points=len(points))
    point = points[req["point_index"]]
    tol = float(sweep.payload.get("tolerance", settings.default_tolerance))
    policy = _policy(version)
    cls = classify_point(point, policy, tol)
    if cls["classification"] == "infeasible":
        raise AgentError("PRECONDITION_FAILED", "an infeasible sweep point cannot be adopted", {"point_index": req["point_index"]})
    content = dict(version.get("content") or {})
    # Only the allocation changes; the original constraints (the stored policy) are carried unchanged.
    content["allocation"] = {"weights": [{"instrument_id": i, "weight": w} for i, w in sorted((point.get("allocation") or {}).items())]}
    param = sweep.payload.get("parameter") or {}
    args = {
        "plan_id": version.get("plan_id"),
        "parent_plan_version_id": pv,
        "expected_revision": plan.get("revision"),
        "domain": DOMAIN,
        "domain_schema_version": DOMAIN_SCHEMA_VERSION,
        "content": content,
        "reason": f"Adopt sensitivity point {req['point_index']} ({param.get('name')} {point.get('value')}) from {req['sweep_run_id']}; classified {cls['classification']}.",
    }
    args["idempotency_key"] = idempotency_key(session_id, turn, "create_override_version", args)
    prep = Prepared("adopt_alternative", {"plan_version_id": pv}, {"plan_version_id": pv, "sweep_run_id": req["sweep_run_id"], "point_index": req["point_index"]}, sync_evidence=[sweep])
    prep.plan_call = {"tool": "create_override_version", "arguments": args, "idempotency_key": args["idempotency_key"]}
    prep.findings.update(classification=cls["classification"], concessions=cls["concessions"], policy_unchanged=True)
    return prep


# ============================================================================ dispatch
def prepare(req: dict[str, Any], tools: GuardedTools, settings: ExplanationSettings, *, session_id: str, turn: int) -> Prepared:
    t = req["type"]
    if t == "plan_performance":
        return _prepare_performance(req, tools)
    if t == "recommendation_change":
        return _prepare_change(req, tools, settings)
    if t == "sensitivity":
        return _prepare_sensitivity(req, tools)
    return _prepare_adopt(req, tools, settings, session_id, turn)


def analyze(prep: Prepared, items: Mapping[str, EvidenceItem], settings: ExplanationSettings) -> Analysis:
    if prep.explanation_type == "plan_performance":
        a = _analyze_performance(prep, items, settings)
    elif prep.explanation_type == "recommendation_change":
        a = _analyze_change(prep, items, settings)
    else:
        a = _analyze_sensitivity(prep, items, settings)
    for lb in prep.labels:
        a.label(lb)
    a.findings.update(prep.findings)
    if "modeled_effect" in a.labels:
        a.mandatory.insert(0, DISCLAIMER)
    return a
