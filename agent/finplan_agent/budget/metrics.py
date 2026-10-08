"""Usage metrics (spec "Token and cost guards" usage reporting) and the month-to-date spend reader.

One usage record per completed turn, built in the CloudWatch Embedded Metric Format and published with
``PutMetricData`` by the Runtime (:func:`put_usage`; :func:`emit_usage` writes the same record as an EMF
log line for local runs). A metrics failure never fails the turn.

Each record carries two dimension sets in namespace ``FinPlan/FinanceAgent``:

* ``[Environment, ReleaseId, ProviderKind]`` for per-environment reporting;
* ``[BudgetCategory]`` (always ``bedrock_explanations``), shared by beta, gamma and prod, so ONE
  ``GetMetricData`` Sum over it is the month-to-date estimate across all environments in the single
  account (design D4 budget layer 2).

The record holds token counts, tool-call count and the estimated cost only: never prompts, responses,
identities or tokens.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from .guard import BUDGET_CATEGORY

__all__ = ["NAMESPACE", "COST_METRIC", "emf_record", "emit_usage", "put_usage", "CloudWatchSpendSource"]

NAMESPACE = "FinPlan/FinanceAgent"
COST_METRIC = "EstimatedCostUSD"


def emf_record(*, environment: str, release_id: str | None, provider_kind: str, model_id: str | None, usage: dict[str, Any], tool_calls: int, now_ms: int | None = None) -> dict[str, Any]:
    metrics = [
        {"Name": COST_METRIC, "Unit": "None"},
        {"Name": "InputTokens", "Unit": "Count"},
        {"Name": "OutputTokens", "Unit": "Count"},
        {"Name": "CacheReadTokens", "Unit": "Count"},
        {"Name": "CacheWriteTokens", "Unit": "Count"},
        {"Name": "ToolCalls", "Unit": "Count"},
    ]
    return {
        "_aws": {
            "Timestamp": now_ms if now_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Environment", "ReleaseId", "ProviderKind"], ["BudgetCategory"]], "Metrics": metrics}],
        },
        "Environment": environment,
        "ReleaseId": release_id or "unreleased",
        "ProviderKind": provider_kind,
        "BudgetCategory": BUDGET_CATEGORY,
        "ModelId": model_id or "none",
        COST_METRIC: float(usage.get("estimated_cost_usd", 0.0)),
        "InputTokens": int(usage.get("input_tokens", 0)),
        "OutputTokens": int(usage.get("output_tokens", 0)),
        "CacheReadTokens": int(usage.get("cache_read_tokens", 0)),
        "CacheWriteTokens": int(usage.get("cache_write_tokens", 0)),
        "ToolCalls": int(tool_calls),
    }


def emit_usage(record: dict[str, Any], stream: TextIO | None = None) -> None:
    """Write the EMF line (local runs and log-based reporting)."""
    out = stream or sys.stdout
    out.write(json.dumps(record, sort_keys=True) + "\n")
    out.flush()


def put_usage(cloudwatch: Any, record: dict[str, Any]) -> None:
    """Publish the record with ``PutMetricData`` (the Runtime path: it does not depend on log-based EMF
    extraction of the Runtime log group). The Runtime role needs ``cloudwatch:PutMetricData`` limited
    by the ``cloudwatch:namespace`` condition to :data:`NAMESPACE`, and ``cloudwatch:GetMetricData``."""
    ts = datetime.fromtimestamp(record["_aws"]["Timestamp"] / 1000, tz=UTC)
    spec = record["_aws"]["CloudWatchMetrics"][0]
    data = []
    for dims in spec["Dimensions"]:
        for m in spec["Metrics"]:
            data.append({"MetricName": m["Name"], "Dimensions": [{"Name": d, "Value": str(record[d])} for d in dims], "Timestamp": ts, "Value": float(record[m["Name"]]), "Unit": m["Unit"]})
    for i in range(0, len(data), 20):
        cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=data[i : i + 20])


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class CloudWatchSpendSource:
    """Month-to-date estimated spend from the usage metrics (cached briefly per process)."""

    def __init__(self, cloudwatch: Any, *, cache_seconds: float = 60.0, clock: Callable[[], datetime] | None = None) -> None:
        self._cw = cloudwatch
        self._cache_seconds = cache_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: tuple[float, float] | None = None
        self.local_unflushed_usd = 0.0

    def record_local(self, usd: float) -> None:
        """Spend of this process not yet visible in metrics (added to the next estimate)."""
        self.local_unflushed_usd += usd

    def month_to_date_usd(self) -> float:
        mono = time.monotonic()
        if self._cached is not None and mono - self._cached[0] < self._cache_seconds:
            return self._cached[1] + self.local_unflushed_usd
        now = self._clock()
        resp = self._cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "spend",
                    "MetricStat": {
                        "Metric": {"Namespace": NAMESPACE, "MetricName": COST_METRIC, "Dimensions": [{"Name": "BudgetCategory", "Value": BUDGET_CATEGORY}]},
                        "Period": 86400,
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=_month_start(now),
            EndTime=now + timedelta(minutes=1),
        )
        total = sum(float(v) for r in resp.get("MetricDataResults", []) for v in r.get("Values", []))
        self._cached = (mono, total)
        # Local spend is never subtracted once it reaches the metrics: the estimate may over-count
        # (conservative) but never under-counts because of metric lag.
        return total + self.local_unflushed_usd
