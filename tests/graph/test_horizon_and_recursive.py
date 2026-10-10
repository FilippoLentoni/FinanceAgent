"""Hosted investigation and research workflows preserve producer evidence and confirmation."""
from copy import deepcopy
import json

import pytest

from finplan_agent.skills import classical_mcp_description, load_skills
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.portfolio import CLASSICAL_TOOLS, LIFECYCLE_TOOLS
from infra.stacks.tool_policy import CLASSICAL_POLICY_FILE, decide, load_policy
from finplan_contracts.schemas import contracts_root
from infra.stacks.tool_schemas import target_metadata
from tests.fakes.agent import SESSION, SESSION_B, FakeToolClient, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation

PD = "pd_01JA2B3C4D5E6F7G8H9JKMNPQR"
CYCLE = "ca_" + "a" * 32
ITERATION = "ca_" + "b" * 32
ANALYSIS = "ca_" + "c" * 32


def setup(rec, results):
    tools = FakeToolClient(results, extra_tools=list(CLASSICAL_TOOLS | LIFECYCLE_TOOLS))
    service, _ = make_service(tools=tools, provider=NoProvider())
    service.deps.catalog_loader = lambda: ToolCatalog([
        CatalogEntry(name, name in {"run_recursive_improvement", "run_portfolio_research", "resolve_portfolio_decision"}, "reader")
        for name in CLASSICAL_TOOLS | LIFECYCLE_TOOLS
    ], environment="beta")
    service.deps.skills, service.deps.stable_instructions = load_skills()
    tools.results["get_portfolio_decision"] = lambda _: {
        "decision": {"decision_id": PD, "portfolio_id": rec["portfolio_state"]["portfolio_id"], "recommendation": deepcopy(rec), "algorithm": "ppo"}}
    return service, tools


def ask(service, text, session=SESSION):
    return service.handle({"prompt": text, "stream": False}, auth(), runtime_session_id=session)


def cycle(**changes):
    return {"analysis_id": ITERATION, "analysis_kind": "recursive_iteration", "cycle_id": CYCLE,
            "summary": "Bounded research evidence", "state": "awaiting_experiment_approval", "dry_run": True,
            "iteration": 0, "max_iterations": 3, "cost_estimate": {"estimated_usd_upper_bound": .04},
            "activation": {"status": "proposal_only"}, "lineage": {"source_analysis_ids": [ANALYSIS]}, **changes}


@pytest.mark.parametrize("protocol_status", ["predeclared_at_issuance", "retrospective_historical_request"])
def test_partial_horizon_is_visible_separately_from_daily_loss(recommendation, protocol_status):
    horizon = {"status": "available", "available_forward_sessions": 2, "full_policy_replay": True,
               "protocol_status": protocol_status, "primary_horizon_mature": False,
               "protocol": {"primary_horizon_sessions": 64, "objective": {"kind": "expected cumulative discounted net reward"}},
               "windows": [{"horizon_sessions": 64, "observed_sessions": 2, "status": "partial"}],
               "evidence_assessment": {"status": "preliminary", "recommendation": "Insufficient forward evidence for the declared horizon"}}
    report = {"analysis_id": ANALYSIS, "analysis_kind": "performance", "summary": "Observed accounting",
              "trend": "red", "actual_source": "saved_paper", "observed_paper": {"pnl": -30},
              "horizon_evaluation": horizon}
    service, tools = setup(recommendation, {"evaluate_portfolio_decision": lambda _: deepcopy(report)})
    result = ask(service, "Evaluate the PPO strategy horizon for " + PD)
    assert [name for name, _ in tools.calls] == ["get_portfolio_decision", "evaluate_portfolio_decision"]
    assert result["status"] == "completed", result
    text = result["answer"]["narrative"]
    assert "Daily/accounting outcome: red" in text
    assert "Strategy objective and horizon evaluation" in text and "Insufficient forward evidence" in text
    assert "does not establish policy failure" in text and "different counterfactual" in text
    if protocol_status == "retrospective_historical_request":
        assert "historical counterfactual, not prospective validation" in text
    assert result["answer"]["portfolio_analyses"][0]["horizon_evaluation"] == horizon
    assert result["answer"]["claim_check"]["passed"]
    assert any(s["name"] == "investigate-portfolio-performance" for s in result["answer"]["skills_used"])


