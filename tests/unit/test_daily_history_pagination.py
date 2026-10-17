"""The response byte cap may place a genuine prior method on a later page."""
from copy import deepcopy

import pytest

from scripts.daily_financial_report import generate
from tests.unit.test_daily_financial_report import NOW, Reader, config
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation


class PagedReader(Reader):
    def __init__(self, rec, pages):
        super().__init__(rec)
        self.pages = pages

    def call(self, label, tool, args):
        if tool != 'list_portfolio_decisions':
            return super().call(label, tool, args)
        self.calls.append((tool, deepcopy(args)))
        index = int(args.get('next_token', '0'))
        page = self.pages[index] if index < len(self.pages) else self.pages[-1]
        return {'status': 'available', 'receipt': label + '.json', 'answer': {'portfolio_decisions': [deepcopy(page)]}}


def test_operator_finds_previous_method_on_page_two_then_stops(recommendation, tmp_path):
    unrelated = {'decision_id': 'other', 'algorithm': 'cvar', 'reference_date': '2026-10-07'}
    previous = {'decision_id': 'old-ppo', 'algorithm': 'ppo', 'reference_date': '2026-10-07'}
    reader = PagedReader(recommendation, [{'decisions': [unrelated], 'next_token': '1'},
                                          {'decisions': [previous], 'next_token': '2'}])
    cfg = config(recommendation, tmp_path)
    cfg['methods'] = ['ppo']
    report = generate(reader, cfg, NOW)
    assert report['methods'][0]['explainability']['2_plan_change']['status'] == 'available'
    assert report['history_search']['pages_read'] == 2
    assert report['history_search']['more_pages_available'] is True
    assert report['history_search']['page_receipts'] == ['history.json', 'history-page-2.json']
    assert [args.get('next_token') for tool, args in reader.calls if tool == 'list_portfolio_decisions'] == [None, '1']
    assert any(args.get('previous_decision_id') == 'old-ppo' for tool, args in reader.calls if tool == 'compare_portfolio_decisions')


@pytest.mark.parametrize('repeated', [False, True])
def test_operator_discloses_bounded_or_repeated_cursor_search(recommendation, tmp_path, repeated):
    pages = [{'decisions': [], 'next_token': '1' if repeated else str(index + 1)} for index in range(9)]
    reader = PagedReader(recommendation, pages)
    cfg = config(recommendation, tmp_path)
    cfg['methods'] = ['ppo']
    report = generate(reader, cfg, NOW)
    assert report['history_search']['pages_read'] == (2 if repeated else 8)
    assert report['methods'][0]['explainability']['2_plan_change']['reason'] in {'history_search_page_bound', 'history_cursor_repeated'}
    assert report['status'] == 'partial_readiness'
    assert 'prior_history_search_incomplete' in report['validation_issues']
