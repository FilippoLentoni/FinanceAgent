"""Shared test configuration. Offline suites run under the offline harness (``tests/harness.py``): no
network beyond loopback, no real AWS call, no Bedrock call at all, fake credentials only. The deployed
suites (``FINPLAN_TARGET_ENV`` set by ``scripts/stage_runner.py``) run WITHOUT it, with the stage role's
real credentials (lesson L5: fake credentials only for offline suites)."""

from __future__ import annotations

import os

if not os.environ.get("FINPLAN_TARGET_ENV"):
    from tests.harness import _offline_harness, pytest_configure, pytest_unconfigure  # noqa: F401
