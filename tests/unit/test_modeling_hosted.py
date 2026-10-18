"""Supported machine call captures exact public scenario before the network request."""
import io
import json

import pytest

from finplan_agent.modeling import hosted


class Session:
    region_name = "us-east-2"
    def client(self, name):
        return self
    def get_parameter(self, **kwargs):
        return {"Parameter": {"Value": "runtime-reference"}}


def scenario():
    return {"input_snapshot_id": "snap_original", "as_of": "2026-10-16",
            "holdings": {"weights": [], "cash_weight": 1, "portfolio_value": 100000, "high_watermark": 100000}}


def test_machine_call_exact_input_and_usage_are_public(monkeypatch):
    order = []
    class Opener:
        def open(self, request, timeout):
            order.append("invoke")
            payload = json.loads(request.data)
            assert payload["recommendation"] == scenario()
            assert payload["capture_inputs"] is True
            return io.BytesIO(json.dumps({"status": "completed", "answer": {"recommendation": {
                "target_weights": [{"instrument_id": "AAPL", "weight": 0.4}], "cash_weight": 0.6, "strategy": "ppo"}},
                "captured_inputs": [{"boundary": "tool.call_tool", "supplied": {"arguments": scenario()}}],
                "usage": {"estimated_cost_usd": 0.0001}}).encode())
    monkeypatch.setattr(hosted.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(hosted, "machine_token", lambda *a: ("private-test-token", ["private-test-secret"]))
    captured = []
    def before(payload):
        order.append("capture")
        captured.append(payload)
    result = hosted.invoke_graph(scenario(), before, session=Session())
    assert order == ["capture", "invoke"]
    assert captured[0]["payload"]["recommendation"] == scenario()
    assert result["target_weights"][0]["weight"] == 0.4
    assert result["served_strategy"] == "ppo" and result["framework"] == "langgraph"
    assert result["usage"]["cost_usd"] == 0.0001
    assert result["usage"]["cost_basis"].startswith("estimated")
    assert "private-test" not in json.dumps([captured, result])


def test_cannot_fall_back_to_saved_user_book():
    with pytest.raises(ValueError, match="explicit shared"):
        hosted.invoke_graph({"portfolio_id": "user-book"}, lambda p: None, session=Session())


def test_capture_failure_prevents_network_inference(monkeypatch):
    class Opener:
        def open(self, *a, **k):
            raise AssertionError("must not invoke without durable capture")
    monkeypatch.setattr(hosted.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(hosted, "machine_token", lambda *a: ("private-test-token", []))
    def fail(payload):
        raise RuntimeError("capture_failed")
    with pytest.raises(RuntimeError, match="capture_failed"):
        hosted.invoke_graph(scenario(), fail, session=Session())


def test_tool_call_sends_named_tool_and_reports_what_the_agent_ran(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            assert payload["tool_request"] == {"name": "recommend_classical_portfolio", "arguments": {**scenario(), "algorithm": "cvar"}}
            assert payload["prompt"] == "Give me a portfolio recommendation using model cvar."
            return io.BytesIO(json.dumps({"status": "completed", "answer": {
                "evidence": [{"tool": "recommend_classical_portfolio", "ok": True}],
                "recommendation": {"target_weights": [{"instrument_id": "VOO", "weight": 0.6}], "cash_weight": 0.4, "strategy": "cvar"}}}).encode())
    monkeypatch.setattr(hosted.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(hosted, "machine_token", lambda *a: ("private-test-token", []))
    captured = []
    result = hosted.invoke_tool("recommend_classical_portfolio", {**scenario(), "algorithm": "cvar"},
                                "Give me a portfolio recommendation using model cvar.", captured.append, session=Session())
    assert captured[0]["payload"]["tool_request"]["name"] == "recommend_classical_portfolio"
    assert result["requested_tool"] == "recommend_classical_portfolio"
    assert result["tools_called"] == ["recommend_classical_portfolio"]
    assert result["target_weights"] == [{"instrument_id": "VOO", "weight": 0.6}] and result["served_strategy"] == "cvar"


def test_tool_call_cannot_fall_back_to_saved_user_book():
    with pytest.raises(ValueError, match="explicit shared"):
        hosted.invoke_tool("recommend_portfolio", {"portfolio_id": "user-book"}, "x", lambda p: None, session=Session())
