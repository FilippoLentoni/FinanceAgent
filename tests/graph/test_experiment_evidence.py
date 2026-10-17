"""Large synthetic MCP result follows the producer's reviewed model-selection shape."""
from __future__ import annotations

import json

from finplan_agent.graph.claim_check import claim_check
from finplan_agent.graph.experiment_evidence import selection_evidence
from finplan_agent.providers import FixtureProvider
from finplan_agent.providers.base import GenerateResult, Usage
from tests.fakes.agent import CHECKSUM, RUN, FakeToolClient, make_service, run


def experiment():
    rows = [{'strategy': strategy, 'total_return': value, 'sharpe': value + .5, 'max_drawdown': -.1, 'params': {'lookback': 60},
             'transaction_cost_fraction': .001} for strategy, value in (
        ('cash', 0), ('equal_weight', .06), ('min_variance', .07), ('mean_variance', .08), ('scenario_cvar', .09), ('ppo', .31))]
    selection = {'universe': ['SYN_A', 'SYN_B'],
        'splits': {split: {'start': '2025-01-02', 'end': '2025-12-31', 'sessions': 250} for split in ('train', 'validation', 'test')},
        'evaluation': {'same_evaluator_for_all_families': True, 'execution_timing': 'next_open', 'long_only': True, 'max_weight': .6},
        'selection': {'rule': {'metric': 'sharpe', 'split': 'validation'}, 'selected': {'strategy': 'ppo', 'seed': 3}, 'selection_checksum': CHECKSUM},
        'by_split': {split: rows for split in ('train', 'validation', 'test')},
        'rl': {'ppo': {'seeds': [{'seed': seed, 'validation': {'total_return': .2 + seed / 100, 'sharpe': .8, 'max_drawdown': -.1}} for seed in (1, 2, 3, 4, 5)]}},
        'test_access': {'test_reuse': True, 'prior_runs_on_this_test_period': 2},
        'caveats': [{'kind': 'development', 'text': 'Reused development history is not independent forward evidence.'}]}
    return {'run_id': RUN, 'completion_status': 'succeeded', 'artifacts_complete': True, 'dataset_checksum': CHECKSUM,
            'artifacts': [{'artifact_id': 'synthetic-evidence', 'checksum': CHECKSUM, 'kind': 'run_artifact'}],
            'model_selection': selection, 'payload': {'unreviewed_details': 'synthetic padding ' * 4000}}


class ComparisonProvider(FixtureProvider):
    evidence = None

    def generate(self, request, on_token=None):
        if request.purpose != 'narrate':
            return super().generate(request, on_token)
        self.evidence = request.evidence[0]['summary']
        rows = self.evidence['model_selection']['validation_comparison']['rows']
        ppo = next(row for row in rows if row[0] == 'ppo')
        text = f'PPO validation return was {ppo[1]}. This is development evidence, not independent forward validation.'
        if on_token:
            on_token(text)
        return GenerateResult(text=text, tool_calls=(), stop_reason='end_turn', usage=Usage(), provider_kind='fixture', model_id=None)


def test_large_result_keeps_common_method_metrics_seeds_and_source_evidence_through_graph():
    document = experiment()
    assert len(json.dumps(document)) > 4000
    provider = ComparisonProvider()
    service, tools = make_service(provider=provider, tools=FakeToolClient(results={'get_experiment_result': lambda _: document}))
    final = run(service, {'tool_request': {'name': 'get_experiment_result', 'arguments': {'run_id': RUN}}, 'stream': False})
    assert final['status'] == 'completed' and final['answer']['claim_check']['passed']
    assert tools.calls == [('get_experiment_result', {'run_id': RUN})]
    evidence = provider.evidence
    assert len(json.dumps(evidence, sort_keys=True)) <= 4000
    assert evidence['dataset_checksum'] == CHECKSUM
    selection = evidence['model_selection']
    assert selection['evaluation']['same_evaluator_for_all_families'] is True
    assert selection['source_artifact']['checksum'] == CHECKSUM
    assert selection['test_access']['test_reuse'] is True
    assert selection['validation_parameters']['min_variance'] == {'lookback': 60}
    assert 'not differently configured daily optimizers' in evidence['evidence_scope']
    assert 'Structured split dates/session counts override conflicting prose' in evidence['evidence_scope']
    assert selection['splits']['validation']['sessions'] == 250
    rows = selection['validation_comparison']['rows']
    assert {row[0] for row in rows} >= {'ppo', 'min_variance', 'mean_variance', 'scenario_cvar'}
    assert all(row[-1] == .001 for row in rows)
    assert [row[1] for row in selection['validation_seeds']['rows']] == [1, 2, 3, 4, 5]
    assert claim_check(final['answer']['narrative'], [document]).passed
    assert not claim_check('The guaranteed future return is 9.876.', [evidence]).passed
    assert final['answer']['evidence'][0]['summary'] == evidence


