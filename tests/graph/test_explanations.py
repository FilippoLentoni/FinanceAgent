"""Explanation workflow graph tests with fixture tool backends and the fixture provider (or a stubbed
Bedrock client); no network, no Bedrock (add-explanation-workflows FA-EV-01..10, FA-WF1-*, FA-WF2-*,
FA-WF3-*)."""

from __future__ import annotations

import json

import pytest

from finplan_agent.explanations.config import ExplanationSettings
from finplan_agent.explanations.result import validate_result
from finplan_agent.explanations.workflows import DISCLAIMER
from finplan_agent.providers.bedrock import BedrockProvider
from tests.fakes.agent import SESSION, StubBedrockClient, auth, bedrock_config, text_response
from tests.fakes.explanations import (
    CHK_NEW,
    CHK_PREV,
    PL,
    PUB,
    PV_C,
    PV_NEW,
    PV_PREV,
    SNAP_REAL,
    WINDOW,
    ExplanationBackend,
    explain_service,
    inventory,
    performance_evidence,
    sweep_evidence,
    toy_resolve_set,
    toy_shapley,
)
from tests.harness import BEDROCK_ATTEMPTS

EFFECTS = {"data": {"objective": 0.03, "expected_return": 0.004, "risk": 0.01, "weights": {"SPY": -0.05, "AGG": 0.05}}, "configuration": {"objective": -0.01, "expected_return": -0.002, "risk": -0.02, "weights": {"SPY": -0.1, "AGG": 0.1}}}
GROUPS = ["data", "configuration"]


def ask(svc, explanation, *, prompt=None, headers=None):
    payload = {"explanation": explanation, "stream": False}
    if prompt:
        payload["prompt"] = prompt
    return svc.handle(payload, headers or auth(), SESSION)


def confirm(svc, approve=True, headers=None):
    return svc.handle({"action": "confirm", "approve": approve, "stream": False}, headers or auth(), SESSION)


def real_submits(backend):
    return [a for n, a in backend.calls if n == "submit_experiment" and not a.get("dry_run")]


def change_req(**kw):
    return {"type": "recommendation_change", "plan_version_id": PV_NEW, "compare_to_plan_version_id": PV_PREV, **kw}


def run_change(backend, *, approve=True, **kw):
    svc = explain_service(backend, **kw.pop("svc_kw", {}))
    first = ask(svc, change_req(**kw))
    if first["status"] != "awaiting_confirmation":
        return svc, first, first
    return svc, first, confirm(svc, approve)


def result_of(final, *, strict=True):
    doc = final["answer"]["explanation"]
    assert doc is not None, final.get("error")
    validate_result(doc)
    if strict and doc.get("claim_check"):
        # Fixture narration is the deterministic statements: the claim check must never strip them.
        assert doc["claim_check"]["passed"], doc["claim_check"]
    return doc


def check_names(doc, status="passed"):
    return {c["name"] for c in doc["checks"] if c["status"] == status}


# ============================================================================ workflow 2
def test_identical_configuration_is_no_effect_without_jobs():  # FA-EV-06
    b = ExplanationBackend(inventory_doc=inventory(identical=True))
    svc = explain_service(b)
    final = ask(svc, change_req())
    doc = result_of(final)
    assert final["status"] == "completed" and final["error"] is None
    assert doc["solution_status"] == "no_effect" and doc["completion_status"] == "succeeded" and "no_effect" in doc["labels"]
    assert b.names() == ["compare_plan_versions"]  # no dry run, no job
    assert doc["evidence"][0]["checksum"].startswith("sha256:") and doc["narrative_status"] == "generated"
    assert CHK_PREV in doc["narrative"] and "[ev1]" in doc["narrative"] and doc["citations"] == [{"cite": "ev1", "artifact_id": doc["evidence"][0]["artifact_id"]}]


