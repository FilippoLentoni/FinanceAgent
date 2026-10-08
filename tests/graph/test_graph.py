"""Graph tests with fake tools and the fixture provider (FA-RT-01/02/04/05/06, FA-SS-01/02/03/05,
FA-PRV-05, FA-PRV-14)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from finplan_agent.session.store import InMemorySessionOwnership
from tests.fakes.agent import CHECKSUM, PL, PV, RUN, SESSION, SESSION_B, FakeToolClient, auth, fake_jwt, make_service, run
from tests.harness import BEDROCK_ATTEMPTS


def _final(events):
    finals = [e for e in events if e.get("type") == "final"]
    assert len(finals) == 1 and events[-1] is finals[0]
    return finals[0]


def _strip_ids(final):
    doc = json.loads(json.dumps(final))
    doc.pop("session_id")
    doc.pop("correlation_id")
    return json.dumps(doc, sort_keys=True)


# ------------------------------------------------------------------ FA-RT-01 / describe
def test_graph_nodes_are_the_designed_langgraph_pipeline():
    from finplan_agent.graph.build import NODES, build_graph

    g = build_graph(InMemorySaver())
    assert [n for n in g.get_graph().nodes if not n.startswith("__")] == list(NODES)
    assert type(g).__module__.startswith("langgraph.")


def test_describe_reports_framework_release_contract_and_provider():
    svc, _ = make_service()
    d = svc.handle({"action": "describe"}, auth(), None)
    assert d["framework"] == "langgraph" and d["release_id"].startswith("rel_")
    assert d["contract_version"] == json.loads((Path(__file__).resolve().parents[2] / "contracts-pin.json").read_text())["version"] and d["provider"]["kind"] == "fixture"
    assert d["streaming"] == {"http": True, "websocket": False}
    assert d["skills"] and d["environment"] == "beta"


# ------------------------------------------------------------------ FA-RT-02
def test_stream_event_order_and_final_ids():
    svc, tools = make_service()
    events = run(svc, {"prompt": f"Show me plan version {PV}"})
    types = [e["type"] for e in events]
    assert types[0] == "progress" and types[-1] == "final"
    first = {t: types.index(t) for t in ("tool_call", "tool_result_summary", "token", "final")}
    assert first["tool_call"] < first["tool_result_summary"] < first["token"] < first["final"]
    final = _final(events)
    assert final["session_id"] == SESSION and re.match(r"^corr-[0-9a-f]{32}$", final["correlation_id"])
    assert final["status"] == "completed" and tools.calls == [("get_plan_version", {"plan_version_id": PV})]
    assert CHECKSUM in final["answer"]["narrative"] and final["answer"]["claim_check"]["passed"]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == final["answer"]["narrative"]


def test_streamed_and_non_streamed_final_payloads_are_identical():
    a, _ = make_service()
    b, _ = make_service()
    streamed = _final(run(a, {"prompt": f"Show me plan version {PV}"}, session=SESSION))
    plain = run(b, {"prompt": f"Show me plan version {PV}", "stream": False}, session=SESSION_B)
    assert isinstance(plain, dict) and plain["type"] == "final"
    assert _strip_ids(streamed) == _strip_ids(plain)


def test_invalid_requests_return_contract_errors():
    svc, _ = make_service()
    out = svc.handle({"prompt": ""}, auth(), SESSION)
    final = _final(list(out))
    assert final["status"] == "failed" and final["error"]["code"] == "VALIDATION_FAILED" and final["error"]["details"]["pointer"] == "/prompt"
    bad_session = svc.handle({"prompt": "hi", "stream": False}, auth(), "short")
    assert bad_session["error"]["code"] == "VALIDATION_FAILED"
    unknown = svc.handle({"action": "trade", "stream": False}, auth(), SESSION)
    assert unknown["error"]["code"] == "VALIDATION_FAILED"


def test_unauthenticated_request_runs_no_graph_step():
    svc, tools = make_service()
    out = svc.handle({"prompt": f"Show {PV}", "stream": False}, {}, SESSION)
    assert out["error"]["code"] == "UNAUTHORIZED" and tools.calls == []


# ------------------------------------------------------------------ FA-RT-04
def test_long_running_job_ends_turn_in_progress_with_run_id():
    svc, tools = make_service()
    final = run(svc, {"prompt": f"What is the status of {RUN}?", "stream": False})
    assert final["status"] == "in_progress"
    assert final["in_progress"] == {"run_id": RUN, "state": "running", "tool": "get_job_status"}
    assert tools.calls == [("get_job_status", {"run_id": RUN})]


# ------------------------------------------------------------------ FA-RT-05
def _publish(svc):
    return run(svc, {"prompt": "Publish it", "tool_request": {"name": "publish_plan_version", "arguments": {"plan_id": PL, "plan_version_id": PV, "expected_revision": 3}}, "stream": False})


def test_state_changing_tool_requires_confirmation_then_runs_once_with_key():
    svc, tools = make_service()
    first = _publish(svc)
    assert first["status"] == "awaiting_confirmation" and tools.calls == []
    call = first["confirmation"]["calls"][0]
    assert call["tool"] == "publish_plan_version" and re.match(r"^fa-[0-9a-f]{48}$", call["idempotency_key"])
    assert call["arguments"]["idempotency_key"] == call["idempotency_key"]
    blocked = run(svc, {"prompt": "something else", "stream": False})
    assert blocked["error"]["code"] == "PRECONDITION_FAILED"
    done = run(svc, {"action": "confirm", "approve": True, "stream": False})
    assert done["status"] == "completed"
    assert tools.calls == [("publish_plan_version", call["arguments"])]
    assert "pub_01JABCDEFGHJKMNPQRSTVWXYZ4" in done["answer"]["narrative"]


def test_declined_confirmation_calls_nothing_and_is_recorded():
    svc, tools = make_service()
    _publish(svc)
    done = run(svc, {"action": "confirm", "approve": False, "stream": False})
    assert tools.calls == [] and done["status"] == "completed"
    assert done["declined"][0]["calls"][0]["tool"] == "publish_plan_version"
    state = svc.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    assert state["declined"] and state["confirmation"]["decision"] == "declined"


def test_confirm_without_pending_interrupt_is_precondition_failed():
    svc, _ = make_service()
    out = run(svc, {"action": "confirm", "approve": True, "stream": False})
    assert out["error"]["code"] == "PRECONDITION_FAILED"


@pytest.mark.parametrize(
    "prompt,kind",
    [
        ("Please approve the paid GPU job run_X", "paid_job_approval"),
        ("Raise my budget to 100 dollars", "paid_job_approval"),
        ("Place a live order to buy 10 shares of SPY", "live_financial_action"),
        ("Connect my brokerage account", "live_financial_action"),
        ("Relax my risk tolerance so the plan is feasible", "risk_preference_change"),
    ],
)
def test_refusals_call_no_tool_and_no_model(prompt, kind):
    svc, tools = make_service()
    final = run(svc, {"prompt": prompt, "stream": False})
    assert final["status"] == "refused" and final["error"]["code"] == "OPERATION_NOT_PERMITTED"
    assert final["error"]["details"]["kind"] == kind and tools.calls == []
    assert svc.deps.provider.calls == 0


def test_live_mode_argument_is_refused_before_the_tool():
    svc, tools = make_service()
    final = run(svc, {"prompt": "Run the query", "tool_request": {"name": "query_market_data", "arguments": {"mode": "live"}}, "stream": False})
    assert tools.calls == []
    assert final["answer"]["evidence"][0]["error"]["code"] == "OPERATION_NOT_PERMITTED"


def test_denied_tools_are_never_offered_or_called():
    tools = FakeToolClient(extra_tools=["place_live_order"])
    svc, _ = make_service(tools=tools)
    final = run(svc, {"prompt": "do it", "tool_request": {"name": "place_live_order", "arguments": {}}, "stream": False})
    assert tools.calls == [] and final["answer"]["evidence"] == []


# ------------------------------------------------------------------ FA-SS-01 / FA-SS-02 / FA-SS-03 / FA-SS-05
def _actor(sub="user-a"):
    from finplan_agent.session.identity import caller_from_headers

    return caller_from_headers(auth(sub), "beta").actor_id


def test_session_resumes_after_process_recycle_with_shared_store():
    saver, owners = InMemorySaver(), InMemorySessionOwnership()
    a, _ = make_service(checkpointer=saver, ownership=owners)
    run(a, {"prompt": f"Show me plan version {PV}", "stream": False})
    b, tools = make_service(checkpointer=saver, ownership=owners)  # new process, same Memory
    final = run(b, {"prompt": f"And again {PV}", "stream": False})
    state = b.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    assert state["turn"] == 2 and final["status"] == "completed"
    texts = [blk.get("text") for m in state["messages"] for blk in m["content"] if "text" in blk]
    assert any(PV in (t or "") for t in texts[:1])
    # figures are re-read through the tool, never taken from the checkpoint (FA-SS-03)
    assert tools.calls == [("get_plan_version", {"plan_version_id": PV})]


def test_pending_confirmation_survives_recycle():
    saver, owners = InMemorySaver(), InMemorySessionOwnership()
    a, _ = make_service(checkpointer=saver, ownership=owners)
    _publish(a)
    b, tools = make_service(checkpointer=saver, ownership=owners)
    done = run(b, {"action": "confirm", "approve": True, "stream": False})
    assert done["status"] == "completed" and [c[0] for c in tools.calls] == ["publish_plan_version"]


def test_other_caller_gets_forbidden_and_reads_nothing():
    svc, tools = make_service()
    run(svc, {"prompt": f"Show me plan version {PV}", "stream": False})
    before = svc.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    out = svc.handle({"prompt": "what did they ask?", "stream": False}, auth("user-b"), SESSION)
    assert out["error"]["code"] == "FORBIDDEN" and out["answer"] is None
    after = svc.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    assert after == before and len(tools.calls) == 1


def test_same_sub_in_another_environment_is_another_actor():
    from finplan_agent.session.identity import actor_id_for

    assert actor_id_for("beta", "iss-b", "u") != actor_id_for("gamma", "iss-g", "u")


def test_explicit_deletion_starts_a_new_session():
    svc, _ = make_service()
    run(svc, {"prompt": f"Show me plan version {PV}", "stream": False})
    gone = svc.handle({"action": "delete_session", "stream": False}, auth(), SESSION)
    assert gone["type"] == "deleted"
    run(svc, {"prompt": "hello", "stream": False})
    state = svc.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    assert state["turn"] == 1


def test_checkpoints_hold_no_token_or_secret(tmp_path):
    saver = InMemorySaver()
    svc, _ = make_service(checkpointer=saver)
    token = fake_jwt("user-a")
    run(svc, {"prompt": f"Show me plan version {PV}", "stream": False}, headers={"Authorization": f"Bearer {token}"})
    blob = repr(saver.storage) + repr(saver.writes) + repr(getattr(saver, "blobs", {}))
    assert token not in blob and "user-a" not in blob and "Bearer" not in blob
    from finplan_contracts.leak_scan import scan_text

    # LangGraph checkpoint IDs are UUIDs whose last group can be 12 decimal digits; they are not
    # account IDs (the scan would flake on roughly one run in ten without this)
    blob = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", blob)
    assert scan_text(blob, "checkpoint") == []


def test_tool_results_are_compacted_in_checkpoints():
    big = {"get_plan_version": lambda a: {"plan_version_id": PV, "checksum": CHECKSUM, "rows": [{"i": i, "pad": "x" * 50} for i in range(500)]}}
    svc, _ = make_service(tools=FakeToolClient(big))
    run(svc, {"prompt": f"Show me plan version {PV}", "stream": False})
    state = svc.graph.get_state({"configurable": {"thread_id": SESSION, "actor_id": _actor()}}).values
    kept = state["tool_results"][0]["result"]
    assert kept["compacted"] is True and kept["rows_count"] == 500 and kept["checksum"] == CHECKSUM


# ------------------------------------------------------------------ tool errors, caps, CI guard
def test_tool_error_is_reported_without_failing_the_turn():
    err = {"code": "DEPENDENCY_UNAVAILABLE", "message": "FinanceModel has no release in beta", "retryable": True, "details": {}, "correlation_id": "corr-12345678", "contract_version": "1.0.0"}
    svc, _ = make_service(tools=FakeToolClient(errors={"get_job_status": err}))
    final = run(svc, {"prompt": f"status of {RUN}", "stream": False})
    assert final["status"] == "completed"
    assert final["answer"]["evidence"][0]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "DEPENDENCY_UNAVAILABLE" in final["answer"]["narrative"]


def test_tool_call_cap_returns_budget_exceeded():
    from tests.fakes.agent import fixture_provider_config

    svc, tools = make_service(provider_config=fixture_provider_config(max_tool_calls_turn=1))
    final = run(svc, {"prompt": f"compare {PV} and {RUN}", "stream": False})
    assert tools.calls == [] and final["error"]["code"] == "BUDGET_EXCEEDED"
    assert final["error"]["details"]["limit"] == "max_tool_calls_turn"


def test_usage_metric_per_turn():
    usage = []
    svc, _ = make_service(usage=usage)
    run(svc, {"prompt": f"Show me plan version {PV}", "stream": False})
    assert len(usage) == 1 and usage[0]["provider_kind"] == "fixture" and usage[0]["tool_calls"] == 1
    assert usage[0]["estimated_cost_usd"] == 0.0


def test_ci_runs_make_zero_bedrock_calls():
    assert BEDROCK_ATTEMPTS == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Why did the plan sell 10% of SPY last month?",
        "What if I lowered my risk tolerance, how would the allocation change?",
        "Explain the trade-off between turnover and tracking error.",
        "Was the GPU job approved by someone?",
        "Why did they raise the budget allocation?",
    ],
)
def test_research_questions_and_hypotheticals_are_not_refused(prompt):
    from finplan_agent.graph.policy import classify_request

    assert classify_request(prompt) is None


def test_crash_mid_tool_call_resumes_with_the_same_idempotency_key():
    saver, owners = InMemorySaver(), InMemorySessionOwnership()

    class Crashing(FakeToolClient):
        def call_tool(self, name, arguments):
            super().call_tool(name, arguments)
            raise RuntimeError("process recycled")

    crashing = Crashing()
    a, _ = make_service(checkpointer=saver, ownership=owners, tools=crashing)
    pending = _publish(a)["confirmation"]["calls"][0]
    failed = run(a, {"action": "confirm", "approve": True, "stream": False})
    assert failed["status"] == "failed" and failed["error"]["code"] == "INTERNAL"
    b, tools = make_service(checkpointer=saver, ownership=owners)  # recycled process
    final = run(b, {"prompt": "thanks", "stream": False})
    assert tools.calls[0] == ("publish_plan_version", pending["arguments"])
    assert crashing.calls[0][1]["idempotency_key"] == tools.calls[0][1]["idempotency_key"] == pending["idempotency_key"]
    assert final["status"] == "completed"
