"""Scheduling/restart semantics without sleeping or invoking a paid model."""
from datetime import UTC, datetime
import json

import pytest

from finplan_agent.modeling.jobs import atomic_json, missed_hours, run_job, slot_for
from finplan_agent.modeling import jobs


def test_independent_hourly_six_hour_slots_and_ny_calendar():
    at = datetime(2026, 10, 18, 0, 31, 42, tzinfo=UTC)
    assert slot_for("snapshot", at) == "2026-10-18T00:00:00Z"
    assert slot_for("research", at) == "2026-10-18T00:00:00Z"
    assert slot_for("research", datetime(2026, 10, 18, 17, 59, tzinfo=UTC)) == "2026-10-18T12:00:00Z"
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


@pytest.mark.parametrize("job", ["hourly", "eod", "weekly", "research", "snapshot"])
def test_configured_evidence_cache_is_enabled_only_for_report_jobs(tmp_path, monkeypatch, job):
    cache = tmp_path / "private-original-byte-cache"
    queue = tmp_path / "pending-report-revisions"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"environment": "beta", "region": "us-east-2",
        "state_dir": str(tmp_path / "state"), "report_cache_dir": str(cache), "report_refresh_dir": str(queue)}))
    monkeypatch.delenv("FINPLAN_REPORT_CACHE_DIR", raising=False)
    monkeypatch.delenv("FINPLAN_REPORT_REFRESH_DIR", raising=False)
    observed = []
    def worker(*args):
        observed.append((jobs.os.environ.get("FINPLAN_REPORT_CACHE_DIR"), jobs.os.environ.get("FINPLAN_REPORT_REFRESH_DIR")))
        return {"completed": True}
    monkeypatch.setattr(jobs, "run_job", worker)
    slot = "2026-10-18T02:00:00Z" if job in ("hourly", "research", "snapshot") else "2026-10-17"
    assert jobs.main([job, "--config", str(config), "--slot", slot]) == 0
    assert observed == [(str(cache), str(queue)) if job in ("hourly", "eod", "weekly") else (None, None)]


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


class ControlledClock:
    def __init__(self):self.at=0.;self.sleeps=[]
    def clock(self):return self.at
    def sleep(self,seconds):self.sleeps.append(seconds);self.at+=seconds


def test_incomplete_exit75_waits_original600second_lease_and_retries_same_slot():
    clock=ControlledClock();calls=[];command=['python','-m','hourly','--slot','permanent']
    def worker(actual,**kwargs):
        assert actual==command;calls.append(clock.at)
        if clock.at<600:raise jobs.RetryableIncomplete('controlled original lease')
        return 0
    result=jobs.execute_with_retry(command,cwd='.',timeout=900,execute_fn=worker,clock=clock.clock,sleep=clock.sleep)
    assert calls[-1]==600 and result['deferred_incomplete_retries']==20
    assert max(clock.sleeps)<=30 and result['attempts']==21


def test_generic_errors_never_enter_incomplete_retry_loop():
    clock=ControlledClock();calls=[]
    def worker(*args,**kwargs):calls.append(True);raise RuntimeError('genuine failure')
    with pytest.raises(RuntimeError,match='genuine failure'):
        jobs.execute_with_retry(['command'],cwd='.',execute_fn=worker,clock=clock.clock,sleep=clock.sleep)
    assert len(calls)==1 and clock.sleeps==[]


def test_incomplete_deadline_is_bounded_and_never_returns_success():
    clock=ControlledClock()
    with pytest.raises(TimeoutError,match='incomplete'):
        jobs.execute_with_retry(['command'],cwd='.',timeout=65,execute_fn=lambda *a,**k:75,clock=clock.clock,sleep=clock.sleep)
    assert clock.at==65 and clock.sleeps==[30.,30.,5.]


def test_hourly_checkpoint_not_advanced_while_incomplete_command_is_waiting(tmp_path,monkeypatch):
    state=tmp_path/'state';state.mkdir();clock=ControlledClock();calls=[]
    cfg={'environment':'beta','region':'us-east-2','state_dir':str(state),'project_root':str(tmp_path),
         'runtime_root':str(tmp_path),'experiment_id':'controlled','commands':{'hourly':[['worker','{slot}']]}}
    config=tmp_path/'config.json';config.write_text(json.dumps(cfg))
    monkeypatch.setattr(jobs.time,'monotonic',clock.clock);monkeypatch.setattr(jobs.time,'sleep',clock.sleep)
    def worker(command,**kwargs):
        assert not (state/'hourly-last-completed.json').exists()
        calls.append(command)
        if clock.at<600:raise jobs.RetryableIncomplete('controlled original lease')
    original=jobs.run_job
    monkeypatch.setattr(jobs,'run_job',lambda c,j,s,d:original(c,j,s,d,execute_fn=worker))
    assert jobs.main(['hourly','--config',str(config),'--slot','2026-10-18T00:00:00Z'])==0
    assert json.loads((state/'hourly-last-completed.json').read_text())['slot']=='2026-10-18T00:00:00Z'
    assert len(calls)==21 and all(call==calls[0] for call in calls)