def test_two_factor_groups_additive_controlled_resolves():  # FA-WF2-02, FA-WF2-03, FA-EV-01/02/05
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS)})
    svc, first, final = run_change(b)
    conf = first["confirmation"]
    assert first["status"] == "awaiting_confirmation" and conf["explanation"]["kind"] == "evidence_jobs"
    assert [c["tool"] for c in conf["calls"]] == ["submit_experiment"] and conf["calls"][0]["arguments"]["job_type"] == "controlled_resolve"
    assert conf["explanation"]["estimates"][0]["budget_category"] == "cpu_research"
    assert real_submits(b) == [conf["calls"][0]["arguments"]]  # exactly the confirmed call
    doc = result_of(final)
    assert final["status"] == "completed"
    assert {"base_reproduction", "all_switched_reproduction", "decomposition_sum_objective", "effect_data_objective"} <= check_names(doc)
    assert "modeled_effect" in doc["labels"] and doc["disclaimer"] == DISCLAIMER and DISCLAIMER in doc["narrative"]
    inv = doc["evidence_items"][0]
    assert any("/payload/risk_aversion: 2.0 to 2.5" in s for s in inv["statements"])
    assert "switching only data changes the objective by 0.03" in doc["narrative"]
    assert doc["claim_check"]["passed"] and len(doc["evidence"]) == 2
    assert [c["cite"] for c in doc["citations"]] == ["ev1", "ev2"]


def test_interacting_model_reports_the_planted_remainder():  # FA-WF2-03 (E8 interacting fixture)
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS, interaction={"objective": 0.007})})
    _, _, final = run_change(b)
    doc = result_of(final)
    cr = doc["evidence_items"][1]
    assert "decomposition_sum_objective" in check_names(doc)
    assert any("interaction remainder of 0.007" in s for s in cr["statements"])


def test_infeasible_single_switch_is_kept_and_the_workflow_continues():
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS, infeasible=("configuration",))})
    _, _, final = run_change(b)
    doc = result_of(final)
    assert final["status"] == "completed" and "infeasible_switch_configuration" in doc["labels"]
    assert any(c["name"] == "decomposition_sum_objective" and c["status"] == "not_applicable" for c in doc["checks"])
    assert "infeasible (solution_status infeasible, completion_status succeeded)" in doc["narrative"]


def test_base_reproduction_failure_blocks_narration():  # FA-EV-04
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS, base_drift=0.01)})
    _, _, final = run_change(b)
    assert final["status"] == "failed" and final["error"]["code"] == "VALIDATION_FAILED"
    assert "base_reproduction" in final["error"]["message"] and final["answer"]["explanation"] is None and final["answer"]["narrative"] == ""
    assert final["error"]["details"]["failed_checks"][0]["name"] == "base_reproduction"


def test_additive_shapley_equals_single_effects():  # FA-WF2-04
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS), "grouped_shapley": toy_shapley(GROUPS, EFFECTS)})
    _, first, final = run_change(b, shapley=True)
    assert [c["arguments"]["job_type"] for c in first["confirmation"]["calls"]] == ["controlled_resolve", "grouped_shapley"]
    doc = result_of(final)
    sh = doc["evidence_items"][2]
    assert {"shapley_efficiency_objective", "shapley_resolve_count", "shapley_total_matches_resolves"} <= check_names(doc)
    assert any("assigns data 0.03 of the modeled objective change" in s for s in sh["statements"])
    assert any("uses 4 re-solves and is ordering-invariant" in s for s in sh["statements"])


def test_shapley_efficiency_violation_fails():
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS), "grouped_shapley": toy_shapley(GROUPS, EFFECTS, tamper=0.01)})
    _, _, final = run_change(b, shapley=True)
    assert final["error"]["code"] == "VALIDATION_FAILED" and "shapley_efficiency_objective" in final["error"]["message"]


def test_shapley_over_too_many_groups_is_refused():
    b = ExplanationBackend()
    svc = explain_service(b, settings=ExplanationSettings(enabled=True, max_shapley_groups=1))
    final = ask(svc, change_req(shapley=True))
    err = final["error"]
    assert err["code"] == "VALIDATION_FAILED" and err["details"]["max_shapley_groups"] == 1 and err["details"]["required_resolves"] == 4
    assert "submit_experiment" not in b.names()


