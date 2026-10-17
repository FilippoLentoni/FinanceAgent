"""Hosted temporal lookup follows bounded immutable cursors without new budgets."""
from copy import deepcopy

from tests.graph.test_portfolio_lifecycle import ANALYSIS, OLD, PD, ask, setup
from tests.unit.test_decision_history import issued
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation


def test_hosted_same_close_formation_lookup_finds_prior_on_page_two(recommendation):
    svc, tools, _ = setup(recommendation)
    pid = recommendation['portfolio_state']['portfolio_id']
    previous, current = issued('17', 'a', OLD), issued('18', 'b', PD)
    for row in (previous, current):
        row['portfolio_id'] = pid
        row['recommendation']['portfolio_state']['portfolio_id'] = pid
    pages = [{'decisions': [current], 'next_token': 'page-two'},
             {'decisions': [previous], 'next_token': 'unused-page-three'}]
    tools.results['list_portfolio_decisions'] = lambda args: deepcopy(pages[1 if args.get('next_token') else 0])
    tools.results['compare_portfolio_decisions'] = lambda _: {'analysis_id': ANALYSIS, 'analysis_kind': 'comparison', 'summary': 'Immutable prior formation compared', 'changes': []}
    result = ask(svc, 'Compare stored PPO recommendations yesterday and today for ' + pid)
    assert result['status'] == 'completed' and result['usage']['invocations'] == 0
    assert [args.get('next_token') for tool, args in tools.calls if tool == 'list_portfolio_decisions'] == [None, 'page-two']
    assert tools.calls[-1] == ('compare_portfolio_decisions', {'portfolio_id': pid, 'previous_decision_id': OLD, 'current_decision_id': PD})
    assert result['answer']['portfolio_decisions'][:2] == pages


def test_hosted_lookup_stops_before_caller_tool_budget_and_discloses_remaining_history(recommendation):
    svc, tools, _ = setup(recommendation)
    pages = []

    def read(args):
        page = {'decisions': [], 'next_token': 'page-' + str(len(pages) + 1)}
        pages.append(deepcopy(page))
        return page

    tools.results['list_portfolio_decisions'] = read
    result = ask(svc, 'Compare stored PPO recommendations yesterday and today')
    assert result['status'] == 'completed'
    assert len(pages) <= 5  # Existing six-tool turn bound reserves the comparison.
    assert 'bounded history search is incomplete' in result['answer']['narrative']
    assert 'not established that prior history is missing' in result['answer']['narrative']
    assert all(tool == 'list_portfolio_decisions' for tool, _ in tools.calls)
