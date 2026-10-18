"""Durable EC2 job wrapper. DynamoDB remains the financial source of truth.

Local state records scheduler health/retry receipts only. Financial commands must
use permanent DynamoDB slot IDs and enforce their own paper ledger conditions.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

JOBS = ("snapshot", "research", "hourly", "eod", "weekly")


def stamp(at):
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_for(job, at):
    at = at.astimezone(UTC)
    if job == "snapshot":
        return stamp(at.replace(second=0, microsecond=0))
    if job == "research":
        return stamp(at.replace(minute=30 * (at.minute // 30), second=0, microsecond=0))
    if job == "hourly":
        return stamp(at.replace(minute=0, second=0, microsecond=0))
    ny = at.astimezone(ZoneInfo("America/New_York"))
    return ny.date().isoformat()


def missed_hours(last, current, maximum=168):
    """Bounded recovery list. No inference is allowed on current data for these slots."""
    if not last:
        return []
    lo = datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(hours=1)
    hi = datetime.fromisoformat(current.replace("Z", "+00:00"))
    count = max(0, int((hi - lo).total_seconds() // 3600))
    if count > maximum:
        raise RuntimeError("hourly recovery exceeds bounded window; operator accounting required")
    return [stamp(lo + timedelta(hours=i)) for i in range(count)]


def atomic_json(path, doc):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as out:
        json.dump(doc, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def append_health_event(state, record, *, maximum_bytes=8 * 1024 * 1024, retained_files=4):
    """Bounded local operational logs; permanent financial audit stays in DynamoDB.

    Separate job locks do not protect their common log, so rotation/append share
    this lock. Never remove financial inputs, output artifacts or slot records.
    """
    with (state / "events.lock").open("a") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        target = state / "events.jsonl"
        if target.exists() and target.stat().st_size >= maximum_bytes:
            for index in range(retained_files, 0, -1):
                source = target if index == 1 else state / ("events.jsonl." + str(index - 1))
                if source.exists():
                    os.replace(source, state / ("events.jsonl." + str(index)))
        with target.open("a") as events:
            events.write(json.dumps(record, sort_keys=True) + "\n")
            events.flush()
            os.fsync(events.fileno())


class RetryableIncomplete(RuntimeError):
    """Exit75 means permanent slot work remains leased; it is not success."""


def execute_with_retry(command, *, cwd, timeout=900, execute_fn=None, clock=None, sleep=None):
    """Retry only explicit incomplete slots within one bounded command deadline.

    A dead owner's600second lease outlives systemd's short restart burst. Keep the
    same command/slot under the service lock until recovery is eligible, without
    treating generic failures as permission to repeat model invocation.
    """
    execute_fn=execute_fn or execute;clock=clock or time.monotonic;sleep=sleep or time.sleep
    deadline=clock()+timeout;attempts=0
    while True:
        remaining=deadline-clock()
        if remaining<=0:raise TimeoutError('incomplete slot exceeded bounded command deadline')
        attempts+=1
        try:
            code=execute_fn(command,cwd=cwd,timeout=remaining)
            if code==75:raise RetryableIncomplete('permanent slot still incomplete')
            if code not in (None,0):raise RuntimeError('job exited with code '+str(code))
            return {'attempts':attempts,'deferred_incomplete_retries':attempts-1}
        except RetryableIncomplete:
            remaining=deadline-clock()
            if remaining<=0:raise TimeoutError('incomplete slot exceeded bounded command deadline') from None
            sleep(min(30.,remaining))


def execute(command, *, cwd, timeout, stdin=None, final=None):
    # The headless console can contain model summaries; keep only public final
    # output and wrapper metadata. No hidden reasoning/transport/auth logs.
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise TimeoutError("bounded job deadline exceeded") from None
    if process.returncode==75:
        raise RetryableIncomplete('permanent slot still incomplete')
    if process.returncode:
        raise RuntimeError("job exited with code " + str(process.returncode))
    return process.returncode


def run_job(cfg, job, slot, directory, *, execute_fn=execute):
    root = Path(cfg["project_root"])
    if job == "snapshot":
        target = root / "test-codex" / ("snapshot-" + slot.replace(":", "").replace("-", "") + ".empty")
        target.parent.mkdir(parents=True, exist_ok=True)
        final = directory / "codex-final.txt"
        prompt = ("Scheduling test only. In the current working directory, use your shell tool to create exactly one "
                  "empty (zero-byte) file named " + target.name + ". Do not create any other file or perform financial work. "
                  "Report that filename when done.")
        execute_fn([cfg["codex"], "exec", "--skip-git-repo-check", "--ephemeral", "-c", 'model_reasoning_effort="low"',
                    "--output-last-message", str(final), "-"], cwd=str(target.parent), timeout=55, stdin=prompt)
        if not target.is_file() or target.stat().st_size != 0:
            raise RuntimeError("Codex did not produce the required zero-byte snapshot")
        return {"snapshot": str(target), "bytes": 0, "invocation": "real_headless_codex", "final_sha256": hashlib.sha256(final.read_bytes()).hexdigest()}
    if job == "research":
        prompt_path = Path(cfg["research_prompt"])
        prompt = prompt_path.read_text()
        for name, value in {"slot": slot, "experiment": cfg["experiment_id"], "output": str(directory),
                            "runtime_python": cfg["runtime_python"], "platform_root": cfg["platform_root"]}.items():
            prompt = prompt.replace("{{" + name + "}}", value)
        final = directory / "codex-final.txt"
        execute_fn([cfg["codex"], "exec", "--skip-git-repo-check", "--ephemeral", "--output-last-message", str(final), "-"],
                   cwd=cfg["runtime_root"], timeout=1500, stdin=prompt)
        receipt = directory / "cycle-receipt.json"
        if not receipt.is_file():
            raise RuntimeError("Codex financial workflow did not publish its durable cycle receipt")
        record = json.loads(receipt.read_text())
        if record.get("slot") != slot or record.get("experiment_id") != cfg["experiment_id"] + "-interim" or not record.get("run_id"):
            raise RuntimeError("financial cycle receipt does not match permanent slot")
        if record.get("status") not in ("succeeded", "hold", "reused", "unavailable", "failed", "skipped"):
            raise RuntimeError("financial cycle has no explicit persisted status")
        return {"invocation": "real_headless_codex", "receipt": record, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    commands = cfg["commands"][job]
    if commands and isinstance(commands[0], str):
        commands = [commands]
    prior_day = None
    if job == "hourly":
        local_slot = datetime.fromisoformat(slot.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        if local_slot.hour == 0:
            prior_day = (local_slot.date() - timedelta(days=1)).isoformat()
            # Include late previous-day interim runs before rendering the current
            # day last, so latest.html continues to identify the current day.
            commands = cfg["commands"].get("rollover", []) + commands
    records = []
    failures = []
    for template in commands:
        command = [s.replace("{slot}", slot).replace("{experiment}", cfg["experiment_id"]).replace("{output}", str(directory)).replace("{previous_day}", prior_day or "") for s in template]
        try:
            retry=execute_with_retry(command,cwd=cfg["runtime_root"],timeout=900,execute_fn=execute_fn)
            records.append({"command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(), "status": "succeeded", **retry})
        except Exception as exc:
            failures.append(type(exc).__name__)
            records.append({"command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(), "status": "failed", "error_code": type(exc).__name__})
            # Still generate the report exposing the failed approach/cohort.
    atomic_json(directory / "command-results.json", {"commands": records})
    if failures:
        raise RuntimeError("one or more bounded workflow commands failed")
    return {"commands": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=JOBS)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--slot", help="manual explicit slot; journal distinguishes it from scheduled")
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    if cfg.get("environment") != "beta" or cfg.get("region") != "us-east-2":
        parser.error("only beta/us-east-2 is authorized")
    if args.job in ("hourly", "eod", "weekly") and cfg.get("report_cache_dir"):
        # Disposable verified original-byte cache; authoritative heads stay in
        # DynamoDB. Do not add report settings to the separate Codex workflows.
        os.environ["FINPLAN_REPORT_CACHE_DIR"] = str(Path(cfg["report_cache_dir"]).resolve())
    state = Path(cfg["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    slot = args.slot or slot_for(args.job, datetime.now(UTC))
    os.environ["FINPLAN_EXPERIMENT_TRIGGER"] = "manual_validation" if args.slot else "systemd_scheduled"
    lock = state / (args.job + ".lock")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"job": args.job, "slot": slot, "status": "overlap_refused"}))
            return 0
        directory = state / args.job / slot.replace(":", "")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        status_path = directory / "status.json"
        prior = json.loads(status_path.read_text()) if status_path.exists() else {}
        if prior.get("status") == "succeeded":
            print(json.dumps({"job": args.job, "slot": slot, "status": "duplicate_noop"}))
            return 0
        record = {"job": args.job, "slot": slot, "trigger": "manual" if args.slot else "systemd_scheduled",
                  "status": "running", "attempt": int(prior.get("attempt", 0)) + 1, "started_at": stamp(datetime.now(UTC))}
        atomic_json(status_path, record)
        try:
            if args.job == "hourly":
                checkpoint = state / "hourly-last-completed.json"
                last = json.loads(checkpoint.read_text())["slot"] if checkpoint.exists() else None
                if last is None and cfg.get("first_scheduled_slot"):
                    last = stamp(datetime.fromisoformat(cfg["first_scheduled_slot"].replace("Z", "+00:00")) - timedelta(hours=1))
                for missed in missed_hours(last, slot):
                    missed_dir = state / "hourly" / missed.replace(":", "")
                    missed_dir.mkdir(parents=True, exist_ok=True)
                    templates = cfg["commands"]["hourly"]
                    if templates and isinstance(templates[0], str):
                        templates = [templates]
                    for template in templates:
                        if not any(module in template for module in ("finplan_model.forward.hourly", "finplan_model.forward.crypto_hourly")):
                            continue
                        command = [s.replace("{slot}", missed).replace("{experiment}", cfg["experiment_id"]).replace("{output}", str(missed_dir)) for s in template]
                        execute_with_retry(command + ["--account-missed"], cwd=cfg["runtime_root"], timeout=900)
                    atomic_json(missed_dir / "status.json", {"job": "hourly", "slot": missed, "status": "accounted_missing", "trigger": "restart_recovery"})
            details = run_job(cfg, args.job, slot, directory)
            record.update(status="succeeded", details=details)
            if args.job == "hourly":
                atomic_json(state / "hourly-last-completed.json", {"slot": slot})
        except Exception as exc:
            record.update(status="failed", error_code=type(exc).__name__)
        record["finished_at"] = stamp(datetime.now(UTC))
        atomic_json(status_path, record)
        append_health_event(state, record)
        print(json.dumps(record, sort_keys=True))
        return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
