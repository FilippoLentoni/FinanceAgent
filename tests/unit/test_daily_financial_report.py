"""Report readiness and comparable hosted evidence without market or credential side effects."""
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from finplan_contracts.validate import validate

from scripts.daily_financial_report import check_recommendation, compare_methods, generate, markdown, private_json
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation

NOW = datetime(2026, 10, 18, 13, 45, tzinfo=UTC)


class Reader:
    def __init__(self, rec, failures=(), previous=()):
        self.rec = rec
        self.failures = failures
        self.previous = previous
        self.calls = []

    def call(self, label, tool, args):
        self.calls.append((tool, deepcopy(args)))
        if tool.startswith('recommend'):
            schema = 'tools/recommend-portfolio-invocation-request' if tool == 'recommend_portfolio' else 'tools/recommend-classical-portfolio-request'
            assert validate(args, schema).valid, args
        if args.get('algorithm', 'ppo') in self.failures and tool.startswith('recommend'):
            return {'status': 'not_available', 'receipt': label + '.json', 'error': {'code': 'NOT_READY'}}
        document = {}
        if tool == 'list_portfolio_decisions':
            document = {'decisions': list(self.previous)}
        elif tool.startswith('recommend'):
            rec = deepcopy(self.rec)
            rec['strategy'] = args.get('algorithm', 'ppo')
            # Keep market/state identical while proving the actions can differ by method.
            if rec['strategy'] == 'mean_variance':
                rec['target_weights'][0]['weight'] += .01
                rec['cash_weight'] -= .01
            document = {'recommendation': rec, 'decision_id': 'decision-' + rec['strategy']}
        else:
            document = {'analysis_id': 'analysis-' + label, 'analysis_kind': 'performance' if tool.startswith('evaluate') else 'explanation'}
        answer = {'portfolio_decisions': [document] if 'decision_id' in document or 'decisions' in document else [],
                  'portfolio_analyses': [document] if document.get('analysis_id') else [],
                  'evidence': [{'tool': tool, 'ok': True}], 'claim_check': {'passed': True}, 'narrative': tool + ' evidence'}
        return {'status': 'available', 'receipt': label + '.json', 'answer': answer}


def config(rec, tmp_path):
    return {'portfolio_id': rec['portfolio_state']['portfolio_id'], 'expected_instruments': [row['instrument_id'] for row in rec['target_weights']],
            'require_news_features': False, 'news_status_dir': str(tmp_path),
            'classical_settings': {'lookback_days': 60, 'max_weight': .6},
            '_calendar_context': {'is_trading_session': False, 'expected_completed_session': rec['as_of'], 'session_status': 'weekend'}}


def test_all_methods_pin_snapshot_holdings_and_settings_and_explain_three_levels(recommendation, tmp_path):
    reader = Reader(recommendation)
    cfg = config(recommendation, tmp_path)
    report = generate(reader, cfg, NOW)
    assert report['status'] == 'complete'
    assert report['session_label'] == 'market_closed_latest_completed_session'
    assert report['comparison']['recommendations_differ'] is True
    assert report['comparison']['winner'] == 'not_established'
    assert len(report['cross_method_explanations']) == 3
    for tool, args in reader.calls:
        assert tool not in {'resolve_portfolio_decision', 'submit_experiment', 'create_override_version'}
        if tool == 'recommend_classical_portfolio':
            assert 'input_snapshot_id' not in args  # Schema couples snapshot and historical as_of.
            assert 'as_of' not in args  # Do not backdate morning news to yesterday's close.
            assert args['portfolio_id'] == recommendation['portfolio_state']['portfolio_id']
            assert args['settings'] == cfg['classical_settings']
    for item in report['methods']:
        levels = item['explainability']
        assert levels['1_recommendation']['status'] == 'available'
        assert levels['2_plan_change']['reason'] == 'no_prior_compatible_market_date'
        assert levels['3_realization_and_context']['evaluated_decision_id'] == item['decision_id']
        assert levels['3_realization_and_context']['dated_context']['status'] == 'available'
    assert 'No holdings were changed.' in markdown(report)