def test_daily_deep_dive_reads_numbers_before_dated_context(recommendation):
    report = {"analysis_id": ANALYSIS, "analysis_kind": "performance", "summary": "Observed accounting",
              "trend": "not_available", "horizon_evaluation": {"status": "not_available", "reason": "frozen policy artifact unavailable"}}
    service, tools = setup(recommendation, {
        "evaluate_portfolio_decision": lambda _: report,
        "research_market_events": lambda _: {"analysis_id": ITERATION, "analysis_kind": "market_events", "summary": "No dated source evidence", "source_status": "unavailable", "sources": []}})
    result = ask(service, "Deep dive into the daily loss and news for PPO " + PD)
    assert [name for name, _ in tools.calls] == ["get_portfolio_decision", "evaluate_portfolio_decision", "research_market_events"]
    assert tools.calls[-1][1] == {"analysis_id": ANALYSIS}
    assert result["status"] == "completed" and result["answer"]["claim_check"]["passed"]
    assert "frozen policy artifact unavailable" in result["answer"]["narrative"]
    assert "do not establish real-world causality" in result["answer"]["narrative"]


def test_recursive_request_reviews_without_paid_launch_or_model_call(recommendation):
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda _: cycle()})
    result = ask(service, "Start recursive improvement of my PPO strategy based on its performance")
    assert tools.calls == [("run_recursive_improvement", {"dry_run": True, "query": "Start recursive improvement of my PPO strategy based on its performance"})]
    assert result["status"] == "completed" and result["usage"]["invocations"] == 0
    assert "awaiting_experiment_approval" in result["answer"]["narrative"]
    assert result["answer"]["portfolio_analyses"][0]["lineage"]["source_analysis_ids"] == [ANALYSIS]
    assert result["answer"]["skills_used"][0]["name"] == "recursive-portfolio-improvement"


def test_paid_next_iteration_waits_for_exact_confirmation_and_preserves_cycle(recommendation):
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda args: cycle(dry_run=args.get("dry_run", True))})
    ask(service, "Review recursive improvement")
    pending = ask(service, "Run the next experiment in this cycle")
    assert pending["status"] == "awaiting_confirmation" and len(tools.calls) == 1
    args = pending["confirmation"]["calls"][0]["arguments"]
    assert args["cycle_id"] == CYCLE and args["dry_run"] is False
    assert args["confirmed_by_user"] is True and args["idempotency_key"]
    result = service.handle({"action": "confirm", "approve": True, "stream": False}, auth(), runtime_session_id=SESSION)
    assert result["status"] == "completed" and tools.calls[-1] == ("run_recursive_improvement", args)


def test_running_cycle_resume_is_readonly_without_poll_loop(recommendation):
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda _: cycle(state="experiment_running", job={"run_id": "run_01JA2B3C4D5E6F7G8H9JKMNPQR", "state": "running"})})
    result = ask(service, "Resume recursive improvement " + CYCLE, SESSION_B)
    assert tools.calls == [("run_recursive_improvement", {"cycle_id": CYCLE, "dry_run": True})]
    assert result["status"] == "completed" and "experiment_running" in result["answer"]["narrative"]


@pytest.mark.parametrize("family", ["Qwen agent swarm", "TypeSafe Jev"])
def test_benchmark_request_preserves_unavailable_configuration(recommendation, family):
    capability = {"configured": False, "reason": "pinned checkpoint not staged", "gpu_approval_required": True}
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda _: cycle(state="stopped", stopping_reason="benchmark_not_configured", benchmark_capabilities={"swarm_mode_a": capability})})
    result = ask(service, "Run the " + family + " benchmark")
    assert tools.calls[0][0] == "run_recursive_improvement" and tools.calls[0][1]["dry_run"] is True
    assert family in tools.calls[0][1]["query"]
    assert result["status"] == "completed" and "pinned checkpoint not staged" in result["answer"]["narrative"]
    assert all(name != "submit_experiment" for name, _ in tools.calls)


