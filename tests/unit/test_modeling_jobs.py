"""Scheduling/restart semantics without sleeping or invoking a paid model."""
from datetime import UTC, datetime
import json

import pytest

from finplan_agent.modeling.jobs import atomic_json, missed_hours, run_job, slot_for


def test_independent_minute_halfhour_hour_slots_and_ny_calendar():
    at = datetime(2026, 10, 18, 0, 31, 42, tzinfo=UTC)
    assert slot_for("snapshot", at) == "2026-10-18T00:31:00Z"
    assert slot_for("research", at) == "2026-10-18T00:30:00Z"
    assert slot_for("hourly", at) == "2026-10-18T00:00:00Z"
    assert slot_for("eod", at) == "2026-10-17"


def test_restart_recovery_accounts_for_each_missing_hour_without_current_slot():
    assert missed_hours("2026-10-17T20:00:00Z", "2026-10-18T00:00:00Z") == [
        "2026-10-17T21:00:00Z", "2026-10-17T22:00:00Z", "2026-10-17T23:00:00Z"]
    assert missed_hours(None, "2026-10-18T00:00:00Z") == []
    with pytest.raises(RuntimeError):
        missed_hours("2026-10-01T00:00:00Z", "2026-10-18T00:00:00Z")


def test_snapshot_requires_real_codex_command_and_validates_file(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    cfg = {"project_root": str(tmp_path), "codex": "/codex"}
    calls = []
    def fake_exec(command, **kwargs):
        calls.append((command, kwargs))
        (tmp_path / "test-codex" / "snapshot-20261017T220000Z.empty").write_bytes(b"")
        (directory / "codex-final.txt").write_text("created")
    result = run_job(cfg, "snapshot", "2026-10-17T22:00:00Z", directory, execute_fn=fake_exec)
    assert calls[0][0][:2] == ["/codex", "exec"]
    assert calls[0][1]["cwd"] == str(tmp_path / "test-codex")
    assert result["bytes"] == 0 and result["invocation"] == "real_headless_codex"


def test_timer_success_without_codex_file_fails(tmp_path):
    with pytest.raises(RuntimeError):
        run_job({"project_root": str(tmp_path), "codex": "/codex"}, "snapshot", "2026-10-17T22:00:00Z", tmp_path, execute_fn=lambda *a, **k: None)


def test_atomic_health_state_does_not_lose_retry_slot(tmp_path):
    p = tmp_path / "status.json"
    atomic_json(p, {"slot": "fixed", "status": "running", "attempt": 1})
    interrupted = json.loads(p.read_text())
    atomic_json(p, {**interrupted, "status": "failed"})
    assert json.loads(p.read_text())["slot"] == "fixed"
    assert not p.with_suffix(".json.tmp").exists()
