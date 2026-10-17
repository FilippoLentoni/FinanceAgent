"""The exact XNYS artifact remains portable outside the multi-repository workspace."""
from datetime import UTC, date, datetime

import pytest

from finplan_agent.core.market_calendar import close_utc, evidence, research_collection_open, session


def test_real_xnys_holiday_and_early_close_boundaries():
    assert session(date(2026, 12, 25))['status'] == 'holiday'
    assert close_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, tzinfo=UTC)
    assert research_collection_open(datetime(2026, 11, 27, 17, 59, tzinfo=UTC))
    assert not research_collection_open(datetime(2026, 11, 27, 18, tzinfo=UTC))


def test_latest_completed_session_and_dst_preserve_local_market_times():
    assert evidence(datetime(2026, 10, 18, 13, 45, tzinfo=UTC))['expected_completed_session'] == '2026-10-16'
    assert evidence(datetime(2026, 12, 25, 14, tzinfo=UTC))['expected_completed_session'] == '2026-12-24'
    assert research_collection_open(datetime(2026, 10, 19, 12, 55, tzinfo=UTC))
    assert research_collection_open(datetime(2026, 11, 2, 13, 55, tzinfo=UTC))
    assert not research_collection_open(datetime(2026, 11, 2, 12, 55, tzinfo=UTC))


def test_calendar_refuses_out_of_coverage_and_naive_times():
    with pytest.raises(ValueError, match='coverage'):
        evidence(datetime(2028, 1, 3, tzinfo=UTC))
    with pytest.raises(ValueError, match='timezone'):
        research_collection_open(datetime(2026, 10, 19))