def test_shapley_over_budget_offers_controlled_resolves():  # FA-EV-07
    b = ExplanationBackend(estimate_usd=0.3)
    svc = explain_service(b)
    final = ask(svc, change_req(shapley=True))
    err = final["error"]
    assert err["code"] == "BUDGET_EXCEEDED" and err["details"]["estimated_usd_upper_bound"] == 0.6 and err["details"]["offer"].startswith("controlled_resolve_only")
    assert real_submits(b) == [] and final["confirmation"] is None


def test_cpu_research_allocation_insufficient_submits_nothing():  # FA-EV-07
    b = ExplanationBackend(estimate_usd=0.05, remaining_usd=0.01)
    final = ask(explain_service(b), change_req())
    err = final["error"]
    assert err["code"] == "BUDGET_EXCEEDED" and err["details"]["remaining_allocation_usd"] == 0.01 and err["details"]["budget_category"] == "cpu_research"
    assert real_submits(b) == []


def test_non_cpu_evidence_job_is_never_submitted():
    b = ExplanationBackend(category="gpu")
    final = ask(explain_service(b), change_req())
    assert final["error"]["code"] == "OPERATION_NOT_PERMITTED" and real_submits(b) == []


def test_rl_reward_change_requires_retraining_and_submits_nothing():  # FA-WF2-05
    inv = inventory(differences=[{"group": "model", "previous": "mv_a", "new": "mv_b"}], family="rl", spec_hashes=("reward-a", "reward-b"))
    b = ExplanationBackend(inventory_doc=inv)
    final = ask(explain_service(b), change_req())
    doc = result_of(final)
    assert "requires_retraining" in doc["labels"] and "submit_experiment" not in b.names()
    assert doc["findings"]["experiment_proposal"]["submitted"] is False and "human_approval" in doc["findings"]["experiment_proposal"]["requires"]
    assert "requires_retraining" in doc["narrative"]


def test_differing_horizons_are_refused():  # FA-WF2-01
    b = ExplanationBackend(inventory_doc=inventory(horizon_new={"holding_period": "P3M", "rebalance": "monthly"}))
    final = ask(explain_service(b), change_req())
    err = final["error"]
    assert err["code"] == "PRECONDITION_FAILED" and err["details"]["previous_horizon"]["holding_period"] == "P1M" and err["details"]["new_horizon"]["holding_period"] == "P3M"


def test_manual_override_child_is_not_resolved():  # FA-WF2-02 override scenario
    inv = inventory(origin="manual_override", differences=[{"group": "configuration", "pointer": "/payload/weights/SPY", "previous": 0.6, "new": 0.55}])
    b = ExplanationBackend(inventory_doc=inv)
    doc = result_of(ask(explain_service(b), change_req()))
    assert "manual_override" in doc["labels"] and "submit_experiment" not in b.names()
    assert "not re-solved" in doc["narrative"]


def test_stochastic_model_reports_seed_spread_and_uncertainty():  # FA-WF2-06
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS, deterministic=False)}, inventory_doc=inventory(deterministic=False))
    _, _, final = run_change(b)
    doc = result_of(final)
    assert "uncertain_attribution" in doc["labels"] and "seed_spread_reported" in check_names(doc)
    assert "the attribution is uncertain" in doc["narrative"] and "0.004" in doc["narrative"]


def test_declined_evidence_jobs_create_nothing():
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS)})
    _, _, final = run_change(b, approve=False)
    assert real_submits(b) == [] and final["status"] == "completed" and "No evidence job was submitted" in final["answer"]["narrative"]


def test_long_running_job_ends_in_progress_and_resumes_by_run_id():  # FA-RT-04 for evidence
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS)}, job_state="running")
    svc, _, final = run_change(b)
    assert final["status"] == "in_progress"
    runs = final["in_progress"]["runs"]
    run_id = runs[0]["run_id"]
    assert run_id.startswith("run_") and "get_experiment_result" not in b.names()
    b.job_state = "succeeded"
    before = len(real_submits(b))
    again = ask(svc, change_req(evidence_run_ids={"controlled_resolve_set": run_id}))
    doc = result_of(again)
    assert len(real_submits(b)) == before and doc["evidence_items"][1]["run_id"] == run_id


