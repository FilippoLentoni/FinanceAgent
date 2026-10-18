"""Read-only EC2 invocation of the deployed LangGraph recommendation workflow.

Uses the existing supported machine client, with its existing read-only grants.
The callback freezes the exact request before the HTTP invocation. Credentials
remain in memory and are never returned, logged, or included in evidence.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable

from .machine import REFS, _NoRedirect, machine_token
from .capture import check_public


def _session_and_timeout(session: Any, timeout: int) -> Any:
    if session is None:
        import boto3
        session = boto3.Session(region_name="us-east-2")
    if session.region_name != "us-east-2":
        raise ValueError("beta comparison is restricted to us-east-2")
    if not 1 <= timeout <= 420:
        raise ValueError("bounded invocation timeout required")
    return session


def _require_isolated_scenario(arguments: dict[str, Any]) -> None:
    # An explicit scenario prevents falling back to the user's saved portfolio.
    if not all(k in arguments for k in ("input_snapshot_id", "as_of", "holdings")) or "portfolio_id" in arguments:
        raise ValueError("explicit shared snapshot and isolated holdings required")


def invoke_tool(tool: str, arguments: dict[str, Any], prompt: str, capture_before: Callable[[dict[str, Any]], None], *,
                session: Any = None, timeout: int = 330) -> dict[str, Any]:
    """Ask the deployed agent to run ONE named model tool for an isolated scenario.

    The prompt states the request in words; ``tool_request`` names the exact MCP tool and
    arguments, so the agent's result can be checked against what was asked.
    """
    session = _session_and_timeout(session, timeout)
    _require_isolated_scenario(arguments)
    if not tool or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("tool name and prompt required")
    payload = {"prompt": prompt, "tool_request": {"name": tool, "arguments": arguments}, "stream": False, "capture_inputs": True}
    check_public(payload)
    final = _post(session, payload, capture_before, timeout,
                  {"numerical_policy": f"FinanceModel model run by the agent through MCP tool {tool}", "news": "Not consumed by this model tool"})
    if final.get("status") == "failed" and "error_code" in final and "answer" not in final:
        return final
    answer = final.get("answer") or {}
    final["requested_tool"] = tool
    final["tools_called"] = [e.get("tool") for e in answer.get("evidence") or []]
    recommendation = answer.get("recommendation")
    if isinstance(recommendation, dict) and isinstance(recommendation.get("target_weights"), list) and recommendation.get("cash_weight") is not None:
        final["target_weights"] = recommendation["target_weights"]
        final["cash_weight"] = recommendation["cash_weight"]
        final["served_strategy"] = recommendation.get("strategy")
    final["forecast"] = {"available": False, "status": "not_available", "reason": "A target allocation is not a portfolio value forecast"}
    return final


def invoke_graph(arguments: dict[str, Any], capture_before: Callable[[dict[str, Any]], None], *, session: Any = None,
                 timeout: int = 330) -> dict[str, Any]:
    session = _session_and_timeout(session, timeout)
    _require_isolated_scenario(arguments)
    payload = {"recommendation": arguments, "stream": False, "capture_inputs": True}
    check_public(payload)
    final = _post(session, payload, capture_before, timeout,
                  {"numerical_policy": "FinanceModel served policy via recommend_portfolio; identity verified against the returned artifact", "news": "News not consumed by the graph request unless the served policy explicitly reports news features"})
    if final.get("status") == "failed" and "error_code" in final and "answer" not in final:
        return final
    recommendation = (final.get("answer") or {}).get("recommendation")
    if isinstance(recommendation, dict) and isinstance(recommendation.get("target_weights"), list) and recommendation.get("cash_weight") is not None:
        final["target_weights"] = recommendation["target_weights"]
        final["cash_weight"] = recommendation["cash_weight"]
        final["allocation_origin"] = "FinanceModel served policy via LangGraph recommend_portfolio tool"
        final["served_strategy"] = recommendation.get("strategy")
        final["forecast"] = {"available": False, "status": "not_available", "reason": "A target allocation is not a portfolio value forecast"}
    else:
        final["policy_status"] = "unavailable_no_validated_numerical_policy_returned"
    if "captured_inputs" not in final:
        final["input_capture_status"] = "unavailable_deployed_release_requires_capture_support"
    return final


def _post(session: Any, payload: dict[str, Any], capture_before: Callable[[dict[str, Any]], None], timeout: int,
          notes: dict[str, Any]) -> dict[str, Any]:
    """Capture the exact payload, then send it to the deployed beta AgentCore Runtime."""
    ssm = session.client("ssm")
    opener = urllib.request.build_opener(_NoRedirect)
    token, sensitive = machine_token(ssm, session.client("secretsmanager"), "beta", opener)
    sensitive.append(token)
    runtime = ssm.get_parameter(Name=REFS["runtime"].format("beta"))["Parameter"]["Value"]
    sid = "modeling_comparison_" + uuid.uuid4().hex
    url = "https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/" + urllib.parse.quote(runtime, safe="") + "/invocations?qualifier=DEFAULT"
    capture_before({"framework": "langgraph", "runtime_session_id": sid, "payload": payload, **notes})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid})
    start = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as response:
            final = json.load(response)
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "error_code": "HOSTED_HTTP_" + str(exc.code), "session_id": sid}
    except (OSError, ValueError):
        return {"status": "failed", "error_code": "HOSTED_TRANSPORT_ERROR", "session_id": sid}
    if not isinstance(final, dict) or any(v and v in json.dumps(final) for v in sensitive):
        return {"status": "failed", "error_code": "HOSTED_RESPONSE_REJECTED", "session_id": sid}
    check_public(final)
    final["latency_seconds"] = time.monotonic() - start
    final["framework"] = "langgraph"
    usage = final.get("usage") or {}
    final["usage"] = {**usage, "cost_usd": usage.get("estimated_cost_usd"),
                      "cost_basis": "estimated_from_reported_provider_tokens_and_configured_rates" if usage.get("estimated_cost_usd") is not None else "not_available"}
    return final
