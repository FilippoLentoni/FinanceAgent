"""Bounded, reviewed model-selection evidence for narration and checkpoints."""
from __future__ import annotations

import json
import math


def _number(value):
    return value if value is None or isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def selection_evidence(result: dict, max_chars: int) -> dict | None:
    """Preserve original validation figures rather than a large payload's list counts.

    The MCP producer already supplies this reviewed model_selection projection. Keep
    its common evaluation and per-seed validation metrics inside the existing result
    budget; never reinterpret a selected configuration as a forward-performance claim.
    Additional optional sections are included only when they fit the same budget.
    """
    if not isinstance(result, dict):
        return None
    selection = result.get('model_selection')
    if not isinstance(selection, dict):
        return None
    summary = {key: result[key] for key in ('run_id', 'completion_status', 'solution_status', 'artifacts_complete',
               'input_snapshot_id', 'configuration_id', 'dataset_checksum', 'evaluator_version') if key in result}
    summary['evidence_scope'] = 'Scores cover frozen configurations, not differently configured daily optimizers. Selected-seed scores do not certify an ensemble. Reused history is not independent forward evidence. Structured split dates/session counts override conflicting prose.'
    summary['evidence_representation'] = 'bounded_validation_excerpt'
    summary['model_selection'] = {}
    if len(json.dumps(summary, sort_keys=True, default=str)) > max_chars:
        return None
    view = summary['model_selection']

    def add(key, value):
        if value is None:
            return
        view[key] = value
        if len(json.dumps(summary, sort_keys=True, default=str)) > max_chars:
            del view[key]

    add('universe', selection.get('universe'))
    add('splits', selection.get('splits'))
    add('evaluation', selection.get('evaluation'))
    add('selection', {key: value for key, value in (selection.get('selection') or {}).items() if key != 'validation_ranking'})
    add('test_access', selection.get('test_access'))
    artifacts = result.get('artifacts') or []
    artifact = next((row for row in artifacts if isinstance(row, dict) and row.get('checksum')), None)
    if artifact:
        add('source_artifact', {key: artifact[key] for key in ('artifact_id', 'checksum', 'kind') if key in artifact})
    columns = ['strategy', 'total_return', 'sharpe', 'max_drawdown', 'transaction_cost_fraction']
    rows = (selection.get('by_split') or {}).get('validation') or []
    add('validation_comparison', {'columns': columns, 'rows': [[row.get('strategy'), *[_number(row.get(key)) for key in columns[1:]]]
        for row in rows[:12] if isinstance(row, dict)], 'units': 'Returns, drawdowns and costs are fractions; Sharpe is a ratio.'})
    seed_columns = ['seed', 'total_return', 'sharpe', 'max_drawdown']
    seeds = []
    for algorithm, block in list((selection.get('rl') or {}).items())[:3]:
        for seed in (block.get('seeds') or [])[:8]:
            values = seed.get('validation') or {}
            seeds.append([algorithm, _number(seed.get('seed')), *[_number(values.get(key)) for key in seed_columns[1:]]])
    add('validation_seeds', {'columns': ['algorithm', *seed_columns], 'rows': seeds})
    add('validation_parameters', {row['strategy']: row['params'] for row in rows[:12] if isinstance(row, dict) and isinstance(row.get('strategy'), str) and isinstance(row.get('params'), dict) and row['params']})
    add('promotion_check', selection.get('promotion_check'))
    caveats = selection.get('caveats') or []
    add('caveats', [{key: row[key][:160] for key in ('kind', 'text') if isinstance(row.get(key), str)} for row in caveats[:2] if isinstance(row, dict)])
    # Do not quietly present a selected label as a numerical comparison if a very
    # small custom result budget could not retain the original metric tables.
    if 'validation_comparison' not in view or 'validation_seeds' not in view:
        summary['metric_evidence_status'] = 'incomplete_under_existing_result_budget'
    if len(json.dumps(summary, sort_keys=True, default=str)) > max_chars:
        return None
    return summary
