"""Report actual numeric archive provenance without inventing morning headline input."""
from copy import deepcopy

from finplan_agent.core.news_evidence import GDELT_PROTOCOL, render, summarize
from tests.fakes.agent import SESSION, auth
from tests.unit.test_saved_portfolio_recommendations import recommendation as recommendation
from tests.unit.test_saved_portfolio_recommendations import service


def news():
    return {'consumed': True, 'feature_protocol': GDELT_PROTOCOL,
            'training_corpus_checksum': 'sha256:' + 'a' * 64, 'decision_time': '2026-10-08T13:45:00Z',
            'provenance': {'source_day': '2026-10-07', 'document_checksum': 'sha256:' + 'b' * 64,
                           'availability_mode': 'platform',
                           'lineage': {'source_available_at': '2026-10-08T11:00:00Z',
                                       'central_available_at': '2026-10-08T12:45:00Z',
                                       'retrieved_at': '2026-10-08T12:40:00Z',
                                       'rights': {'rights_status': 'research_and_training',
                                                  'scope': 'released_GDELT_machine_metadata_only'}}}}


def test_numeric_source_day_lag_and_central_availability_are_explicit():
    proof = news()
    result = summarize(proof)
    assert result['feature_protocol'] == GDELT_PROTOCOL
    assert result['source_day'] == '2026-10-07'
    assert result['archive_day_lag_days'] == 1
    assert result['source_availability_lag_hours'] == 2.75
    assert result['central_available_at'] == '2026-10-08T12:45:00Z'
    assert 'not established' in result['same_morning_paid_headlines']
    assert 'not calibrated stock sentiment' in result['sentiment_interpretation']
    assert proof == news()  # Explanation does not rewrite frozen evidence.


def test_bad_or_absent_source_timestamps_do_not_invent_lag():
    proof = news()
    proof['decision_time'] = 'unverified'
    result = summarize(proof)
    assert 'source_availability_lag_hours' not in result
    assert 'archive_day_lag_days' not in result
    assert summarize({})['source_kind'] == 'not_consumed'


def test_current_news_recommendation_narration_preserves_real_cutoff_and_claim_check(recommendation):
    rec = deepcopy(recommendation)
    rec['news_timing'] = 'eligible_news_at_decision_with_completed_prices'
    rec['decision_at'] = '2026-10-08T13:45:00Z'
    rec['diagnostics']['news_evidence'] = news()
    svc, tools = service(rec)
    result = svc.handle({'prompt': 'How should I invest today?', 'stream': False}, auth(), runtime_session_id=SESSION)
    assert result['status'] == 'completed'
    assert result['answer']['claim_check']['passed'], result['answer']['claim_check']
    narrative = result['answer']['narrative']
    assert 'not live intraday prices' in narrative
    assert '2026-10-07' in narrative and '2026-10-08T12:45:00Z' in narrative
    assert 'separately collected same-morning paid research headlines' in narrative
    assert 'Decisions use the completed close for the next session' not in narrative
    assert tools.calls == [('get_portfolio_history', {'limit': 1}), ('generate_portfolio_recommendation_ppo', {'instruments': ['AAPL', 'GOOGL', 'NFLX', 'NVDA', 'VOO']})]


def test_declared_paid_research_features_keep_their_own_cutoff():
    text = render({'consumed': True, 'formation_time': '2026-10-08T13:45:00Z'})
    assert '2026-10-08T13:45:00Z' in text
    assert 'GDELT' not in text


def test_intraday_source_protocol_and_actual_window_are_not_rewritten_as_daily_archive():
    proof = news()
    proof['feature_protocol'] = 'gdelt-gkg-v2-intraday-organization-tone-v1'
    proof['provenance']['lineage'].update(source='gdelt-gkg-v2', window_start='2026-10-08T12:45:00Z',
                                        window_end='2026-10-08T13:30:00Z')
    proof['max_window_age_hours'] = 1.5
    result = summarize(proof)
    assert result['feature_version'] == proof['feature_protocol']
    assert result['source_name'] == 'gdelt-gkg-v2'
    assert result['source_window_start'] == '2026-10-08T12:45:00Z'
    assert result['source_window_end'] == '2026-10-08T13:30:00Z'
    text = render(proof)
    assert proof['feature_protocol'] in text
    assert '2026-10-08T12:45:00Z through 2026-10-08T13:30:00Z' in text
    assert 'daily UTC archive' not in text
