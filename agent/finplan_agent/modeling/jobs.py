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
    records = []
    failures = []
    for template in commands:
        command = [s.replace("{slot}", slot).replace("{experiment}", cfg["experiment_id"]).replace("{output}", str(directory)) for s in template]
        try:
            execute_fn(command, cwd=cfg["runtime_root"], timeout=900)
            records.append({"command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(), "status": "succeeded"})
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
                        if "finplan_model.forward.hourly" not in template:
                            continue
                        command = [s.replace("{slot}", missed).replace("{experiment}", cfg["experiment_id"]).replace("{output}", str(missed_dir)) for s in template]
                        execute(command + ["--account-missed"], cwd=cfg["runtime_root"], timeout=300)
                    atomic_json(missed_dir / "status.json", {"job": "hourly", "slot": missed, "status": "accounted_missing", "trigger": "restart_recovery"})
            details = run_job(cfg, args.job, slot, directory)
            record.update(status="succeeded", details=details)
            if args.job == "hourly":
                atomic_json(state / "hourly-last-completed.json", {"slot": slot})
        except Exception as exc:
            record.update(status="failed", error_code=type(exc).__name__)
        record["finished_at"] = stamp(datetime.now(UTC))
        atomic_json(status_path, record)
        with (state / "events.jsonl").open("a") as events:
            events.write(json.dumps(record, sort_keys=True) + "\n")
            events.flush()
            os.fsync(events.fileno())
        print(json.dumps(record, sort_keys=True))
        return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