def test_missing_read_tool_is_dependency_unavailable():  # EX-OQ-9
    class NoCompare(ExplanationBackend):
        _t_compare_plan_versions = None  # type: ignore[assignment]

    final = ask(explain_service(NoCompare()), change_req())
    assert final["error"]["code"] == "DEPENDENCY_UNAVAILABLE" and final["error"]["details"]["reason"] == "tool_not_offered"


def test_explanations_disabled_in_environment():
    final = ask(explain_service(ExplanationBackend(), settings=ExplanationSettings(enabled=False)), change_req())
    assert final["error"]["code"] == "OPERATION_NOT_PERMITTED" and final["error"]["details"]["kind"] == "explanations_disabled"


def test_prod_repo_config_keeps_explanations_disabled():
    from finplan_agent.config.settings import load_repo_config
    from finplan_agent.explanations.config import load_explanation_settings

    assert load_explanation_settings(load_repo_config("prod")).enabled is False
    assert load_explanation_settings(load_repo_config("beta")).enabled is True


# ============================================================================ identity (FA-EV-10)
def test_tools_run_through_the_callers_own_client():
    seen = []
    b = ExplanationBackend(inventory_doc=inventory(identical=True))
    svc = explain_service(b)
    factory = svc.deps.tool_client_factory
    svc.deps.tool_client_factory = lambda caller: (seen.append(caller.public()), factory(caller))[1]
    ask(svc, change_req(), headers=auth("researcher-7", groups=("researcher",)))
    assert len(seen) == 1 and "researcher" in json.dumps(seen[0])


def test_caller_denied_submit_gets_evidence_unavailable_and_no_job():
    b = ExplanationBackend(deny=("submit_experiment",))
    final = ask(explain_service(b), change_req(), headers=auth("viewer-1", groups=("viewer",)))
    assert final["status"] == "completed" and final["error"]["code"] == "FORBIDDEN"
    assert "Evidence is unavailable for this caller" in final["answer"]["narrative"] and real_submits(b) == []


# ============================================================================ workflow 1
def perf_req(**kw):
    return {"type": "plan_performance", "publication_id": PUB, "window": WINDOW, "realized_snapshot_id": SNAP_REAL, **kw}


def run_perf(backend, req=None):
    svc = explain_service(backend)
    first = ask(svc, req or perf_req())
    if first["status"] != "awaiting_confirmation":
        return first
    return confirm(svc)


def test_publication_anchor_and_planted_components():  # FA-WF1-01, 03, 04, 05
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence()})
    final = run_perf(b)
    doc = result_of(final)
    args = real_submits(b)[0]
    assert args["job_type"] == "performance_decomposition" and args["input_snapshot_id"] == SNAP_REAL and args["evaluation_window"] == WINDOW
    assert doc["request_ids"]["publication_id"] == PUB and doc["request_ids"]["plan_version_checksum"] == CHK_PREV
    assert {"anchor_identifiers", "reconciliation_plan_path", "reconciliation_executed_path", "gap_components_sum_to_total", "forecast_position_consistent"} <= check_names(doc)
    assert "execution gap -0.004" in doc["narrative"] and "cost gap -0.0015" in doc["narrative"]
    assert "within forecast uncertainty and is not attributed to model error" in doc["narrative"] and "within_forecast_interval" in doc["labels"]


def test_unpublished_version_is_refused_with_available_publications():  # FA-WF1-01
    b = ExplanationBackend()
    final = ask(explain_service(b), {"type": "plan_performance", "plan_version_id": PV_C, "window": WINDOW, "realized_snapshot_id": SNAP_REAL})
    err = final["error"]
    assert err["code"] == "PRECONDITION_FAILED" and err["details"]["available_publications"] == [{"publication_id": PUB, "plan_version_id": PV_PREV}]


def test_plan_id_resolves_to_one_publication():
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence()})
    doc = result_of(run_perf(b, {"type": "plan_performance", "plan_id": PL, "window": WINDOW, "realized_snapshot_id": SNAP_REAL}))
    assert doc["request_ids"]["publication_id"] == PUB and doc["request_ids"]["resolved_from"] == "plan_id"


