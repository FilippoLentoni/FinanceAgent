"""AgentCore Runtime HTTP contract (FA-RT-02, FA-RT-03; task 2.2, 2.3) through the real ASGI app
(BedrockAgentCoreApp) with Starlette's in-process test client."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from finplan_agent.runtime.app import create_app
from tests.fakes.agent import PV, SESSION, auth, make_service

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


@pytest.fixture
def client():
    svc, tools = make_service()
    app = create_app(svc)
    with TestClient(app) as c:
        c.tools = tools
        yield c


def _sse(body: str) -> list[dict]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def test_ping_reports_healthy(client):
    r = client.get("/ping")
    assert r.status_code == 200 and r.json()["status"] in ("Healthy", "HealthyBusy")


def test_streamed_invocation_is_sse_with_final_event(client):
    r = client.post("/invocations", json={"prompt": f"Show me plan version {PV}"}, headers={**auth(), SESSION_HEADER: SESSION})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse(r.text)
    assert events[0]["type"] == "progress" and events[-1]["type"] == "final"
    assert events[-1]["session_id"] == SESSION and events[-1]["status"] == "completed"


def test_non_streamed_invocation_is_json(client):
    r = client.post("/invocations", json={"prompt": f"Show me plan version {PV}", "stream": False}, headers={**auth(), SESSION_HEADER: SESSION})
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["type"] == "final" and r.json()["answer"]["evidence"][0]["tool"] == "get_plan_version"


def test_describe(client):
    r = client.post("/invocations", json={"action": "describe"}, headers=auth())
    assert r.json()["framework"] == "langgraph"


def test_missing_token_is_refused_without_tool_calls(client):
    r = client.post("/invocations", json={"prompt": f"Show {PV}", "stream": False}, headers={SESSION_HEADER: SESSION})
    assert r.json()["error"]["code"] == "UNAUTHORIZED" and client.tools.calls == []


def test_websocket_is_refused_in_phase_1(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={SESSION_HEADER: SESSION}) as ws:
            ws.receive_text()
    # HTTP invocation remains available
    assert client.post("/invocations", json={"action": "describe"}, headers=auth()).status_code == 200


def test_errors_never_leak_stack_traces(client, monkeypatch):
    svc = client.app.state.agent_service

    def boom(*a, **k):
        raise RuntimeError("secret internal detail " + "a" + "rn:aws:iam::" + "1" * 12 + ":role/x")

    monkeypatch.setattr(svc.graph, "stream", boom)
    r = client.post("/invocations", json={"prompt": "hello"}, headers={**auth(), SESSION_HEADER: SESSION})
    final = _sse(r.text)[-1]
    assert final["error"]["code"] == "INTERNAL" and "secret" not in r.text and "1" * 12 not in r.text