def test_unavailable_ppo_keeps_classical_outputs_and_missing_levels(recommendation, tmp_path):
    reader = Reader(recommendation, failures={'ppo'})
    report = generate(reader, config(recommendation, tmp_path), NOW)
    assert report['status'] == 'partial_readiness'
    assert report['methods'][0]['status'] == 'not_available'
    assert all(row['status'] == 'available' for row in report['methods'][1:])
    assert all(level['status'] == 'not_available' for level in report['methods'][0]['explainability'].values())
    assert len(report['cross_method_explanations']) == 2


def test_prior_decision_explanation_uses_actual_distinct_date(recommendation, tmp_path):
    old = {'decision_id': 'previous-ppo', 'algorithm': 'ppo', 'reference_date': '2026-10-07'}
    same_day = {'decision_id': 'same-day-retry', 'algorithm': 'ppo', 'reference_date': recommendation['as_of']}
    reader = Reader(recommendation, previous=[same_day, old])
    generate(reader, config(recommendation, tmp_path), NOW)
    assert ('compare_portfolio_decisions', {'portfolio_id': recommendation['portfolio_state']['portfolio_id'],
                                          'previous_decision_id': 'previous-ppo', 'current_decision_id': 'decision-ppo'}) in reader.calls
    assert ('evaluate_portfolio_decision', {'portfolio_id': recommendation['portfolio_state']['portfolio_id'],
                                          'decision_id': 'previous-ppo'}) in reader.calls


def test_readiness_flags_universe_news_and_stale_prices_without_silent_substitution(recommendation, tmp_path):
    cfg = config(recommendation, tmp_path)
    cfg['expected_instruments'] = ['VOO', 'GOOGL', 'AMZN', 'MSFT', 'META', 'TSLA', 'AAPL', 'NVDA']
    cfg['require_news_features'] = True
    cfg['_calendar_context'] = {'is_trading_session': True, 'expected_completed_session': '2026-10-16'}
    report = generate(Reader(recommendation), cfg, NOW)
    assert report['status'] == 'partial_readiness'
    assert set(report['methods'][0]['readiness_issues']) == {'requested_universe_not_served', 'approved_news_features_not_consumed'}
    assert report['pipeline_readiness']['issues'] == ['latest_expected_completed_prices_not_served', 'today_news_collector_marker_missing']
    assert report['methods'][0]['explainability']['1_recommendation']['status'] == 'available'


def test_changed_portfolio_revision_prevents_controlled_comparison(recommendation):
    rec = deepcopy(recommendation)
    rec['portfolio_state']['revision'] += 1
    record = {'recommendation': rec, 'decision_id': 'stored'}
    assert 'cross_method_inputs_differ' in check_recommendation(record, 'ppo', rec['portfolio_state']['portfolio_id'], recommendation, set())
    assert compare_methods([{'method': 'ppo', 'recommendation': recommendation}, {'method': 'min_variance', 'recommendation': rec}])['status'] == 'not_available'


def test_private_receipts_do_not_inherit_permissive_file_modes(tmp_path):
    destination = tmp_path / 'receipt.json'
    private_json(destination, {'result': 'evidence'})
    assert destination.stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError):
        private_json(tmp_path / 'invalid.json', {'weight': float('nan')})


def test_invalid_producer_state_still_renders_a_readiness_report(recommendation, tmp_path):
    recommendation['solution_status'] = 'rejected_hold_current'
    report = generate(Reader(recommendation), config(recommendation, tmp_path), NOW)
    assert report['status'] == 'partial_readiness'
    assert all(item['status'] == 'not_ready' for item in report['methods'])
    assert 'not_verified' in markdown(report)
    assert report['comparison']['status'] == 'not_available'