def test_executed_path_reconciliation_failure():  # FA-WF1-03
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence(executed_end_override=101000.0)})
    final = run_perf(b)
    assert final["error"]["code"] == "VALIDATION_FAILED" and "reconciliation_executed_path" in final["error"]["message"] and final["answer"]["explanation"] is None


def test_decomposition_that_does_not_sum_fails():  # FA-EV-04, FA-WF1-04
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence(total_override=0.02)})
    final = run_perf(b)
    assert final["error"]["code"] == "VALIDATION_FAILED" and "gap_components_sum_to_total" in final["error"]["message"]


def test_no_published_forecast_is_not_available():  # FA-WF1-05
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence(forecast=False)})
    doc = result_of(run_perf(b))
    assert "forecast_not_available" in doc["labels"] and doc["findings"]["forecast_uncertainty"] == "not_available"
    assert "Forecast uncertainty is not_available" in doc["narrative"]


def test_intraday_partial_day_is_flagged_in_the_narrative():  # FA-WF1-02
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence(partial_day="2026-03-31")})
    doc = result_of(run_perf(b))
    assert "partial_period" in doc["labels"] and "2026-03-31 is intraday_partial" in doc["narrative"]


def test_revised_observation_is_a_labeled_modeled_effect():  # FA-WF1-06
    b = ExplanationBackend(evidence={"performance_decomposition": performance_evidence(revision=True)})
    doc = result_of(run_perf(b))
    assert "modeled_effect" in doc["labels"] and "revised from 472.31 to 472.8" in doc["narrative"] and "revised_observation" in doc["narrative"]


def test_live_actual_source_is_not_permitted():
    final = ask(explain_service(ExplanationBackend()), perf_req(actual_source="live"))
    assert final["error"]["code"] == "OPERATION_NOT_PERMITTED"


# ============================================================================ workflow 3
def sweep_req(values, name="turnover_limit", **kw):
    return {"type": "sensitivity", "plan_version_id": PV_PREV, "parameter": {"name": name, "values": values, **kw}}


def run_sweep(backend, req, **kw):
    svc = explain_service(backend, **kw)
    first = ask(svc, req)
    if first["status"] != "awaiting_confirmation":
        return svc, first
    return svc, confirm(svc)


VALUES = [0.05, 0.1, 0.15, 0.2, 0.3]


def test_five_point_turnover_sweep_with_concession():  # FA-WF3-01/02/03
    ev = sweep_evidence(VALUES, turnovers=[0.04, 0.08, 0.12, 0.16, 0.3])
    b = ExplanationBackend(evidence={"sensitivity_sweep": ev})
    _, final = run_sweep(b, sweep_req(VALUES))
    subs = real_submits(b)
    assert len(subs) == 1 and subs[0]["configuration"]["payload"]["parameter"]["values"] == VALUES
    doc = result_of(final)
    pts = doc["findings"]["points"]
    assert [p["classification"] for p in pts] == ["permitted", "permitted", "permitted", "permitted", "concession"]
    assert pts[4]["concessions"] == [{"item": "turnover_limit", "original": 0.2, "required": 0.3}] and pts[0]["concessions"] == []
    assert "turnover_limit original 0.2, required 0.3" in doc["narrative"] and "concession" in doc["labels"]
    assert "point_0_portfolio_wide_change" in check_names(doc)
    assert "Allocation changes versus the base at turnover_limit 0.05: AGG -0.01, GLD 0.03, SPY -0.02" in doc["narrative"]


def test_grid_larger_than_maximum_is_refused():  # FA-WF3-01
    b = ExplanationBackend()
    final = ask(explain_service(b, settings=ExplanationSettings(enabled=True, max_sweep_points=3)), sweep_req(VALUES))
    assert final["error"]["code"] == "VALIDATION_FAILED" and final["error"]["details"]["max_sweep_points"] == 3 and b.calls == []


def test_change_reported_only_for_the_capped_asset_fails():  # FA-WF3-02
    b = ExplanationBackend(evidence={"sensitivity_sweep": sweep_evidence(VALUES, name="max_weight", drop_change_for="SPY")})
    _, final = run_sweep(b, sweep_req(VALUES, name="max_weight", instrument="SPY"))
    assert final["error"]["code"] == "VALIDATION_FAILED" and "portfolio_wide_change" in final["error"]["message"]


