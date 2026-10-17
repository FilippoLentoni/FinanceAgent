"""Actual boundary receipts are frozen, isolated, authorized, and retained on failure."""
import hashlib
import json

import pytest

from finplan_agent.core.errors import AgentError
from finplan_agent.modeling.capture import InputCapture, canonical
from finplan_agent.providers.base import GenerateRequest
from tests.fakes.agent import PL, SESSION, FakeToolClient, auth, make_service


def test_before_is_frozen_and_checksum_matches_actual_values():
    cap = InputCapture()
    supplied = {"covariance": [[1, 0.2], [0.2, 2]], "news": {"consumed": False}}
    row = cap.before("solver", supplied)
    supplied["covariance"][0][0] = 999
    assert row["supplied"]["covariance"][0][0] == 1
    assert row["checksum"] == "sha256:" + hashlib.sha256(canonical(row["supplied"])).hexdigest()


def test_failed_provider_preserves_exact_input_without_exception_text():
    class Failing:
        kind, model_id = "fixture", None
        def generate(self, request, on_token=None):
            raise RuntimeError("do not expose this transport error")
    cap = InputCapture()
    with pytest.raises(RuntimeError):
        cap.provider(Failing()).generate(GenerateRequest(purpose="plan", system="public", messages=[{"role": "user", "content": [{"text": "actual context"}]}], max_tokens=32))
    assert cap.entries[0]["supplied"]["messages"][0]["content"][0]["text"] == "actual context"
    assert cap.entries[0]["status"] == "failed"
    assert "transport error" not in json.dumps(cap.entries)


@pytest.mark.parametrize("payload", [{"access_token": "secret"}, {"message": "Bearer " + "x" * 40}])
def test_credentials_refused_before_capture(payload):
    cap = InputCapture()
    with pytest.raises(AgentError):
        cap.before("tool", payload)
    assert not cap.entries


def test_live_graph_boundary_capture_is_per_request_not_checkpointed():
    tools = FakeToolClient(results={"get_plan": lambda a: {"plan_id": a["plan_id"]}, "record_agent_activity": lambda a: {"activity_event_id": "ae-test", "checksum": "sha256:" + "a" * 64}}, extra_tools=["record_agent_activity"])
    svc, tools = make_service(tools=tools)
    request = {"tool_request": {"name": "get_plan", "arguments": {"plan_id": PL}}, "stream": False, "capture_inputs": True}
    machine = auth("comparison-machine", groups=(), extra={"client_id": "comparison-machine"})
    result = svc.handle(request, machine, SESSION)
    captures = result["captured_inputs"]
    actual = next(r for r in captures if r["boundary"] == "tool.call_tool")
    assert actual["supplied"]["arguments"] == next(args for name, args in tools.calls if name == "get_plan")
    assert tools.calls[0][0] == "record_agent_activity"
    assert actual["durable_receipt"]["activity_event_id"] == "ae-test"
    assert actual["output"]["result"]["plan_id"] == PL
    state = svc.graph.get_state({"configurable": {"thread_id": SESSION}})
    assert "captured_inputs" not in state.values
    assert "Authorization" not in json.dumps(captures)


def test_capture_cannot_be_enabled_by_regular_user_or_higher_environment():
    svc, tools = make_service()
    result = svc.handle({"prompt": "show capabilities", "stream": False, "capture_inputs": True}, auth(), SESSION)
    assert result["error"]["code"] == "FORBIDDEN" and not tools.calls
    svc, tools = make_service(environment="gamma")
    result = svc.handle({"prompt": "show capabilities", "stream": False, "capture_inputs": True}, auth("machine", groups=(), extra={"client_id": "machine"}), SESSION)
    assert result["error"]["code"] == "FORBIDDEN" and not tools.calls