def test_small_budget_does_not_claim_complete_metric_evidence_and_partial_results_are_unchanged():
    assert selection_evidence({'run_id': RUN, 'completion_status': 'timed_out', 'artifacts_complete': False}, 4000) is None
    evidence = selection_evidence(experiment(), 900)
    assert evidence is None or (len(json.dumps(evidence, sort_keys=True)) <= 900 and evidence['metric_evidence_status'] == 'incomplete_under_existing_result_budget')


# --- Regression: hosted narration once called scenario_cvar's validation max drawdown
# (0.039991) its "Sharpe ratio" and claimed it had the LOWER transaction cost fraction.
# Rows below are the actual validation metrics of run_01M54F9HVCNVRQTR8QKEEY8CDC.
from finplan_agent.graph.claim_check import REMOVED, metric_label_check  # noqa: E402
from finplan_agent.graph.experiment_evidence import comparison_cells, qualitative_commentary, render_selection_evidence  # noqa: E402

ACTUAL_VALIDATION = [  # strategy, total_return, sharpe, max_drawdown, transaction_cost_fraction
    ('cash', 0.0, None, 0.0, 0.0), ('buy_and_hold', 0.20824, 2.198609, 0.078392, 0.0002),
    ('equal_weight', 0.200069, 2.169466, 0.083462, 0.000488), ('mean_variance', 0.390401, 2.523211, 0.079036, 0.004665),
    ('min_variance', 0.129819, 2.129922, 0.060636, 0.0008), ('scenario_cvar', 0.208845, 2.985971, 0.039991, 0.011805),
    ('ppo', 0.197681, 2.473892, 0.065857, 0.000484)]
METRICS = ('total_return', 'sharpe', 'max_drawdown', 'transaction_cost_fraction')
MISLABELLED = ('The scenario_cvar strategy has a Sharpe ratio of 0.039991, which is higher than the other strategies. '
               'The validation results also show that the scenario_cvar strategy has a lower max drawdown and transaction cost fraction compared to the other strategies. '
               'Historical scores apply only to the frozen experiment configurations.')


def actual_experiment():
    document = experiment()
    rows = [{'strategy': s, **dict(zip(METRICS, values, strict=True)), 'params': {}} for s, *values in ACTUAL_VALIDATION]
    selection = document['model_selection']
    selection['by_split'] = {split: rows for split in ('train', 'validation', 'test')}
    selection['selection'] = {'rule': {'metric': 'sharpe', 'split': 'validation', 'direction': 'higher is better'},
                              'selected': {'strategy': 'scenario_cvar', 'family': 'traditional', 'seed': None}, 'incumbent': 'buy_and_hold', 'selection_checksum': CHECKSUM}
    selection['rl'] = {'ppo': {'seeds': [{'seed': 1, 'validation': {'total_return': 0.215897, 'sharpe': 2.650944, 'max_drawdown': 0.056036}}]}}
    return document


class MislabellingProvider(FixtureProvider):
    requests = ()

    def generate(self, request, on_token=None):
        if request.purpose != 'narrate':
            return super().generate(request, on_token)
        self.requests += (request,)
        if on_token:
            on_token(MISLABELLED)
        return GenerateResult(text=MISLABELLED, tool_calls=(), stop_reason='end_turn', usage=Usage(), provider_kind='fixture', model_id=None)


def _table(narrative, header_start):
    lines = narrative.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header_start))
    header = [cell.strip() for cell in lines[start].strip('|').split('|')]
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith('|'):
            break
        rows.append(dict(zip(header, (cell.strip() for cell in line.strip('|').split('|')), strict=False)))
    return rows


