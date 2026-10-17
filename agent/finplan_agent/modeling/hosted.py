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


def invoke_graph(arguments: dict[str, Any], capture_before: Callable[[dict[str, Any]], None], *, session: Any = None,
                 timeout: int = 330) -> dict[str, Any]:
    if session is None:
        import boto3
        session = boto3.Session(region_name="us-east-2")
    if session.region_name != "us-east-2":
        raise ValueError("beta comparison is restricted to us-east-2")
    if not 1 <= timeout <= 420:
        raise ValueError("bounded invocation timeout required")
    # An explicit scenario prevents falling back to the user's saved portfolio.
    if not all(k in arguments for k in ("input_snapshot_id", "as_of", "holdings")) or "portfolio_id" in arguments:
        raise ValueError("explicit shared snapshot and isolated holdings required")
    payload = {"recommendation": arguments, "stream": False, "capture_inputs": True}
    check_public(payload)
    ssm = session.client("ssm")
    opener = urllib.request.build_opener(_NoRedirect)
    token, sensitive = machine_token(ssm, session.client("secretsmanager"), "beta", opener)
    sensitive.append(token)
    runtime = ssm.get_parameter(Name=REFS["runtime"].format("beta"))["Parameter"]["Value"]
    sid = "modeling_comparison_" + uuid.uuid4().hex
    url = "https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/" + urllib.parse.quote(runtime, safe="") + "/invocations?qualifier=DEFAULT"
    capture_before({"framework": "langgraph", "runtime_session_id": sid, "payload": payload,
                    "numerical_policy": "served PPO via recommend_portfolio", "news": "News not consumed by the graph request unless the served policy explicitly reports news features"})
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
