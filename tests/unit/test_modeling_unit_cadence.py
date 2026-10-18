"""Generated systemd start limits must admit the requested recurring cadence."""
from configparser import ConfigParser
from pathlib import Path

import pytest

from scripts.install_modeling_jobs import units
from finplan_agent.modeling import jobs


@pytest.mark.parametrize("job,period", [("snapshot", 60), ("research", 21600), ("hourly", 3600), ("eod", 86400), ("weekly", 86400)])
def test_start_limiter_allows_consecutive_calendar_runs_and_bounded_retry(job, period, tmp_path):
    cfg = {"runtime_pythonpath": "/runtime/src", "storage_pythonpath": "/platform/src",
           "scratch_dir": str(tmp_path), "runtime_python": "/runtime/.venv/bin/python",
           "source_commits": {k: "a" * 40 for k in ("financemodel", "financialplanning", "financeagent")}}
    service, _ = units(job, cfg, tmp_path / "config.json", Path(cfg["runtime_python"]))
    parsed = ConfigParser(strict=False, interpolation=None)
    parsed.read_string(service)
    interval = parsed.getint("Unit", "StartLimitIntervalSec")
    burst = parsed.getint("Unit", "StartLimitBurst")
    # Exercise an hour's real calendar starts plus a retry after a first failure.
    # The former one-hour/three-start snapshot limit rejects the fourth minute.
    starts = sorted({0, 30, *range(0, max(3601, period * 5 + 1), period)})
    window_start, admitted = None, 0
    for at in starts:
        if window_start is None or at - window_start >= interval:
            window_start, admitted = at, 0
        admitted += 1
        assert admitted <= burst, f"{job} calendar activation at{at}s would be rate limited"
    assert burst == 3


def test_weekly_service_admits_both_bounded_report_windows(tmp_path, monkeypatch):
    cfg = {"runtime_pythonpath": "/runtime/src", "storage_pythonpath": "/platform/src",
           "scratch_dir": str(tmp_path), "runtime_python": "/runtime/.venv/bin/python",
           "runtime_root": str(tmp_path), "project_root": str(tmp_path), "experiment_id": "isolated",
           "source_commits": {k: "a" * 40 for k in ("financemodel", "financialplanning", "financeagent")},
           "platform_root": str(tmp_path / "platform"),
           "commands": {"weekly": [["/runtime/.venv/bin/python", str(tmp_path / "platform/scripts/modeling_report.py"), "--weekly", "--closed-week"], ["/runtime/.venv/bin/python", str(tmp_path / "platform/scripts/modeling_report.py"), "--weekly"]]}}
    elapsed = [0.0]
    calls = []
    monkeypatch.setattr(jobs.time, "monotonic", lambda: elapsed[0])

    def bounded_report(command, **kwargs):
        assert kwargs["timeout"] == 1500
        calls.append("closed" if "--closed-week" in command else "current")
        elapsed[0] += 1499

    result = jobs.run_job(cfg, "weekly", "2026-10-17", tmp_path, execute_fn=bounded_report)
    service, _ = units("weekly", cfg, tmp_path / "config.json", Path(cfg["runtime_python"]))
    parsed = ConfigParser(strict=False, interpolation=None)
    parsed.read_string(service)
    assert elapsed[0] + 50 <= parsed.getint("Service", "TimeoutStartSec")
    assert calls == ["closed", "current"]
    assert all(r["status"] == "succeeded" for r in result["commands"])