def test_benchmark_explanation_keeps_every_metric_beside_its_strategy_and_field():
    provider = MislabellingProvider()
    service, _ = make_service(provider=provider, tools=FakeToolClient(results={'get_experiment_result': lambda _: actual_experiment()}))
    final = run(service, {'tool_request': {'name': 'get_experiment_result', 'arguments': {'run_id': RUN}}, 'stream': False})
    answer = final['answer']
    narrative = answer['narrative']
    assert final['status'] == 'completed' and answer['claim_check']['passed'], answer['claim_check']
    labels = {'total_return': 'Total return', 'sharpe': 'Sharpe', 'max_drawdown': 'Max drawdown', 'transaction_cost_fraction': 'Transaction cost fraction'}
    table = {row['Strategy']: row for row in _table(narrative, '| Strategy |')}
    assert set(table) == {row[0] for row in ACTUAL_VALIDATION}
    for strategy, *values in ACTUAL_VALIDATION:
        for metric, value in zip(METRICS, values, strict=True):
            assert table[strategy][labels[metric]] == ('n/a' if value is None else format(value, '.10g') if value else '0'), (strategy, metric)
    assert table['scenario_cvar'] == {'Strategy': 'scenario_cvar', 'Total return': '0.208845', 'Sharpe': '2.985971', 'Max drawdown': '0.039991', 'Transaction cost fraction': '0.011805'}
    assert table['ppo'] == {'Strategy': 'ppo', 'Total return': '0.197681', 'Sharpe': '2.473892', 'Max drawdown': '0.065857', 'Transaction cost fraction': '0.000484'}
    assert 'Validation ranking by Sharpe, best first: scenario_cvar > mean_variance > ppo > buy_and_hold > equal_weight > min_variance.' in narrative
    assert 'The recorded selection scenario_cvar is the top validation row by Sharpe (2.985971).' in narrative
    assert 'highest Sharpe: scenario_cvar (2.985971)' in narrative
    assert 'smallest max drawdown: scenario_cvar (0.039991)' in narrative
    assert 'lowest transaction cost fraction: buy_and_hold (0.0002); highest transaction cost fraction: scenario_cvar (0.011805)' in narrative
    seeds = _table(narrative, '| Algorithm |')
    assert seeds == [{'Algorithm': 'ppo', 'Seed': '1', 'Total return': '0.215897', 'Sharpe': '2.650944', 'Max drawdown': '0.056036'}]
    # The provider's mislabelled figure and inverted cost comparison never reach the user;
    # its figure-free qualitative sentence is kept, after the deterministic tables.
    assert '0.039991, which' not in narrative and 'lower max drawdown and transaction cost' not in narrative
    assert narrative.endswith('Historical scores apply only to the frozen experiment configurations.')
    assert 'Do not write any number' in provider.requests[-1].stable_instructions[-1]
    assert metric_label_check(narrative, comparison_cells(answer['evidence'][0]['summary'])).passed


def test_metric_label_check_rejects_a_figure_attached_to_the_wrong_metric_or_strategy():
    summary = selection_evidence(actual_experiment(), 4000)
    cells = comparison_cells(summary)
    assert cells['scenario_cvar']['sharpe'] == [2.985971]
    swapped = metric_label_check('The scenario_cvar strategy has a Sharpe ratio of 0.039991.', cells)
    assert not swapped.passed and swapped.unsupported == ['0.039991'] and REMOVED in swapped.text
    assert not metric_label_check('CVaR had a max drawdown of 0.065857.', cells).passed  # PPO's drawdown
    assert not metric_label_check('PPO had the transaction cost fraction 0.011805.', cells).passed  # CVaR's cost
    assert not metric_label_check('Minimum variance returned 4.00%.', cells).passed  # 0.039991 as a percentage
    # The numeric check alone accepts the swap: every figure exists somewhere in the result.
    assert claim_check('The scenario_cvar strategy has a Sharpe ratio of 0.039991.', [summary]).passed
    for correct in ('The scenario_cvar strategy has a Sharpe ratio of 2.985971 and a max drawdown of 0.039991.',
                    'scenario_cvar had the highest transaction cost fraction, 0.011805; PPO had 0.000484.',
                    'PPO seed 1 had a validation Sharpe of 2.650944.', 'scenario_cvar returned 20.88% on validation.',
                    'Across scenario_cvar and PPO the Sharpe values were 2.985971 and 2.473892.'):
        assert metric_label_check(correct, cells).passed, correct
    assert metric_label_check(render_selection_evidence(summary), cells).passed


def test_qualitative_commentary_drops_figures_and_metric_comparisons():
    kept, dropped = qualitative_commentary(MISLABELLED)
    assert kept == 'Historical scores apply only to the frozen experiment configurations.'
    assert len(dropped) == 2


def test_qualitative_commentary_drops_strategy_specific_and_availability_claims():
    # Observed live after the first fix: the provider called scenario_cvar's listed configuration unavailable.
    live = ('However, the scenario_cvar strategy was not selected for promotion due to a predeclared development period not being an untouched holdout. '
            'Historical scores apply only to the frozen experiment configurations and do not establish performance for differently configured daily optimizers. '
            'The configuration used for the scenario_cvar strategy is unavailable in the tool evidence. '
            'The configuration is not available. Minimum variance is a conservative choice. CVaR is tail-aware.')
    kept, dropped = qualitative_commentary(live, comparison_cells(selection_evidence(actual_experiment(), 4000)))
    assert kept == 'Historical scores apply only to the frozen experiment configurations and do not establish performance for differently configured daily optimizers.'
    assert len(dropped) == 5
