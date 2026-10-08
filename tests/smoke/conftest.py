"""Deployed suite (lesson L5): real stage-role credentials, never the offline fakes, and the run FAILS
when fewer than one test executed (a fully skipped deployed suite proves nothing)."""

from __future__ import annotations

import os

_EXECUTED = {"n": 0}


def pytest_runtest_logreport(report):
    if report.when == "call" and not report.skipped:
        _EXECUTED["n"] += 1


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("FINPLAN_TARGET_ENV") and _EXECUTED["n"] < 1:
        session.exitstatus = 1
