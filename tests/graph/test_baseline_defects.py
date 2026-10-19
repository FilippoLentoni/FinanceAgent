"""Regressions for the phase 2 baseline sweep of the deployed agent (D1, D2, D4, D5, D6, D8, D9, D10)."""
import json
from types import SimpleNamespace

from finplan_contracts.validate import validate

from finplan_agent.providers.base import GenerateResult, ToolCall, Usage
from finplan_agent.tools.catalog import CatalogEntry, ToolCatalog
from finplan_agent.tools.recording import decode_stored, write_activity
from tests.fakes.agent import PL, PV, SESSION, FakeToolClient, auth, make_service
from tests.unit.test_saved_portfolio_recommendations import NoProvider

MACHINE = auth("comparison-machine", groups=(), extra={"client_id": "comparison-machine"})
A = "ca_" + "a" * 32


def activity_store(*, refuse=False):
    events = []

    def record(args):
        result = validate(args, "tools/record-agent-activity-request")
        assert result.valid, result.issues
        if refuse:
            raise AssertionError("not reached")
        events.append(json.loads(json.dumps(args)))
        return {"activity_event_id": f"ae_{len(events):04d}", "checksum": "sha256:" + "a" * 64, "recorded_at": "2026-10-19T10:00:00Z"}
    return events, record


class PlanningProvider:
    """An LLM-like planner: one tool call, then a narrative (two provider invocations)."""
    kind, model_id = "fixture", None

    def __init__(self, call):
        self.call = call
        self.calls = 0

    def estimate_input_tokens(self, request):
        return 10

    def generate(self, request, on_token=None):
        self.calls += 1
        if request.purpose == "plan" and self.calls == 1:
            return GenerateResult(text="", tool_calls=(self.call,), usage=Usage(), stop_reason="tool_use", provider_kind="fixture", model_id=None)
        return GenerateResult(text="Plan " + PL + " was read.", tool_calls=(), usage=Usage(), stop_reason="end_turn", provider_kind="fixture", model_id=None)


def test_d1_capture_never_consumes_the_tool_call_budget():
    events, record = activity_store()
    tools = FakeToolClient({"get_plan": lambda a: {"plan_id": a["plan_id"], "head_plan_version_id": PV}, "record_agent_activity": record}, extra_tools=["record_agent_activity"])
    svc, tools = make_service(tools=tools, provider=PlanningProvider(ToolCall(id="p1", name="get_plan", arguments={"plan_id": PL})))
    final = svc.handle({"prompt": "Show plan " + PL, "stream": False, "capture_inputs": True}, MACHINE, SESSION)
    assert final["status"] == "completed", final["error"]
    assert final["usage"]["tool_calls"] == 1 and len(final["captured_inputs"]) >= 3
    assert final["input_capture_status"] == "captured_at_invocation_boundary"


