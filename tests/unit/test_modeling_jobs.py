"""Scheduling/restart semantics without sleeping or invoking a paid model."""
from datetime import UTC, datetime
import json

import pytest

from finplan_agent.modeling.jobs import atomic_json, missed_hours, run_job, slot_for
from finplan_agent.modeling import jobs


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


def test_failed_slot_retries_then_duplicate_is_a_noop(tmp_path, monkeypatch):
    cfg = {"environment": "beta", "region": "us-east-2", "state_dir": str(tmp_path / "state")}
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    calls = []
    def worker(*args):
        calls.append(args[2])
        if len(calls) == 1:
            raise RuntimeError("simulated process interruption")
        return {"snapshot": "verified"}
    monkeypatch.setattr(jobs, "run_job", worker)
    args = ["snapshot", "--config", str(config), "--slot", "2026-10-17T22:00:00Z"]
    assert jobs.main(args) == 1
    assert jobs.main(args) == 0
    assert jobs.main(args) == 0
    assert calls == ["2026-10-17T22:00:00Z"] * 2
    record = json.loads((tmp_path / "state/snapshot/2026-10-17T220000Z/status.json").read_text())
    assert record["attempt"] == 2 and record["status"] == "succeeded"


def test_actual_restart_path_accounts_each_cohort_without_historical_inference(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    atomic_json(state / "hourly-last-completed.json", {"slot": "2026-10-17T22:00:00Z"})
    cfg = {"environment": "beta", "region": "us-east-2", "state_dir": str(state), "experiment_id": "test", "runtime_root": str(tmp_path),
           "commands": {"hourly": [["python", "-m", "finplan_model.forward.hourly", "--slot", "{slot}"],
                                    ["python", "-m", "finplan_model.forward.hourly", "--cohort", "eight_assets", "--slot", "{slot}"],
                                    ["python", "-m", "finplan_model.forward.crypto_hourly", "--slot", "{slot}"],
                                    ["python", "modeling_report.py"]]}}
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    calls = []
    monkeypatch.setattr(jobs, "execute", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(jobs, "run_job", lambda *a: {"completed": True})
    assert jobs.main(["hourly", "--config", str(config), "--slot", "2026-10-18T00:00:00Z"]) == 0
    assert len(calls) == 3
    assert all("--account-missed" in c and "2026-10-17T23:00:00Z" in c for c in calls)
    assert json.loads((state / "hourly-last-completed.json").read_text())["slot"] == "2026-10-18T00:00:00Z"


def test_research_coordinates_owned_collection_then_review_and_requires_matching_receipt(tmp_path):
    prompt = tmp_path / "research.prompt"
    prompt.write_text("{{runtime_python}} {{platform_root}}/scripts/collect_modeling_sources.py --kind research --slot '{{slot}}' --experiment '{{experiment}}' --output '{{output}}'\n{{runtime_python}} -m finplan_model.forward.research cycle --slot '{{slot}}' --experiment '{{experiment}}' --output '{{output}}'")
    cfg = {"project_root": str(tmp_path), "runtime_root": str(tmp_path), "runtime_python": "/release/bin/python",
           "platform_root": "/release/platform", "research_prompt": str(prompt), "codex": "/codex", "experiment_id": "isolated"}
    def worker(command, **kwargs):
        assert command[:2] == ["/codex", "exec"]
        lines = kwargs["stdin"].splitlines()
        assert "collect_modeling_sources.py" in lines[0] and "research cycle" in lines[1]
        assert "{{" not in kwargs["stdin"]
        (tmp_path / "cycle-receipt.json").write_text(json.dumps({"slot": "2026-10-17T22:30:00Z", "experiment_id": "isolated-interim", "run_id": "permanent-id", "status": "hold"}))
    result = run_job(cfg, "research", "2026-10-17T22:30:00Z", tmp_path, execute_fn=worker)
    assert result["receipt"]["status"] == "hold"
    doc = json.loads((tmp_path / "cycle-receipt.json").read_text())
    (tmp_path / "cycle-receipt.json").write_text(json.dumps({**doc, "experiment_id": "wrong"}))
    with pytest.raises(RuntimeError, match="permanent slot"):
        run_job(cfg, "research", "2026-10-17T22:30:00Z", tmp_path, execute_fn=lambda *a, **k: None)


def test_health_rotation_retains_parseable_events_and_permanent_slot_receipts(tmp_path):
    receipt = tmp_path / "status.json"
    atomic_json(receipt, {"slot": "permanent", "status": "succeeded"})
    for n in range(5):
        jobs.append_health_event(tmp_path, {"cycle": n}, maximum_bytes=1, retained_files=2)
    assert json.loads((tmp_path / "events.jsonl").read_text()) == {"cycle": 4}
    assert json.loads((tmp_path / "events.jsonl.1").read_text()) == {"cycle": 3}
    assert json.loads((tmp_path / "events.jsonl.2").read_text()) == {"cycle": 2}
    assert not (tmp_path / "events.jsonl.3").exists()
    assert json.loads(receipt.read_text())["slot"] == "permanent"


@pytest.mark.parametrize("slot,expected", [
    ("2026-10-18T04:00:00Z", ["2026-10-17", "current"]),
    ("2026-11-02T05:00:00Z", ["2026-11-01", "current"]),
    ("2026-10-18T03:00:00Z", ["current"]),
])
def test_midnight_calendar_revision_precedes_current_latest(tmp_path, slot, expected):
    cfg = {"project_root": str(tmp_path), "runtime_root": str(tmp_path), "experiment_id": "isolated",
           "commands": {"hourly": [["report", "current"]], "rollover": [["report", "{previous_day}"]]}}
    calls = []
    run_job(cfg, "hourly", slot, tmp_path,
            execute_fn=lambda command, **kwargs: calls.append(command[-1]))
    assert calls == expected
