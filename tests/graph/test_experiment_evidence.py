"""Large synthetic MCP result follows the producer's reviewed model-selection shape."""
from __future__ import annotations

import json

from finplan_agent.graph.claim_check import claim_check
from finplan_agent.graph.experiment_evidence import selection_evidence
from finplan_agent.providers import FixtureProvider
from finplan_agent.providers.base import GenerateResult, Usage
from tests.fakes.agent import CHECKSUM, RUN, FakeToolClient, make_service, run


def experiment():
    rows = [{'strategy': strategy, 'total_return': value, 'sharpe': value + .5, 'max_drawdown': -.1,
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