def test_d2_refused_capture_write_never_stops_the_tool():
    tools = FakeToolClient({"get_plan": lambda a: {"plan_id": a["plan_id"]}}, extra_tools=["record_agent_activity"],
                           errors={"record_agent_activity": {"code": "VALIDATION_FAILED", "message": "refused", "retryable": False, "details": {}}})
    svc, tools = make_service(tools=tools)
    final = svc.handle({"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False, "capture_inputs": True}, MACHINE, SESSION)
    assert ("get_plan", {"plan_id": PL}) in tools.calls
    assert final["input_capture_status"] == "incomplete_durable_capture"
    assert any(e.get("durable_status") == "capture_failed" and e.get("error_code") for e in final["captured_inputs"])


def test_d2_large_planner_capture_is_encoded_and_split_into_parts():
    events, record = activity_store()
    sink = FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"])
    supplied = {"system": "pattern ^[a-z]\\d+$ and /finplan/beta/x path", "messages": ["x" * 2000 for _ in range(60)]}
    from finplan_agent.tools.recording import encode_for_activity
    stored, _ = encode_for_activity(supplied, ("supplied",))
    out = write_activity(sink, event_kind="modeling_input_capture", session_id=SESSION, correlation_id="corr-cap", synthetic=True,
                         idempotency_key="input-test", metadata={"boundary": "provider.generate"}, content={"supplied": stored}, max_event_bytes=50_000, chunk_bytes=40_000)
    assert len(out["part_activity_event_ids"]) == len(events) - 1 >= 2
    import base64
    data = b"".join(base64.urlsafe_b64decode(e["payload"]["content"]["value"] + "=" * (-len(e["payload"]["content"]["value"]) % 4)) for e in events[1:])
    assert decode_stored(json.loads(data)["supplied"]) == supplied


def test_d4_turn_archive_with_paths_and_escapes_passes_the_storage_location_check():
    from finplan_agent.graph.activity import archive_turn
    events, record = activity_store()
    ctx = SimpleNamespace(session_id=SESSION, release_id="rel_x", tool_recorder=None,
                          tools=FakeToolClient({"record_agent_activity": record}, extra_tools=["record_agent_activity"]))
    narrative = "See ftp://mirror.example/paper and C:\\models; source excerpt http://example.org/a"
    state = {"messages": [{"role": "user", "content": [{"text": "research"}]}], "correlation_id": "corr-d4", "narrative": narrative,
             "tool_results": [{"id": "x", "tool": "research_portfolio_models", "ok": True, "result": {"sources": [{"excerpt": "/root/path \\ escaped"}]}}]}
    assert archive_turn(state, ctx, "completed", {}).ok
    assert decode_stored(events[0]["payload"]["narrative"]) == narrative


def test_d5_production_strategy_get_is_a_read_without_confirmation():
    tools = FakeToolClient({"production_strategy": lambda a: {"strategy": "ppo"}}, extra_tools=["production_strategy"])
    svc, tools = make_service(tools=tools)
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry("production_strategy", True, "plan-writer")], environment="beta")
    final = svc.handle({"tool_request": {"name": "production_strategy", "arguments": {"action": "get"}}, "stream": False}, auth(), SESSION)
    assert final["status"] == "completed" and final["confirmation"] is None
    assert tools.calls == [("production_strategy", {"action": "get"})]
    pending = svc.handle({"tool_request": {"name": "production_strategy", "arguments": {"action": "set", "strategy": "ppo"}}, "stream": False}, auth(), SESSION + "x")
    assert pending["status"] == "awaiting_confirmation"


def test_d8_tool_request_is_authoritative_or_refused_with_a_typed_error():
    svc, tools = make_service(provider=NoProvider())  # the fake Gateway does not offer resolve_portfolio_decision
    refused = svc.handle({"prompt": "Reject it", "tool_request": {"name": "resolve_portfolio_decision", "arguments": {"decision_id": "pd_01JA2B3C4D5E6F7G8H9JKMNPQR"}}, "stream": False}, auth(), SESSION)
    assert refused["status"] == "failed" and refused["error"]["code"] == "FORBIDDEN" and not tools.calls
    assert "resolve_portfolio_decision" in refused["answer"]["narrative"]
    denied = svc.handle({"tool_request": {"name": "place_order", "arguments": {}}, "stream": False}, auth(), SESSION + "y")
    assert denied["error"]["code"] == "OPERATION_NOT_PERMITTED" and not tools.calls


def test_d8_deterministic_routes_never_replace_the_requested_tool():
    tools = FakeToolClient({"submit_experiment": lambda a: {"dry_run": True, "estimated_cost_usd": 0.05}}, extra_tools=["submit_experiment", "get_etf_research_inputs"])
    svc, tools = make_service(tools=tools)
    args = {"dry_run": True, "job_type": "run_backtest", "instruments": ["AAPL", "VOO"]}
    final = svc.handle({"prompt": "Estimate a VOO and AAPL research backtest recommendation", "tool_request": {"name": "submit_experiment", "arguments": args}, "stream": False}, auth(), SESSION)
    assert [name for name, _ in tools.calls] == ["submit_experiment"]
    assert final["status"] in ("completed", "awaiting_confirmation")


