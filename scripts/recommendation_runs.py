"""Read the recommendation run log with the operator's own AWS credentials (read-only).

    python scripts/recommendation_runs.py [--env beta] latest [-n 5] [--method ppo] [--since 2026-10-15]
                                          [--until 2026-10-17] [--portfolio-id pf_...] [--trigger on_demand] [--json]
    python scripts/recommendation_runs.py [--env beta] get RUN_ID [--json]

The table name is resolved from SSM (``/finplan/<env>/financeagent/config/recommendation-runs-table``).
``latest`` queries the ``recent`` index newest first; ``get`` queries one run id. Dates are America/New_York
calendar days (the formation calendar). Only ``ssm:GetParameter`` and ``dynamodb:Query`` are used.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "agent") not in sys.path:
    sys.path.insert(0, str(ROOT / "agent"))
from finplan_agent.config.settings import SsmNames  # noqa: E402
from finplan_agent.graph.runs_query import DEFAULT_LIMIT, MAX_LIMIT, METHODS, day_bounds, get_run, latest_runs, render_runs  # noqa: E402

ENVIRONMENTS = ("beta", "gamma", "prod")


def resolve_table(session: Any, environment: str) -> Any:
    name = session.client("ssm").get_parameter(Name=SsmNames(environment).recommendation_runs_table_ref)["Parameter"]["Value"].strip()
    return session.resource("dynamodb").Table(name)


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from None


def _count(value: str) -> int:
    n = int(value)
    if not 1 <= n <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"expected 1..{MAX_LIMIT}")
    return n


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read-only recommendation run-log queries.")
    p.add_argument("--env", choices=ENVIRONMENTS, default="beta")
    p.add_argument("--region", default="us-east-2")
    sub = p.add_subparsers(dest="command", required=True)
    latest = sub.add_parser("latest", help="most recent runs, newest first")
    latest.add_argument("-n", type=_count, default=DEFAULT_LIMIT)
    latest.add_argument("--method", action="append", dest="methods", choices=METHODS)
    latest.add_argument("--since", type=_day, help="first New York day included")
    latest.add_argument("--until", type=_day, help="last New York day included")
    latest.add_argument("--portfolio-id")
    latest.add_argument("--trigger", choices=("scheduled", "on_demand"))
    latest.add_argument("--json", action="store_true")
    get = sub.add_parser("get", help="one run by id (all portfolios)")
    get.add_argument("run_id")
    get.add_argument("--json", action="store_true")
    return p


def run(args: argparse.Namespace, table: Any) -> tuple[str, int]:
    if args.command == "get":
        items = get_run(table, args.run_id)
        if not items:
            return f"run {args.run_id} not found", 1
        if args.json:
            return json.dumps(items, indent=2, sort_keys=True, default=str), 0
        text, _ = render_runs(items, {"limit": len(items)}, heading=f"Run {args.run_id}:")
        return text, 0
    lo = day_bounds(args.since)[0] if args.since else None
    hi = day_bounds(args.until)[1] if args.until else None
    if lo and hi and lo >= hi:
        return "--since must not be after --until", 2
    page = latest_runs(table, args.env, limit=args.n, methods=args.methods, since=lo, until=hi, portfolio_id=args.portfolio_id, trigger=args.trigger)
    if args.json:
        return json.dumps({"items": page.items, "more": page.more}, indent=2, sort_keys=True, default=str), 0
    period = None
    if args.since and args.until:
        period = f"from {args.since.isoformat()} to {args.until.isoformat()}"
    elif args.since:
        period = f"since {args.since.isoformat()}"
    elif args.until:
        period = f"until {args.until.isoformat()}"
    request = {"limit": args.n, "methods": args.methods, "portfolio_id": args.portfolio_id, "trigger": args.trigger, "period": period}
    text, _ = render_runs(page.items, request, more=page.more)
    return text, 0


def main(argv: list[str] | None = None, *, table: Any = None) -> int:
    args = parser().parse_args(argv)
    if table is None:
        import boto3

        table = resolve_table(boto3.session.Session(region_name=args.region), args.env)
    text, code = run(args, table)
    print(text, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
