"""Start an on-demand recommendation run with the operator's own AWS credentials.

Invokes ``finplan-<env>-financeagent-recommendation-trigger`` synchronously (RequestResponse) with
``{"trigger": "on_demand", "portfolio_ids": [...]?, "methods": [...]?}`` and prints the run id, the
run status and each method's status and decision id. The run is read-only advisory work: nothing is
confirmed, traded or changed. Exit status: 0 completed/partial/not_due, 1 failed or invocation error,
2 refused (duplicate or cap_reached).

    python scripts/recommend_now.py --env beta [--portfolio-id pf_...] [--method ppo] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

ENVIRONMENTS = ("beta", "gamma", "prod")
METHODS = ("ppo", "min_variance", "mean_variance", "cvar")
READ_TIMEOUT_SECONDS = 960  # the trigger Lambda may run up to 900 s
_PF = re.compile(r"^pf_[0-7][0-9A-HJKMNP-TV-Z]{25}\Z")


def function_name(environment: str) -> str:
    return f"finplan-{environment}-financeagent-recommendation-trigger"


def build_payload(portfolio_ids: list[str] | None = None, methods: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"trigger": "on_demand"}
    if portfolio_ids:
        bad = [p for p in portfolio_ids if not _PF.match(p)]
        if bad:
            raise ValueError("not a saved portfolio id: " + ", ".join(bad))
        payload["portfolio_ids"] = list(dict.fromkeys(portfolio_ids))
    if methods:
        bad = [m for m in methods if m not in METHODS]
        if bad:
            raise ValueError("unknown method: " + ", ".join(bad))
        payload["methods"] = list(dict.fromkeys(methods))
    return payload


def lambda_client(region: str) -> Any:
    import boto3
    from botocore.config import Config

    # No retries: a retried synchronous invoke would start a second run.
    return boto3.session.Session(region_name=region).client(
        "lambda", region_name=region, config=Config(read_timeout=READ_TIMEOUT_SECONDS, connect_timeout=10, retries={"total_max_attempts": 1}))


def invoke(client: Any, environment: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.invoke(FunctionName=function_name(environment), InvocationType="RequestResponse", Payload=json.dumps(payload).encode())
    body = response["Payload"].read() if hasattr(response.get("Payload"), "read") else response.get("Payload") or b""
    try:
        doc = json.loads(body or b"{}")
    except json.JSONDecodeError:
        doc = {}
    if response.get("FunctionError"):
        return {"status": "failed", "error_code": "FUNCTION_ERROR", "error_type": str(doc.get("errorType", "Unhandled"))[:80] if isinstance(doc, dict) else "Unhandled"}
    return doc if isinstance(doc, dict) else {"status": "failed", "error_code": "INVALID_RESPONSE"}


def summarize(result: dict[str, Any]) -> str:
    lines = [f"run {result.get('run_id', 'not_recorded')}: {result.get('status', 'unknown')}"
             + (f" ({result['error_code']})" if result.get("error_code") else "")]
    for portfolio in result.get("portfolios") or []:
        lines.append(f"  {portfolio.get('portfolio_id')}: {portfolio.get('status', 'unknown')}")
        for method, entry in (portfolio.get("methods") or {}).items():
            entry = entry or {}
            detail = entry.get("decision_id") or entry.get("error_code") or ""
            lines.append(f"    {method:<14} {entry.get('status', 'unknown'):<10} {detail}".rstrip())
    return "\n".join(lines)


def exit_code(result: dict[str, Any]) -> int:
    status = result.get("status")
    if status in ("completed", "partial", "not_due"):
        return 0
    if status in ("duplicate", "cap_reached"):
        return 2
    return 1


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", choices=ENVIRONMENTS, default="beta")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--portfolio-id", action="append", dest="portfolio_ids", help="repeatable; default: the configured portfolios")
    parser.add_argument("--method", action="append", dest="methods", choices=METHODS, help="repeatable; default: all four")
    parser.add_argument("--json", action="store_true", help="print the full JSON response")
    args = parser.parse_args(argv)
    try:
        payload = build_payload(args.portfolio_ids, args.methods)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        result = invoke(client or lambda_client(args.region), args.env, payload)
    except Exception as exc:  # noqa: BLE001 - report the class only (no request echo)
        code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")
        print(f"invocation failed: {type(exc).__name__}" + (f" ({code})" if code else ""), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str) if args.json else summarize(result))
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
