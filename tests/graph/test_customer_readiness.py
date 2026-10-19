"""Regressions for observed customer questions, not just successful status."""
from copy import deepcopy
from finplan_contracts.validate import validate
from finplan_agent.graph.portfolio import render_classical
from finplan_agent.graph.lifecycle import lifecycle_plan
from finplan_agent.graph.sensitivity import sensitivity_plan
from tests.graph.test_portfolio_lifecycle import PD


def state(text):
    return {'messages': [{'role': 'user', 'content': [{'text': text}]}], 'turn': 1}


def test_negated_apply_never_routes_sensitivity_to_resolution():
    plan = lifecycle_plan(state(f'How would decision {PD} change if risk aversion increased from 2 to 5 or maximum instrument weight fell from 0.6 to 0.4? Do not apply changes.'), 'session')
    assert plan['workflow']['mode'] == 'lifecycle_sensitivity'
    assert plan['workflow']['variants'] == [{'parameter': 'risk_aversion', 'base': 2.0, 'value': 5.0}, {'parameter': 'max_weight', 'base': 0.6, 'value': 0.4}]
    assert [call.name for call in plan['calls']] == ['get_portfolio_decision']


def test_current_value_question_routes_to_evaluation_not_explanation():
    plan = lifecycle_plan(state(f'Evaluate portfolio decision {PD}. Explain current value, planned allocation and forecast uncertainty.'), 'session')
    assert plan['workflow']['intent'] == 'performance'


def test_negated_accept_does_not_request_resolution():
    plan = lifecycle_plan(state(f'Explain decision {PD}; do not accept it.'), 'session')
    assert plan['workflow']['intent'] == 'why'


def test_sensitivity_freezes_holdings_and_changes_one_parameter():
    rec = {'strategy': 'min_variance', 'settings': {'risk_aversion': 2, 'max_weight': 0.6, 'lookback_days': 60},
           'input_snapshot_id': 'snap_01JA2B3C4D5E6F7G8H9JKMNPQR', 'as_of': '2025-01-03',
           'portfolio_state': {'portfolio_value': 1000, 'high_watermark': 1100, 'current_cash': 200,
                               'positions': [{'instrument_id': 'SYNTH', 'quantity': 8, 'reference_price': 100}]}}
    original = deepcopy(rec)
    wf = {'mode': 'lifecycle_sensitivity', 'variants': [{'parameter': 'max_weight', 'base': 0.6, 'value': 0.4}]}
    rows = [{'tool': 'get_portfolio_decision', 'ok': True, 'result': {'decision': {'decision_id': PD, 'recommendation': rec}}}]
    plan = sensitivity_plan(wf, rows, 1)
    call = plan['calls'][0]
    assert call.name == 'generate_portfolio_recommendation_min_variance'
    args = call.arguments
    # Only settings the per-model tool accepts; the algorithm is fixed by the tool name.
    assert args['settings'] == {'max_weight': 0.4, 'lookback_days': 60} and 'algorithm' not in args
    assert args['instruments'] == ['SYNTH']
    assert args['holdings'] == {'weights': [{'instrument_id': 'SYNTH', 'weight': 0.8}], 'cash_weight': 0.2, 'portfolio_value': 1000, 'high_watermark': 1100}
    assert 'portfolio_id' not in args
    assert rec == original
    assert not sensitivity_plan(plan['workflow'], rows + [{'tool': 'generate_portfolio_recommendation_min_variance', 'ok': True}], 1)['calls']


def test_mean_variance_and_risk_aversion_sensitivity_are_refused_clearly():
    rec = {'strategy': 'mean_variance', 'settings': {'risk_aversion': 2, 'max_weight': 0.6},
           'input_snapshot_id': 'snap_01JA2B3C4D5E6F7G8H9JKMNPQR', 'as_of': '2025-01-03',
           'portfolio_state': {'portfolio_value': 1000, 'high_watermark': 1100, 'current_cash': 200,
                               'positions': [{'instrument_id': 'SYNTH', 'quantity': 8, 'reference_price': 100}]}}
    rows = [{'tool': 'get_portfolio_decision', 'ok': True, 'result': {'decision': {'decision_id': PD, 'recommendation': rec}}}]
    plan = sensitivity_plan({'mode': 'lifecycle_sensitivity', 'variants': [{'parameter': 'risk_aversion', 'base': 2, 'value': 5}]}, rows, 1)
    assert not plan['calls'] and 'no longer served' in plan['clarification']


def test_market_context_narrative_does_not_archive_escape_paths():
    doc = {'analysis_id': 'ca_' + 'a' * 32, 'analysis_kind': 'market_events', 'status': 'not_available',
           'query': '("Apple" OR "Google")', 'reason': 'no_matching_dated_metadata', 'sources': []}
    text, _ = render_classical(doc)
    assert '\\' not in text
    request = {'event_kind': 'agent_turn', 'session_id': 'customer_readiness_' + 'x' * 33,
               'correlation_id': 'corr-test', 'payload': {'narrative': text, 'tool_results': [doc]}, 'idempotency_key': 'customer-readiness-test'}
    result = validate(request, 'tools/record-agent-activity-request')
    assert result.valid, result.issues


def test_horizon_narrative_retains_limits_without_diagnostic_dump():
    doc = {'analysis_id': 'ca_' + 'a' * 32, 'analysis_kind': 'performance', 'reason': 'no_forward_observations',
           'forecast': {'status': 'not_available', 'reason': 'No calibrated forecast'},
           'horizon_evaluation': {'status': 'not_available', 'available_forward_sessions': 0, 'primary_horizon_mature': False,
                                  'protocol': {'primary_horizon_sessions': 64, 'objective': {'kind': 'reward'}},
                                  'large_diagnostics': ['synthetic diagnostic'] * 10000}}
    text, _ = render_classical(doc)
    assert 'No calibrated forecast' in text and 'primary horizon mature: False' in text
    assert len(text) < 3000
    assert doc['horizon_evaluation']['large_diagnostics']