@pytest.mark.parametrize("family,name", [("swarm_mode_a", "Qwen swarm"), ("jev_backtest", "TypeSafe Jev")])
def test_named_benchmark_obtains_concrete_dry_run_then_confirms_paid_request(recommendation, family, name):
    request = json.loads((contracts_root()/"fixtures/tools/submit-experiment-request/valid/research.json").read_text())
    request["job_type"] = family
    request["configuration"]["payload"].update(strategy="qwen_swarm" if family == "swarm_mode_a" else "jev", objective="llm_benchmark")
    preview = cycle(proposed_experiment={"tool_request": {"name": "submit_experiment", "arguments": request}})
    estimate = {"dry_run": True, "run_id": None, "state": None,
                "cost_estimate": {"estimated_usd_upper_bound": .25, "budget_category": "gpu" if family == "swarm_mode_a" else "cpu_research"},
                "tool_limit": {"within_limit": True}, "message": "Dry run: no run was recorded; explicit approval required before compute."}
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda _: deepcopy(preview), "submit_experiment": lambda _: deepcopy(estimate)})
    result = ask(service, "Estimate the " + name + " benchmark")
    assert [tool for tool, _ in tools.calls] == ["run_recursive_improvement", "submit_experiment"]
    args = tools.calls[1][1]
    assert args["job_type"] == family and args["dry_run"] is True and args["purpose"] == "research"
    assert args["idempotency_key"] != request["idempotency_key"]
    assert result["status"] == "completed" and result["answer"]["claim_check"]["passed"], result
    assert "Sandbox benchmark evidence" in result["answer"]["narrative"] and "0.25" in result["answer"]["narrative"]
    pending = ask(service, "Run the " + name + " benchmark")
    assert pending["status"] == "awaiting_confirmation" and len(tools.calls) == 2
    call = pending["confirmation"]["calls"][0]
    paid = call["arguments"]
    assert call["tool"] == "run_recursive_improvement"
    assert paid["cycle_id"] == CYCLE and paid["dry_run"] is False and paid["confirmed_by_user"] is True
    assert paid["idempotency_key"] != args["idempotency_key"] and "job_type" not in paid
    declined = service.handle({"action": "confirm", "approve": False, "stream": False}, auth(), runtime_session_id=SESSION)
    assert declined["status"] == "completed" and len(tools.calls) == 2
    assert "No experiment was launched" in declined["answer"]["narrative"]


def test_confirmed_benchmark_job_is_owned_by_same_cycle_and_resume_cannot_duplicate_it(recommendation):
    request = json.loads((contracts_root()/"fixtures/tools/submit-experiment-request/valid/research.json").read_text())
    request.update(job_type="swarm_mode_a")
    request["configuration"]["payload"].update(strategy="qwen_swarm", objective="llm_benchmark")
    preview = cycle(proposed_experiment={"tool_request": {"name": "submit_experiment", "arguments": request}})
    job = {"run_id": "run_01JA2B3C4D5E6F7G8H9JKMNPQR", "state": "awaiting_approval"}
    launched = cycle(**{**preview, "job": job, "dry_run": False, "iteration": 1,
                        "lineage": [{"analysis_id": ITERATION, "iteration": 0}]})
    calls = []
    def recursive(args):
        calls.append(args)
        return deepcopy(launched if len(calls) > 1 else preview)
    service, tools = setup(recommendation, {
        "run_recursive_improvement": recursive,
        "submit_experiment": lambda _: {"dry_run": True, "cost_estimate": {"estimated_usd_upper_bound": .25}, "tool_limit": {"within_limit": True}}})
    ask(service, "Estimate Qwen swarm benchmark")
    pending = ask(service, "Run the Qwen benchmark")
    assert pending["status"] == "awaiting_confirmation"
    result = service.handle({"action": "confirm", "approve": True, "stream": False}, auth(), runtime_session_id=SESSION)
    assert result["status"] == "completed" and job["run_id"] in result["answer"]["narrative"]
    assert tools.calls[-1][0] == "run_recursive_improvement" and tools.calls[-1][1]["cycle_id"] == CYCLE
    assert tools.calls[-1][1]["dry_run"] is False
    resumed = ask(service, "Run the Qwen benchmark")
    assert resumed["status"] == "completed"
    assert tools.calls[-1] == ("run_recursive_improvement", {"cycle_id": CYCLE, "dry_run": True})
    assert sum(name == "submit_experiment" for name, _ in tools.calls) == 1
    assert sum(args.get("dry_run") is False for _, args in tools.calls) == 1


