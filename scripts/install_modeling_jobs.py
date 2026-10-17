#!/usr/bin/env python3
"""Install beta EC2 user units after their data-path verification receipt exists.

No AWS mutation. Run as the EC2 user; existing user lingering keeps the units
alive after logout and starts them at boot. Unrelated timers are left alone.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

CALENDARS = {
    "snapshot": "*-*-* *:*:00 UTC",
    "research": "*-*-* *:00,30:00 UTC",
    "hourly": "*-*-* *:05:00 UTC",
    "eod": "*-*-* 17:00:00 America/New_York",
    "weekly": "*-*-* 17:10:00 America/New_York",
}
TIMEOUTS = {"snapshot": 70, "research": 1550, "hourly": 3400, "eod": 950, "weekly": 950}


def units(job, cfg, config_path, python):
    paths = [cfg["runtime_pythonpath"], cfg["storage_pythonpath"], str(Path(__file__).resolve().parents[1] / "agent")]
    root = Path(__file__).resolve().parents[1]
    command = shlex.join([str(python), "-m", "finplan_agent.modeling.jobs", job, "--config", str(config_path)])
    service = f"""[Unit]
Description=Beta modeling {job} workflow (isolated paper only)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=3600
StartLimitBurst=3

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={command}
Environment=HOME=/home/ec2-user
Environment=AWS_REGION=us-east-2
Environment=AWS_DEFAULT_REGION=us-east-2
Environment=PYTHONUNBUFFERED=1
Environment=TMPDIR={cfg['scratch_dir']}
Environment=PYTHONPATH={':'.join(paths)}
Environment=PATH={Path(cfg['runtime_python']).parent}:/home/ec2-user/.local/bin:/usr/local/bin:/usr/bin:/bin
UMask=0077
TimeoutStartSec={TIMEOUTS[job]}
KillMode=control-group
NoNewPrivileges=true
Restart=on-failure
RestartSec=30
"""
    timer = f"""[Unit]
Description=Durable beta modeling {job} schedule

[Timer]
OnCalendar={CALENDARS[job]}
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0
Unit=finplan-beta-modeling-{job}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    proof = json.loads(args.verification_receipt.read_text())
    if cfg.get("environment") != "beta" or cfg.get("region") != "us-east-2" or proof.get("data_path_verified") is not True:
        parser.error("a real beta data-path verification receipt is required")
    if proof.get("experiment_id") != cfg.get("experiment_id"):
        parser.error("verification belongs to another experiment")
    linger = subprocess.check_output(["loginctl", "show-user", "ec2-user", "-p", "Linger", "--value"], text=True).strip()
    if linger != "yes":
        parser.error("user lingering must be enabled for reboot/logout persistence")
    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    for job in CALENDARS:
        service, timer = units(job, cfg, args.config.resolve(), Path(sys.executable))
        (unit_dir / f"finplan-beta-modeling-{job}.service").write_text(service)
        (unit_dir / f"finplan-beta-modeling-{job}.timer").write_text(timer)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if args.enable:
        subprocess.run(["systemctl", "--user", "enable", "--now", *[f"finplan-beta-modeling-{job}.timer" for job in CALENDARS]], check=True)
    print(json.dumps({"installed": list(CALENDARS), "enabled": args.enable, "unit_dir": str(unit_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
