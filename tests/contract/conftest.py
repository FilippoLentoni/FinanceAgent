"""Offline suite: always hermetic, even when ``FINPLAN_TARGET_ENV`` is set (lesson L5)."""

from tests.harness import _offline_harness, install  # noqa: F401

install()