def test_flat_region_and_infeasible_points_are_kept():  # FA-WF3-04
    values = [2.0, 2.5, 3.0, 3.5, 4.0]
    b = ExplanationBackend(evidence={"sensitivity_sweep": sweep_evidence(values, name="risk_aversion", flat=(0, 1, 2), infeasible=(4,))}, constraints={"max_weight": 0.9})
    _, final = run_sweep(b, sweep_req(values, name="risk_aversion"))
    doc = result_of(final)
    statuses = [p["solution_status"] for p in doc["findings"]["points"]]
    assert statuses == ["no_effect", "no_effect", "no_effect", "optimal", "infeasible"]
    assert all(p["completion_status"] == "succeeded" for p in doc["findings"]["points"])
    assert "insensitive under the model for risk_aversion from 2.0 to 3.0" in doc["narrative"]
    assert "risk_aversion 4.0: solution_status infeasible" in doc["narrative"]


def test_non_monotonic_sweep_is_reported_as_computed():  # FA-WF3-06
    b = ExplanationBackend(evidence={"sensitivity_sweep": sweep_evidence(VALUES, risks=[0.12, 0.14, 0.13, 0.15, 0.11])}, constraints={"max_weight": 0.9})
    _, final = run_sweep(b, sweep_req(VALUES))
    doc = result_of(final)
    assert "Computed risk is non-monotonic across the swept values" in doc["narrative"]


def test_adopting_a_concession_point_needs_confirmation_and_keeps_the_policy():  # FA-WF3-05, FA-EV-08
    ev = sweep_evidence(VALUES, turnovers=[0.04, 0.08, 0.12, 0.16, 0.3])
    b = ExplanationBackend(evidence={"sensitivity_sweep": ev})
    svc, _ = run_sweep(b, sweep_req(VALUES))
    sweep_run = next(r for r, t in b.run_ids.items() if t == "sensitivity_sweep")
    b.calls.clear()
    first = ask(svc, {"type": "adopt_alternative", "plan_version_id": PV_PREV, "sweep_run_id": sweep_run, "point_index": 4})
    conf = first["confirmation"]
    assert first["status"] == "awaiting_confirmation" and conf["explanation"]["concessions"] == [{"item": "turnover_limit", "original": 0.2, "required": 0.3}]
    call = conf["calls"][0]
    assert call["tool"] == "create_override_version" and call["arguments"]["content"]["constraints"] == {"max_turnover": 0.2, "max_weight": 0.7}
    assert "create_override_version" not in b.names()
    final = confirm(svc)
    created = [a for n, a in b.calls if n == "create_override_version"]
    assert created == [call["arguments"]] and final["status"] == "completed"
    assert "stored constraints and risk preferences are unchanged" in final["answer"]["narrative"]


def test_declined_adoption_calls_no_plan_tool():
    ev = sweep_evidence(VALUES, turnovers=[0.04, 0.08, 0.12, 0.16, 0.3])
    b = ExplanationBackend(evidence={"sensitivity_sweep": ev})
    svc, _ = run_sweep(b, sweep_req(VALUES))
    sweep_run = next(iter(b.run_ids))
    ask(svc, {"type": "adopt_alternative", "plan_version_id": PV_PREV, "sweep_run_id": sweep_run, "point_index": 4})
    final = confirm(svc, approve=False)
    assert "create_override_version" not in b.names() and "stored policy is unchanged" in final["answer"]["narrative"]


# ============================================================================ narration (FA-EV-02, FA-EV-09)
def bedrock_service(backend, stub, **cfg):
    cfg_obj = bedrock_config(**cfg)
    return explain_service(backend, environment="gamma", provider=BedrockProvider(cfg_obj, stub), provider_config=cfg_obj, settings=ExplanationSettings(enabled=True, narration_max_tokens_invocation=300))