def test_d9_listing_stored_classical_analyses_never_issues_a_recommendation():
    rows = [{"analysis_id": A, "analysis_kind": "recommendation", "created_at": "2026-10-18T13:45:00Z", "recommendation": {"strategy": "cvar"}}]
    names = ["list_classical_analyses", "generate_portfolio_recommendation_min_variance"]
    tools = FakeToolClient({"list_classical_analyses": lambda a: {"analyses": rows, "next_token": None}}, extra_tools=names)
    svc, tools = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry(n, False, "reader") for n in names], environment="beta")
    final = svc.handle({"prompt": "List stored classical recommendation analyses.", "stream": False}, auth(), SESSION)
    assert tools.calls == [("list_classical_analyses", {"limit": 10, "kind": "recommendation"})]
    assert A in final["answer"]["narrative"] and "No compatible stored" not in final["answer"]["narrative"]
    assert final["answer"]["claim_check"]["passed"]


def test_feedback_requires_explicit_instruction_and_confirmation():
    names = ["submit_portfolio_feedback", "list_classical_analyses"]
    tools = FakeToolClient({"submit_portfolio_feedback": lambda a: {"analysis_id": A, "analysis_kind": "feedback"}}, extra_tools=names)
    svc, tools = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry(n, False, "reader") for n in names], environment="beta")
    pending = svc.handle({"prompt": f"Record my feedback on traditional analysis {A}: too much turnover.", "stream": False}, auth(), SESSION)
    assert pending["status"] == "awaiting_confirmation" and not tools.calls


def test_d6_tool_error_still_yields_a_deterministic_narrative():
    from finplan_agent.core.errors import AgentError

    class Failing(PlanningProvider):
        def generate(self, request, on_token=None):
            if request.purpose == "narrate":
                raise AgentError.budget("turn token limit", limit="max_tokens_turn")
            self.calls += 1
            calls = (self.call,) if self.calls == 1 else ()
            return GenerateResult(text="", tool_calls=calls, usage=Usage(), stop_reason="tool_use" if calls else "end_turn", provider_kind="fixture", model_id=None)

    tools = FakeToolClient(errors={"get_plan": {"code": "INTERNAL", "message": "internal error", "retryable": True, "details": {}}})
    svc, tools = make_service(tools=tools, provider=Failing(ToolCall(id="p1", name="get_plan", arguments={"plan_id": PL})))
    final = svc.handle({"prompt": "Show plan " + PL, "stream": False}, auth(), SESSION)
    assert "get_plan failed: INTERNAL" in final["answer"]["narrative"]


def test_d10_brokerage_reason_is_surfaced():
    from finplan_agent.brokerage.gateway import BrokerageGatewayReader
    from finplan_agent.graph.brokerage import connection_plan

    reader = BrokerageGatewayReader.__new__(BrokerageGatewayReader)
    reader.client = FakeToolClient({"get_etf_connection_status": lambda a: {"authenticated": False, "status": "unavailable", "reason": "owner_authentication"}}, extra_tools=["get_etf_connection_status"])
    plan = connection_plan({"messages": [{"role": "user", "content": [{"text": "Is the ETF brokerage connection ready?"}]}]}, reader)
    evidence = plan["tool_results"][0]["result"]
    assert evidence["reason"] == "owner_authentication" and "owner_authentication" in evidence["message"]
    assert reader.client.calls == [("get_etf_connection_status", {})]


