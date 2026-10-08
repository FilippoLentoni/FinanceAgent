"""Unit tests of the explanation evidence pipeline pieces (FA-EV-01, FA-EV-03, FA-EV-04, FA-EV-05,
FA-EV-08, FA-WF3-03, FA-WF3-06; tasks 1.1-1.7)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from finplan_agent.core.errors import AgentError
from finplan_agent.explanations import checks as ck
from finplan_agent.explanations.claims import check_explanation_narrative, is_monotonic
from finplan_agent.explanations.config import ExplanationSettings, load_explanation_settings
from finplan_agent.explanations.evidence import EvidenceItem, evidence_from_job_result, reproduced, reproduction_arguments
from finplan_agent.explanations.result import assemble, validate_result
from finplan_agent.explanations.tools import GuardedTools
from finplan_agent.explanations.workflows import classify_point
from finplan_agent.graph.claim_check import REMOVED
from tests.fakes.explanations import ExplanationBackend, catalog, checksum, evidence_ref, sweep_evidence, uid

ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "tests" / "fixtures" / "explanations" / "draft-schemas"


def item(payload, key="ev1", labels=(), statements=()):
    return EvidenceItem(key=key, kind=payload.get("evidence_kind", "sensitivity_sweep"), ref=evidence_ref(payload, int(key[2:])), payload=payload, source_tool="get_experiment_result", labels=list(labels), statements=list(statements))


# ============================================================================ FA-EV-04 property tests
@pytest.mark.parametrize("seed", range(25))
def test_sum_check_property_over_random_decompositions(seed):
    rnd = random.Random(seed)
    comps = {f"c{i}": round(rnd.uniform(-1, 1), 6) for i in range(rnd.randint(1, 6))}
    total = sum(comps.values())
    assert ck.sum_check("s", components=comps, total=total, tol=1e-9).status == "passed"
    off = total + rnd.choice([-1, 1]) * rnd.uniform(1e-3, 1)
    bad = ck.sum_check("s", components=comps, total=off, tol=1e-6)
    assert bad.status == "failed" and bad.observed > 1e-6


@pytest.mark.parametrize("seed", range(25))
def test_weights_and_allocation_delta_properties(seed):
    rnd = random.Random(1000 + seed)
    raw = [rnd.random() for _ in range(rnd.randint(2, 7))]
    base = {f"I{i}": x / sum(raw) for i, x in enumerate(raw)}
    raw2 = [rnd.random() for _ in base]
    point = {k: x / sum(raw2) for k, x in zip(base, raw2, strict=True)}
    assert ck.weights_sum_check("w", base, tol=1e-9).status == "passed"
    change = {k: point[k] - base[k] for k in base}
    assert ck.allocation_delta_check("d", base=base, point=point, reported_change=change, tol=1e-9).status == "passed"
    partial = dict(list(change.items())[:1])
    res = ck.allocation_delta_check("d", base=base, point=point, reported_change=partial, tol=1e-9)
    assert res.status == "failed" and res.extra["missing_holdings"]


def test_identity_check_reconciliation_and_missing_terms():
    terms = {"start_value": 100.0, "cash_flows": 5.0, "realized_pnl": 3.0, "unrealized_pnl": -1.0, "fees": 0.5, "end_value": 106.5}
    signs = {"start_value": 1, "cash_flows": 1, "realized_pnl": 1, "unrealized_pnl": 1, "fees": -1}
    assert ck.identity_check("r", lhs_terms=terms, signs=signs, total=106.5, tol=1e-9).status == "passed"
    assert ck.identity_check("r", lhs_terms=terms, signs=signs, total=107.0, tol=0.01).status == "failed"
    assert "missing" in ck.identity_check("r", lhs_terms={**terms, "fees": None}, signs=signs, total=106.5, tol=1).detail
    assert ck.constraint_check("c", {"a": 0.0, "b": -1e-3}, tol=1e-6).status == "failed"
    assert ck.constraint_check("c", None, tol=1e-6).status == "not_applicable"


# ============================================================================ FA-EV-01 / FA-EV-05 / FA-WF3-06 claim check
def test_planted_provider_arithmetic_is_removed():
    ev = item({"evidence_kind": "controlled_resolve_set", "effects": {"data": {"objective": 0.03}, "configuration": {"objective": -0.01}}}, statements=["s [ev1]."])
    res = check_explanation_narrative("Data adds 0.03 and configuration -0.01, so together 0.02 [ev1].", [ev])
    assert "0.02" in res.removed_figures and REMOVED in res.text and "0.03" in res.text and not res.passed


def test_figures_without_citation_are_removed_and_citations_must_exist():
    ev = item({"evidence_kind": "x", "v": 0.5}, statements=["v is 0.5 [ev1]."])
    res = check_explanation_narrative("The value is 0.5. It is also 0.5 [ev1]. See [ev7].", [ev])
    assert res.removed_figures == ["0.5"] and res.unknown_citations == ["ev7"] and res.citations == ["ev1"]


def test_causal_wording_is_rewritten_as_modeled_effect():
    ev = item({"evidence_kind": "x", "v": 0.5}, statements=["v [ev1]."])
    res = check_explanation_narrative("The snapshot change caused 0.5 of the shift [ev1]. Returns fell because the market dropped [ev1].", [ev])
    assert set(res.causal_rewrites) == {"because the market", "caused"}
    assert "caused" not in res.text and "is modeled to account for 0.5" in res.text and "while the market" in res.text


def test_monotonic_claim_against_non_monotonic_sweep_is_rejected():
    ev = item(sweep_evidence([0.05, 0.1, 0.15, 0.2], risks=[0.12, 0.14, 0.13, 0.15]), statements=["sweep [ev1]."])
    res = check_explanation_narrative("Risk rises monotonically with the turnover limit [ev1]. Risk is non-monotonic across the sweep [ev1].", [ev])
    assert [r["rule"] for r in res.rejected_sentences] == ["unsupported_trend"] and "non-monotonic" in res.text
    mono = item(sweep_evidence([0.05, 0.1, 0.15]), statements=["sweep [ev1]."])
    ok = check_explanation_narrative("Risk rises steadily across the computed points [ev1].", [mono])
    assert ok.passed and is_monotonic([1, 2, 2, 3]) and not is_monotonic([1, 3, 2])


def test_no_effect_claim_needs_a_no_effect_label():
    ev = item({"evidence_kind": "x", "v": 1}, statements=["s [ev1]."])
    res = check_explanation_narrative("The change had no effect [ev1].", [ev])
    assert res.rejected_sentences[0]["rule"] == "unsupported_no_effect" and res.replaced_with_statements
    labeled = item({"evidence_kind": "x", "v": 1}, labels=["no_effect"], statements=["s [ev1]."])
    assert check_explanation_narrative("The change had no effect [ev1].", [labeled]).passed


def test_mandatory_statements_are_appended_and_all_digit_checksums_are_not_figures():
    chk = "sha256:" + "1" * 64
    ev = item({"evidence_kind": "x", "checksum": chk}, statements=[f"checksum {chk} [ev1]."])
    res = check_explanation_narrative(f"Both share checksum {chk} [ev1].", [ev], mandatory=["Modeled effects, not causes."])
    assert res.passed is True and chk in res.text and res.text.endswith("Modeled effects, not causes.")


# ============================================================================ FA-WF3-03
def test_point_classification_lists_every_relaxed_item():
    policy = {"max_turnover": 0.2, "max_weight": 0.5, "min_weight": 0.05}
    point = {"solution_status": "optimal", "allocation": {"A": 0.6, "B": 0.38, "C": 0.02}, "metrics": {"turnover": 0.3}}
    cls = classify_point(point, policy, 1e-9)
    assert cls["classification"] == "concession"
    assert cls["concessions"] == [{"item": "turnover_limit", "original": 0.2, "required": 0.3}, {"item": "max_weight", "original": 0.5, "required": 0.6}, {"item": "min_weight", "original": 0.05, "required": 0.02}]
    ok = classify_point({"solution_status": "optimal", "allocation": {"A": 0.5, "B": 0.5}, "metrics": {"turnover": 0.1}}, policy, 1e-9)
    assert ok == {"classification": "permitted", "concessions": []}
    assert classify_point({"solution_status": "infeasible"}, policy, 1e-9)["classification"] == "infeasible"


# ============================================================================ FA-EV-08 / task 1.7
def test_guarded_tools_refuse_unconfirmed_state_changes_but_allow_dry_runs():
    b = ExplanationBackend()
    g = GuardedTools(b, catalog())
    with pytest.raises(AgentError) as exc:
        g.call("create_override_version", {"plan_id": "x", "idempotency_key": "k1"})
    assert exc.value.code == "OPERATION_NOT_PERMITTED" and exc.value.details["kind"] == "unconfirmed_state_change" and b.calls == []
    with pytest.raises(AgentError):
        g.call("submit_experiment", {"dry_run": False, "idempotency_key": "k2"})
    assert g.requires_confirmation("submit_experiment", {"dry_run": True}) is False
    approved = GuardedTools(b, catalog(), approved_keys=["k1"])
    assert approved.call("create_override_version", {"plan_id": "x", "parent_plan_version_id": "p", "idempotency_key": "k1"}).ok
    with pytest.raises(AgentError) as live:
        g.call("get_plan", {"mode": "live"})
    assert live.value.code == "OPERATION_NOT_PERMITTED"


def test_guarded_tools_bound_the_number_of_calls():
    g = GuardedTools(ExplanationBackend(), catalog(), max_calls=2)
    g.call("get_plan", {"plan_id": "p"})
    g.call("get_plan", {"plan_id": "p"})
    with pytest.raises(AgentError) as exc:
        g.call("get_plan", {"plan_id": "p"})
    assert exc.value.code == "BUDGET_EXCEEDED"


def test_execution_read_tools_are_only_allowed_when_cataloged_read_only():
    from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog

    assert catalog().is_denied("list_executions") is False
    assert ToolCatalog([CatalogEntry("list_executions", True, "plan-writer")]).is_denied("list_executions") is True
    assert ToolCatalog([]).is_denied("list_executions") is True and catalog().is_denied("create_execution") is True


# ============================================================================ job results (CS-07)
def test_job_result_needs_succeeded_completion_and_a_trusted_reference():
    doc = sweep_evidence([1.0])
    good = {"run_id": uid("run", 1), "completion_status": "succeeded", "solution_status": "infeasible", "artifacts": [evidence_ref(doc, 2)], "artifacts_complete": True, "payload": {"evidence": doc}}
    it = evidence_from_job_result("ev2", good, expected_kind="sensitivity_sweep")
    assert it.payload["solution_status"] == "infeasible" and it.ref["checksum"] == checksum(doc)
    for bad in (
        {**good, "completion_status": "failed", "error": {"code": "INTERNAL"}},
        {**good, "artifacts": [{**evidence_ref(doc, 2), "checksum": "md5:x"}]},
        {**good, "artifacts": [{**evidence_ref(doc, 2), "kind": "run_artifact"}]},
        {**good, "artifacts_complete": False},
    ):
        with pytest.raises(AgentError) as exc:
            evidence_from_job_result("ev2", bad, expected_kind="sensitivity_sweep")
        assert exc.value.code == "DEPENDENCY_UNAVAILABLE"
    with pytest.raises(AgentError):
        evidence_from_job_result("ev2", good, expected_kind="grouped_shapley")


# ============================================================================ FA-EV-03 (offline half; deployed half BLOCKED by EX-OQ-1)
def test_reproduction_arguments_and_checksum_comparison():
    doc = {
        **sweep_evidence([1.0]),
        "recorded_request": {
            "job_type": "sensitivity_sweep",
            "input_snapshot_id": uid("snap", 1),
            "evaluation_window": {"start": "2026-01-02", "end": "2026-03-31"},
            "configuration": {"domain": "finance", "domain_schema_version": "1.0", "payload": {"evidence_kind": "sensitivity_sweep"}},
        },
        "seeds": [7],
    }
    original = item(doc)
    args = reproduction_arguments(original)
    assert args["job_type"] == "sensitivity_sweep" and args["configuration"]["payload"]["reproduce"]["seeds"] == [7] and "idempotency_key" not in args
    assert reproduced(original, item(json.loads(json.dumps(doc))))
    assert not reproduced(original, item({**doc, "tolerance": 1e-3}))
    with pytest.raises(AgentError) as exc:
        reproduction_arguments(item(sweep_evidence([1.0])))
    assert "recorded_request" not in exc.value.details["missing"] and "job_type" in exc.value.details["missing"]


# ============================================================================ FA-EV-02 assembler (contract envelope)
def test_assembler_output_conforms_to_the_contract_envelope_and_never_has_an_empty_narrative():
    ev = item(sweep_evidence([1.0]), statements=["a [ev1]."])
    for status in ("generated", "unavailable", "budget_exceeded"):
        doc = assemble(
            explanation_type="sensitivity",
            subject={"plan_version_id": uid("pv", 1)},
            request_ids={},
            items=[ev],
            checks=[],
            labels=["modeled_effect"],
            narrative="a [ev1]." if status == "generated" else "",
            narrative_status=status,
            citations=["ev1"],
        )
        validate_result(doc)
        assert doc["narrative"] and doc["narrative_status"] == status and doc["evidence"][0]["kind"] == "explanation_evidence"
        assert (doc["citations"] != []) == (status == "generated")
    with pytest.raises(AgentError):
        assemble(explanation_type="sensitivity", subject={"plan_version_id": uid("pv", 1)}, request_ids={}, items=[], checks=[], labels=[], narrative="x", narrative_status="generated")


def test_contract_rejects_a_result_without_evidence_checksum():
    from finplan_contracts.validate import validate

    ev = item(sweep_evidence([1.0]), statements=["a [ev1]."])
    doc = assemble(explanation_type="sensitivity", subject={"plan_version_id": uid("pv", 1)}, request_ids={}, items=[ev], checks=[], labels=[], narrative="a [ev1].", narrative_status="generated")
    broken = json.loads(json.dumps(doc))
    del broken["evidence"][0]["checksum"]
    assert not validate(broken, "explanation-result").valid


# ============================================================================ configuration
def test_explanation_settings_validation_and_ssm_override():
    class Ssm:
        def __init__(self, value):
            self.value = value

        def get_parameter(self, Name):  # noqa: N803
            if self.value is None:
                raise KeyError(Name)
            return {"Parameter": {"Value": self.value}}

    base = {"explanations": {"enabled": True, "max_sweep_points": 11}}
    assert load_explanation_settings(base, Ssm('{"max_sweep_points": 5}'), "/p").max_sweep_points == 5
    assert load_explanation_settings(base, Ssm(None), "/p").max_sweep_points == 11
    for bad in ('{"max_sweep_points": 0}', '{"max_estimated_usd_per_request": -1}', "[1]", "not json", '{"enabled": "yes"}'):
        with pytest.raises(AgentError):
            load_explanation_settings(base, Ssm(bad), "/p")
    assert ExplanationSettings(narration_max_tokens_invocation=600).narration_cap(512) == 512


def test_no_model_identifier_or_price_in_explanation_config():
    from finplan_agent.config.settings import load_repo_config

    for env in ("beta", "gamma", "prod"):
        block = json.dumps(load_repo_config(env)["explanations"])
        assert "anthropic" not in block and "per_1k" not in block


# ============================================================================ draft evidence schemas (EX-OQ-2)
@pytest.mark.parametrize("kind", ["performance_decomposition", "difference_inventory", "controlled_resolve_set", "grouped_shapley", "sensitivity_sweep"])
def test_fixture_evidence_matches_the_draft_schemas(kind):
    import jsonschema

    from tests.fakes import explanations as fx

    schema = json.loads((DRAFT / f"{kind}.json").read_text())
    docs = {
        "performance_decomposition": fx.performance_evidence(revision=True, partial_day="2026-03-31"),
        "difference_inventory": fx.inventory(),
        "controlled_resolve_set": fx.toy_resolve_set(["data", "configuration"], {"data": {"objective": 0.1}, "configuration": {"objective": 0.2}}),
        "grouped_shapley": fx.toy_shapley(["data", "configuration"], {"data": {"objective": 0.1}, "configuration": {"objective": 0.2}}),
        "sensitivity_sweep": fx.sweep_evidence([0.1, 0.2], infeasible=(1,)),
    }
    jsonschema.validate(docs[kind], schema)
    assert schema["x-finplan-status"] == "draft-not-published"


def test_guarded_tools_report_an_unreleased_tool_as_a_missing_dependency():
    # Regression (first beta deploy): compare_plan_versions is not in the released catalog yet;
    # the guard must call nothing and say DEPENDENCY_UNAVAILABLE, not ask for confirmation.
    b = ExplanationBackend()
    g = GuardedTools(b, catalog())
    with pytest.raises(AgentError) as exc:
        g.call("tool_that_was_never_released", {"idempotency_key": "k9"})
    assert exc.value.code == "DEPENDENCY_UNAVAILABLE" and exc.value.details["kind"] == "tool_not_released"
    assert b.calls == []