def test_bedrock_narration_is_capped_claim_checked_and_costed():
    b = ExplanationBackend(inventory_doc=inventory(identical=True))
    text = f"The two versions share checksum {CHK_PREV} [ev1]. This caused a 0.9 shift [ev1]. Risk rises monotonically [ev1]. Totals are 7 [ev9]."
    stub = StubBedrockClient([text_response(text)])
    final = ask(bedrock_service(b, stub), change_req())
    doc = result_of(final, strict=False)
    assert len(stub.requests) == 1 and stub.requests[0]["inferenceConfig"]["maxTokens"] == 300 and "toolConfig" not in stub.requests[0]
    sent = json.dumps(stub.requests[0]["messages"])
    assert "statements" in sent and "[ev1]" in sent  # compact evidence summary, last
    cc = doc["claim_check"]
    assert not cc["passed"] and "0.9" in cc["removed_figures"] and cc["unknown_citations"] == ["ev9"] and cc["causal_rewrites"] == ["caused"]
    assert cc["rejected_sentences"][0]["rule"] == "unsupported_trend"
    assert "0.9" not in doc["narrative"] and "monotonic" not in doc["narrative"] and "is modeled to account for" in doc["narrative"]
    assert final["usage"]["provider_kind"] == "bedrock" and final["usage"]["estimated_cost_usd"] > 0
    assert BEDROCK_ATTEMPTS == []


def test_session_budget_insufficient_returns_evidence_with_budget_exceeded():
    b = ExplanationBackend(inventory_doc=inventory(identical=True))
    stub = StubBedrockClient([text_response("unused")])
    final = ask(bedrock_service(b, stub, max_cost_session_usd=0.0001), change_req())
    doc = result_of(final)
    assert stub.requests == [] and doc["narrative_status"] == "budget_exceeded" and doc["evidence"]
    assert "remaining" in doc["narrative_message"] and "estimate" in doc["narrative_message"] and final["error"]["code"] == "BUDGET_EXCEEDED"
    assert doc["evidence_items"][0]["statements"]


def test_provider_unavailable_returns_evidence_with_unavailable():
    from tests.fakes.agent import ClientError

    b = ExplanationBackend(inventory_doc=inventory(identical=True))
    stub = StubBedrockClient(error=ClientError("ServiceUnavailableException"))
    doc = result_of(ask(bedrock_service(b, stub), change_req()))
    assert doc["narrative_status"] == "unavailable" and doc["evidence"] and doc["citations"] == []


def test_ci_explanations_use_the_fixture_provider_only():
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS)})
    svc, _, final = run_change(b)
    assert svc.deps.provider.kind == "fixture" and final["usage"]["provider_kind"] == "fixture" and BEDROCK_ATTEMPTS == []


@pytest.mark.parametrize("bad", [{"type": "nope"}, change_req(groups=["weather"]), {"type": "sensitivity", "plan_version_id": PV_PREV, "parameter": {"name": "leverage", "values": [1]}}])
def test_invalid_explanation_requests(bad):
    final = ask(explain_service(ExplanationBackend()), bad)
    assert final["status"] == "failed" and final["error"]["code"] == "VALIDATION_FAILED"


def test_new_version_checksum_reaches_the_inventory_statements():
    b = ExplanationBackend(evidence={"controlled_resolve": toy_resolve_set(GROUPS, EFFECTS)})
    _, _, final = run_change(b)
    assert CHK_NEW in result_of(final)["evidence_items"][0]["statements"][1]


def test_streamed_explanation_events_and_final_match_non_streamed():  # FA-RT-02 for explanations
    def go(stream):
        b = ExplanationBackend(inventory_doc=inventory(identical=True))
        svc = explain_service(b)
        out = svc.handle({"explanation": change_req(), "stream": stream}, auth(), SESSION)
        return list(out) if stream else out

    events = go(True)
    types = [e["type"] for e in events]
    assert types[0] == "progress" and types[-1] == "final" and types.index("tool_call") < types.index("tool_result_summary") < types.index("token")
    assert any(e.get("stage") == "explain_prepare" for e in events)
    streamed, plain = events[-1], go(False)
    for d in (streamed, plain):
        d.pop("correlation_id")
        d["answer"]["explanation"].pop("generated_at")
    assert json.dumps(streamed, sort_keys=True) == json.dumps(plain, sort_keys=True)
