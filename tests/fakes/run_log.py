"""A fake recommendation run-log table (DynamoDB resource ``Table`` subset: ``query``)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

PF = "pf_01JABCDEFGHJKMNPQRSTVWXYZ4"
PF_B = "pf_01JABCDEFGHJKMNPQRSTVWXYZ5"


def decision_id(n: int) -> str:
    return "pd_01JA2B3C4D5E6F7G8H9JKMN" + "PQRSTVWXYZ"[n % 10] + "PQRSTVWXYZ"[(n // 10) % 10] + "A"


def method(status: str = "issued", n: int = 0, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if status == "issued":
        out.update(decision_id=decision_id(n), completed_session="2026-10-16", formation_at="2026-10-16T13:00:00Z",
                   input_snapshot_id="ms_2026-10-16", news={"source_day": "2026-10-16", "checksum": "sha256:" + "a" * 64},
                   targets={"VTI": Decimal("0.4"), "BND": Decimal("0.3125"), "GLD": Decimal("0.1"), "VEA": Decimal("0.0875"), "TLT": Decimal("0.05")},
                   usage={"estimated_cost_usd": Decimal("0.0012"), "input_tokens": Decimal(1200), "output_tokens": Decimal(300)})
    out.update(extra)
    return out


def run_item(hour: int, *, portfolio: str = PF, trigger: str = "scheduled", status: str = "completed", methods: dict[str, Any] | None = None, env: str = "beta", day: int = 17) -> dict[str, Any]:
    started = f"2026-10-{day:02d}T{hour:02d}:00:05Z"
    run_id = f"sched-2026-10-{day:02d}T{hour:02d}:00:00Z" if trigger == "scheduled" else f"od-2026-10-{day:02d}T{hour:02d}-x{hour}"
    if methods is None:
        methods = {m: method(n=hour * 4 + i) for i, m in enumerate(("ppo", "min_variance", "mean_variance", "cvar"))}
    return {"run_id": run_id, "portfolio_id": portfolio, "env": env, "started_at": started, "started_key": f"{started}#{run_id}",
            "finished_at": started.replace(":05Z", ":50Z"), "trigger": trigger, "window": run_id[6:], "status": status,
            "requested_methods": list(methods), "methods": methods}


class FakeRunTable:
    def __init__(self, items: list[dict[str, Any]] | None = None, *, error: Exception | None = None) -> None:
        self.items = list(items or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        values = kwargs["ExpressionAttributeValues"]
        condition = kwargs["KeyConditionExpression"]
        if kwargs.get("IndexName"):
            assert kwargs["IndexName"] == "recent" and kwargs["ExpressionAttributeNames"] == {"#env": "env"}
            rows = [i for i in self.items if i.get("env") == values[":env"]]
            if "BETWEEN" in condition:
                rows = [i for i in rows if values[":lo"] <= i["started_key"] <= values[":hi"]]
            elif ">=" in condition:
                rows = [i for i in rows if i["started_key"] >= values[":lo"]]
            elif re.search(r"started_key <", condition):
                rows = [i for i in rows if i["started_key"] < values[":hi"]]
            rows.sort(key=lambda i: i["started_key"], reverse=kwargs.get("ScanIndexForward") is False)
        else:
            rows = sorted((i for i in self.items if i["run_id"] == values[":r"]), key=lambda i: i["portfolio_id"])
        start = int((kwargs.get("ExclusiveStartKey") or {}).get("pos", 0))
        limit = int(kwargs.get("Limit") or 1000)
        page = rows[start:start + limit]
        out: dict[str, Any] = {"Items": [dict(i) for i in page]}
        if start + limit < len(rows):
            out["LastEvaluatedKey"] = {"pos": start + limit}
        return out


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "denied"}}