def test_d11_free_text_lists_are_rendered_deterministically_with_real_identifiers():
    versions = {"plan_id": PL, "plan_versions": [{"plan_version_id": PV, "status": "validated"}], "truncated": False}
    tools = FakeToolClient({"list_plan_versions": lambda a: versions})
    svc, tools = make_service(tools=tools, provider=PlanningProvider(ToolCall(id="p1", name="list_plan_versions", arguments={"plan_id": PL})))
    final = svc.handle({"prompt": "List the versions of plan " + PL, "stream": False}, auth(), SESSION)
    text = final["answer"]["narrative"]
    assert PV in text and "list_plan_versions: 1 plan_versions in this page." in text and final["answer"]["claim_check"]["passed"]


def test_d11_invented_identifiers_in_a_model_narrative_are_removed():
    class Inventing(PlanningProvider):
        def generate(self, request, on_token=None):
            self.calls += 1
            if request.purpose == "plan" and self.calls == 1:
                return GenerateResult(text="", tool_calls=(self.call,), usage=Usage(), stop_reason="tool_use", provider_kind="fixture", model_id=None)
            if request.purpose == "plan":
                return GenerateResult(text="", tool_calls=(), usage=Usage(), stop_reason="end_turn", provider_kind="fixture", model_id=None)
            return GenerateResult(text=f"Plan {PL} was published as pub_01N52QABCDEFGHJKMNPQRSTVWX.", tool_calls=(), usage=Usage(), stop_reason="end_turn", provider_kind="fixture", model_id=None)

    svc, tools = make_service(provider=Inventing(ToolCall(id="p1", name="get_plan", arguments={"plan_id": PL})))
    final = svc.handle({"prompt": "Show plan " + PL, "stream": False}, auth(), SESSION)
    cc = final["answer"]["claim_check"]
    assert not cc["passed"] and cc["removed_identifiers"] == ["pub_01N52QABCDEFGHJKMNPQRSTVWX"]
    assert "pub_01N52Q" not in final["answer"]["narrative"] and PL in final["answer"]["narrative"]


def test_tool_request_list_of_classical_analyses_renders_rows_and_brokerage_evidence_names_its_gateway():
    rows = [{"analysis_id": A, "analysis_kind": "recommendation", "created_at": "2026-10-18T13:45:00Z"}]
    tools = FakeToolClient({"list_classical_analyses": lambda a: {"analyses": rows, "next_token": None}}, extra_tools=["list_classical_analyses"])
    svc, tools = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry("list_classical_analyses", False, "reader")], environment="beta")
    final = svc.handle({"tool_request": {"name": "list_classical_analyses", "arguments": {"limit": 5}}, "stream": False}, auth(), SESSION)
    assert A in final["answer"]["narrative"] and "No compatible stored" not in final["answer"]["narrative"]
    from finplan_agent.graph.brokerage import connection_plan
    plan = connection_plan({"messages": [{"role": "user", "content": [{"text": "Is the Coinbase brokerage connection ready?"}]}]})
    assert plan["tool_results"][0]["gateway"] == "brokerage"


def test_declined_feedback_is_described_as_feedback():
    names = ["submit_portfolio_feedback"]
    tools = FakeToolClient({"submit_portfolio_feedback": lambda a: {"analysis_id": A}}, extra_tools=names)
    svc, tools = make_service(tools=tools, provider=NoProvider())
    svc.deps.catalog_loader = lambda: ToolCatalog([CatalogEntry(n, False, "reader") for n in names], environment="beta")
    svc.handle({"prompt": f"Record my feedback on traditional analysis {A}: too much turnover.", "stream": False}, auth(), SESSION)
    final = svc.handle({"action": "confirm", "approve": False, "stream": False}, auth(), SESSION)
    assert "feedback was not submitted" in final["answer"]["narrative"] and "experiment" not in final["answer"]["narrative"] and not tools.calls


def test_d13_beta_turn_token_cap_is_raised():
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[2] / "config" / "beta.json").read_text())["guard_defaults"]
    assert cfg["max_tokens_turn"] == 120000 and cfg["max_tokens_session"] >= cfg["max_tokens_turn"]
