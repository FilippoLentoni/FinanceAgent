"""Deployed beta/gamma agent checks with REAL payloads (FA-PL-04 agent part, FA-RT-01/02/03, FA-SS-02,
FA-PRV-01 fixture in beta). Run by the stage runner with ``FINPLAN_TARGET_ENV``; skipped offline."""

from __future__ import annotations

from finplan_agent.core.ids import new_session_id
from tests.deployed import deployed, requires_deployed

pytestmark = requires_deployed


def test_describe_reports_langgraph_release_and_provider():
    env = deployed()
    status, doc = env.invoke_agent({"action": "describe"}, new_session_id())
    assert status == 200 and doc["framework"] == "langgraph" and doc["environment"] == env.env
    assert doc["release_id"].startswith("rel_") and doc["contract_version"].startswith("1.")
    if env.env == "beta":
        assert doc["provider"]["kind"] == "bedrock"
    assert doc["streaming"]["websocket"] is False


def test_agent_answers_through_the_gateway_with_streamed_events():
    env = deployed()
    sid = new_session_id()
    status, events = env.invoke_agent({"prompt": "Which capabilities do you have?"}, sid)
    assert status == 200 and events[-1]["type"] == "final", events[-3:]
    final = events[-1]
    assert final["session_id"] == sid and final["status"] == "completed", final.get("error")
    assert any(e["type"] == "tool_call" and e["tool"] == "describe_capabilities" for e in events)
    assert final["answer"]["evidence"][0]["ok"], final["answer"]["evidence"]


def test_unauthenticated_invocation_is_rejected_by_the_runtime():
    env = deployed()
    status, _ = env.invoke_agent({"action": "describe"}, new_session_id(), token="not-a-token")
    assert status in (401, 403)


def test_gateway_lists_tools_for_the_ci_principal():
    names = {t.name for t in deployed().gateway().list_tools()}
    assert "describe_capabilities" in names


def test_gateway_invalid_holdings_are_reported_as_nonretryable_validation():
    result = deployed().gateway().call_tool("recommend_portfolio", {"holdings": {"weights": []}})
    assert not result.ok and result.error["code"] == "VALIDATION_FAILED"
    assert result.error["retryable"] is False


def test_saved_portfolio_why_replays_through_mcp_and_exports_the_applied_skill():
    import pytest

    env = deployed()
    if env.env != "beta":
        pytest.skip("Saved paper book is initialized explicitly in beta only")
    sid = new_session_id()
    code, first = env.invoke_agent({"prompt": "What is your daily portfolio recommendation?", "stream": True}, sid)
    assert code == 200 and first[-1]["status"] == "completed", first[-1]
    rec = first[-1]["answer"]["recommendation"]
    assert rec and first[-1]["usage"]["tool_calls"] == 1
    code, second = env.invoke_agent({"prompt": "Why is this your recommendation?", "stream": True}, sid)
    final = second[-1]
    assert code == 200 and final["status"] == "completed", final
    assert [e["tool"] for e in second if e.get("type") == "tool_call"] == ["recommend_portfolio", "query_market_data"]
    assert final["answer"]["recommendation"] == rec
    assert final["answer"]["claim_check"]["passed"] and not final["answer"]["claim_check"]["removed_figures"]
    assert final["usage"]["invocations"] == 0
    skill = next(s for s in final["answer"]["skills_used"] if s["name"] == "recommend-portfolio")
    remote = next(t for t in env.gateway().list_tools() if t.name == "recommend_portfolio")
    assert skill["instructions_checksum"] in remote.description
    assert f"recommend-portfolio@{skill['version']}" in remote.description