def test_explicit_benchmark_cycle_cannot_launch_another_family(recommendation):
    request = json.loads((contracts_root()/"fixtures/tools/submit-experiment-request/valid/research.json").read_text())
    request.update(job_type="swarm_mode_a")
    request["configuration"]["payload"].update(strategy="qwen_swarm", objective="llm_benchmark")
    preview = cycle(proposed_experiment={"tool_request": {"name": "submit_experiment", "arguments": request}})
    service, tools = setup(recommendation, {
        "run_recursive_improvement": lambda _: deepcopy(preview),
        "submit_experiment": lambda _: {"dry_run": True, "cost_estimate": {"estimated_usd_upper_bound": .25}, "tool_limit": {"within_limit": True}}})
    ask(service, "Estimate the Qwen benchmark")
    result = ask(service, "Run the Jev benchmark in cycle " + CYCLE)
    assert result["status"] == "completed"
    assert "complete matching sandbox benchmark request is unavailable" in result["answer"]["narrative"]
    assert tools.calls[-1] == ("run_recursive_improvement", {"cycle_id": CYCLE, "dry_run": True})
    assert all(args.get("dry_run") is True for _, args in tools.calls)


def test_unlinked_sandbox_estimate_does_not_authorize_recursive_launch(recommendation):
    from finplan_agent.graph.improvement import _benchmark_history
    request = json.loads((contracts_root()/"fixtures/tools/submit-experiment-request/valid/research.json").read_text())
    request.update(job_type="swarm_mode_a", dry_run=True)
    request["configuration"]["payload"].update(strategy="qwen_swarm", objective="llm_benchmark")
    preview = cycle(proposed_experiment={"tool_request": {"name": "submit_experiment", "arguments": request}})
    messages = [{"role": "tool", "content": [{"tool_result": {"name": "run_recursive_improvement", "status": "success", "content": preview}}]},
                {"role": "user", "content": [{"text": "Estimate a separate sandbox job"}]},
                {"role": "assistant", "content": [{"tool_use": {"name": "submit_experiment", "id": "independent", "input": request}}]},
                {"role": "tool", "content": [{"tool_result": {"name": "submit_experiment", "id": "independent", "status": "success", "content": {"dry_run": True, "cost_estimate": {}, "tool_limit": {"within_limit": True}}}}]}]
    assert _benchmark_history(messages) == []


def test_named_benchmark_does_not_submit_a_different_family(recommendation):
    request = json.loads((contracts_root()/"fixtures/tools/submit-experiment-request/valid/research.json").read_text())
    preview = cycle(proposed_experiment={"tool_request": {"name": "submit_experiment", "arguments": request}})
    service, tools = setup(recommendation, {"run_recursive_improvement": lambda _: preview})
    result = ask(service, "Estimate the Qwen swarm benchmark")
    assert [tool for tool, _ in tools.calls] == ["run_recursive_improvement"]
    assert "complete matching sandbox benchmark request is unavailable" in result["answer"]["narrative"]


def test_direct_and_hosted_investigation_share_versioned_instructions():
    inventory, instructions = load_skills()
    skill = next(row for row in inventory if row["name"] == "investigate-portfolio-performance")
    remote = classical_mcp_description("evaluate_portfolio_decision", "Evaluate decision")
    assert skill["instructions_checksum"] in remote
    assert next(text for text in instructions if text.startswith("# investigate-portfolio-performance\n")) in remote
    assert "Frozen first-allocation hold" in remote and "partial" in remote


def test_recursive_gateway_grants_and_identity_transport():
    policy = load_policy(CLASSICAL_POLICY_FILE)
    for role in ("viewer", "plan_editor", "plan_publisher", "ci_test"):
        assert decide("run_recursive_improvement", groups=[role], arguments={"dry_run": True}, doc=policy)
        assert not decide("run_recursive_improvement", groups=[role], arguments={"dry_run": False}, doc=policy)
    assert decide("run_recursive_improvement", groups=["researcher"], arguments={"dry_run": False}, doc=policy)
    assert target_metadata("beta", "run_recursive_improvement", classical=True) == {"allowedRequestHeaders": ["X-Finplan-User-Token"]}
    assert target_metadata("beta", "run_recursive_improvement", classical=False) == {}
    primary_policy = load_policy()
    for role in ("viewer", "plan_editor", "plan_publisher", "ci_test"):
        assert decide("submit_experiment", groups=[role], arguments={"dry_run": True}, doc=primary_policy)
        assert not decide("submit_experiment", groups=[role], arguments={"dry_run": False}, doc=primary_policy)
