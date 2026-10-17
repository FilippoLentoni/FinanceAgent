"""Bounded, reviewed model-selection evidence for narration and checkpoints."""
from __future__ import annotations

import json
import math
import re


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


# ---------------------------------------------------------------------------
# Deterministic rendering of reviewed model-selection evidence.
#
# A provider once narrated scenario_cvar's validation max drawdown as its "Sharpe
# ratio" and inverted the transaction-cost comparison: every figure existed somewhere
# in the tool result, so the numeric claim check passed. Metric tables, rankings and
# per-metric leaders are therefore rendered here, with each figure printed beside its
# strategy and source field. A provider may only add qualitative context (see
# ``qualitative_commentary``).

METRIC_LABELS = {'total_return': 'Total return', 'sharpe': 'Sharpe', 'max_drawdown': 'Max drawdown',
                 'transaction_cost_fraction': 'Transaction cost fraction'}
# Direction in which a metric is better; drawdowns are reported as positive fractions.
_BETTER_HIGH = {'total_return': True, 'sharpe': True, 'max_drawdown': False, 'transaction_cost_fraction': False}
_CONTROLS = frozenset({'cash'})


def reviewed_selection(summary) -> dict | None:
    """The reviewed selection summary when it carries the validation metric table."""
    if not isinstance(summary, dict) or summary.get('evidence_representation') != 'bounded_validation_excerpt':
        return None
    comparison = (summary.get('model_selection') or {}).get('validation_comparison') or {}
    columns = comparison.get('columns') or []
    if not columns or columns[0] != 'strategy' or not isinstance(comparison.get('rows'), list):
        return None
    return summary


def fmt(value) -> str:
    if value is None:
        return 'n/a'
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, float):
        return format(value, '.10g')
    return str(value)


def comparison_cells(summary: dict) -> dict[str, dict[str, list[float]]]:
    """{strategy: {metric: [values]}} from the reviewed tables, including per-seed rows
    (keyed by algorithm) and promotion test figures (keyed by candidate/incumbent)."""
    view = (summary or {}).get('model_selection') or {}
    cells: dict[str, dict[str, list[float]]] = {}

    def put(strategy, metric, value):
        if isinstance(strategy, str) and _number(value) is not None:
            cells.setdefault(strategy, {}).setdefault(metric, []).append(float(value))

    for block_name in ('validation_comparison', 'validation_seeds'):
        block = view.get(block_name) or {}
        columns = block.get('columns') or []
        for row in block.get('rows') or []:
            if not isinstance(row, list) or len(row) != len(columns):
                continue
            record = dict(zip(columns, row, strict=True))
            name = record.get('strategy', record.get('algorithm'))
            for metric in METRIC_LABELS:
                put(name, metric, record.get(metric))
    promotion = view.get('promotion_check') or {}
    for role in ('candidate', 'incumbent'):
        for metric, value in (promotion.get(role + '_test') or {}).items():
            put(promotion.get(role), metric, value)
    return cells


def _table(columns, rows, labels) -> list[str]:
    lines = ['| ' + ' | '.join(labels.get(c, c.replace('_', ' ').capitalize()) for c in columns) + ' |',
             '|' + '---|' * len(columns)]
    for row in rows:
        if isinstance(row, list) and len(row) == len(columns):
            lines.append('| ' + ' | '.join(fmt(v) for v in row) + ' |')
    return lines


def render_selection_evidence(summary: dict) -> str:
    """Source-labelled, deterministic explanation of a reviewed model-selection result."""
    view = summary.get('model_selection') or {}
    comparison = view['validation_comparison']
    columns, rows = comparison['columns'], [r for r in comparison['rows'] if isinstance(r, list) and len(r) == len(comparison['columns'])]
    records = [dict(zip(columns, row, strict=True)) for row in rows]
    out = [f"**Recorded model-selection evidence** for run {summary.get('run_id', 'unknown')} "
           f"(completion status: {summary.get('completion_status', 'unknown')}; artifacts complete: {fmt(summary.get('artifacts_complete'))}). "
           'Every figure below is copied from the tool result field named beside it.']
    selection = view.get('selection') or {}
    rule = selection.get('rule') or {}
    selected = selection.get('selected') or {}
    metric = rule.get('metric')
    if selection:
        text = f"Selection rule (model_selection.selection.rule): {METRIC_LABELS.get(metric, metric or 'unspecified metric')} on the {rule.get('split', 'unspecified')} split"
        text += f" ({rule['direction']})." if rule.get('direction') else '.'
        text += f" Selected (model_selection.selection.selected): {selected.get('strategy', 'none recorded')}"
        if selected.get('family'):
            text += f", family {selected['family']}"
        if selected.get('seed') is not None:
            text += f", seed {fmt(selected['seed'])}"
        text += '.'
        if selection.get('incumbent'):
            text += f" Incumbent: {selection['incumbent']}."
        out.append(text)
    splits = view.get('splits') or {}
    if splits:
        out.append('Splits (model_selection.splits): ' + '; '.join(
            f"{name} {block.get('start', '?')} to {block.get('end', '?')}, {fmt(block.get('sessions'))} sessions"
            for name, block in splits.items() if isinstance(block, dict)) + '.')
    evaluation = view.get('evaluation') or {}
    if evaluation:
        out.append('Common evaluation settings (model_selection.evaluation): ' + ', '.join(
            f"{key.replace('_', ' ')} {fmt(value)}" for key, value in evaluation.items()) + '.')
    out.extend(['', 'Validation metrics by strategy (model_selection.validation_comparison; ' + str(comparison.get('units', 'units as recorded')).rstrip('.') + '):', ''])
    out.extend(_table(columns, rows, {'strategy': 'Strategy', **METRIC_LABELS}))
    if metric in columns:
        magnitude = (lambda r: abs(r[metric])) if metric == 'max_drawdown' else (lambda r: r[metric])
        higher = rule.get('direction', '').startswith('higher') if rule.get('direction') else _BETTER_HIGH.get(metric, True)
        ranked = sorted((r for r in records if _number(r.get(metric)) is not None), key=magnitude, reverse=higher)
        if ranked:
            out.extend(['', f"Validation ranking by {METRIC_LABELS.get(metric, metric)}, best first: " + ' > '.join(r['strategy'] for r in ranked) + '.'])
            unranked = [r['strategy'] for r in records if _number(r.get(metric)) is None]
            if unranked:
                out[-1] += ' No value recorded for: ' + ', '.join(unranked) + '.'
            top = ranked[0]
            if selected.get('strategy') == top['strategy']:
                out.append(f"The recorded selection {top['strategy']} is the top validation row by {METRIC_LABELS.get(metric, metric)} ({fmt(top[metric])}).")
            elif selected.get('strategy'):
                row = next((r for r in records if r['strategy'] == selected['strategy']), None)
                out.append(f"The recorded selection {selected['strategy']} is not the top row of this table by {METRIC_LABELS.get(metric, metric)} (top: {top['strategy']}, {fmt(top[metric])}"
                           + (f"; {selected['strategy']}: {fmt(row.get(metric))}" if row else '; the selected strategy has no row here') + ').')
    leaders = []
    candidates = [r for r in records if r.get('strategy') not in _CONTROLS]
    for name in columns[1:]:
        if name not in METRIC_LABELS:
            continue
        valued = [r for r in candidates if _number(r.get(name)) is not None]
        if len(valued) < 2:
            continue
        # Drawdowns may be recorded as positive or negative fractions: compare magnitudes.
        key = (lambda r, n=name: abs(r[n])) if name == 'max_drawdown' else (lambda r, n=name: r[n])
        best = max(valued, key=key) if _BETTER_HIGH[name] else min(valued, key=key)
        worst = min(valued, key=key) if _BETTER_HIGH[name] else max(valued, key=key)
        word = ('highest', 'lowest') if _BETTER_HIGH[name] else ('smallest', 'largest') if name == 'max_drawdown' else ('lowest', 'highest')
        label = METRIC_LABELS[name] if name == 'sharpe' else METRIC_LABELS[name].lower()
        leaders.append(f"{word[0]} {label}: {best['strategy']} ({fmt(best[name])}); {word[1]} {label}: {worst['strategy']} ({fmt(worst[name])})")
    if leaders:
        out.extend(['', 'Per-metric extremes on validation, excluding the cash control: ' + '; '.join(leaders) + '.'])
    seeds = view.get('validation_seeds') or {}
    if seeds.get('rows'):
        out.extend(['', 'Per-seed validation metrics (model_selection.validation_seeds; each seed is one ensemble member, not the aggregate row above):', ''])
        out.extend(_table(seeds['columns'], seeds['rows'], {'algorithm': 'Algorithm', 'seed': 'Seed', **METRIC_LABELS}))
    params = view.get('validation_parameters') or {}
    if params:
        out.extend(['', 'Recorded configuration (model_selection.validation_parameters): ' + '; '.join(
            f"{name}: " + ', '.join(f"{key} {fmt(value)}" for key, value in block.items()) for name, block in params.items()) + '. Settings not listed are unavailable in this evidence.'])
    access = view.get('test_access') or {}
    if access:
        out.extend(['', 'Test access (model_selection.test_access): ' + ', '.join(f"{key.replace('_', ' ')} {fmt(value)}" for key, value in access.items()) + '.'])
    promotion = view.get('promotion_check') or {}
    if promotion:
        text = (f"Promotion check (model_selection.promotion_check): candidate {promotion.get('candidate')} versus incumbent {promotion.get('incumbent')} "
                f"on the {promotion.get('split', 'unspecified')} split; result {promotion.get('result', 'unknown')}")
        text += f", reason {promotion['reason']}" if promotion.get('reason') else ''
        text += f"; requires user approval {fmt(promotion.get('requires_user_approval'))}." if 'requires_user_approval' in promotion else '.'
        for role in ('candidate', 'incumbent'):
            figures = promotion.get(role + '_test') or {}
            if figures:
                text += f" {promotion.get(role)} test figures ({role}_test): " + ', '.join(f"{METRIC_LABELS.get(k, k.replace('_', ' '))} {fmt(v)}" for k, v in figures.items()) + '.'
        out.extend(['', text])
    caveats = view.get('caveats') or []
    if caveats:
        out.extend(['', 'Recorded caveats (model_selection.caveats, excerpted):'] + [f"- {row.get('kind', 'caveat')}: {row.get('text', '')}…" for row in caveats if isinstance(row, dict)])
    if summary.get('metric_evidence_status'):
        out.extend(['', 'Metric evidence status: ' + str(summary['metric_evidence_status']) + '; some metric tables did not fit the result budget.'])
    out.extend(['', 'Scope: ' + str(summary.get('evidence_scope', 'Historical evaluation only.')) +
                ' A validation-period selection is frozen historical evidence, not deployment readiness or independent forward performance.'])
    return '\n'.join(out)


COMMENTARY_INSTRUCTION = (
    'The metric tables, rankings, selection facts and settings for this experiment are rendered deterministically from the '
    'tool result and shown to the user before your text. Add at most four sentences of qualitative context only: what the '
    'evidence scope means (frozen selection versus deployment readiness, reused development/test history, lack of independent '
    'forward evidence, configuration differences). Do not write any number, date or metric value, do not name or characterise '
    'individual strategies, and do not compare or rank strategies on any metric or say which evidence is unavailable.')

_DIGIT = re.compile(r'\d')
_METRIC_WORD = re.compile(r'sharpe|drawdown|cost|return|volatil|turnover|cagr|risk.adjusted|performance|perform', re.I)
_COMPARATIVE = re.compile(r'\b(?:higher|lower|highest|lowest|better|worse|best|worst|more|less|most|least|greater|smaller|'
                          r'larger|outperform\w*|underperform\w*|beat\w*|exceed\w*|superior|inferior|favou?r\w*|stronger|weaker|top|leading)\b', re.I)
# Availability is stated deterministically above; a provider once called a listed configuration unavailable.
_AVAILABILITY = re.compile(r'\b(?:unavailable|not available|missing|absent|not (?:present|provided|recorded|included|listed|reported))\b', re.I)
_SENTENCE = re.compile(r'(?<=[.!?])\s+')


def qualitative_commentary(text: str, strategies=()) -> tuple[str, list[str]]:
    """Keep only strategy-agnostic provider sentences without figures, metric comparisons
    or availability claims; strategy-specific facts come only from the rendered tables."""
    from .claim_check import strategy_pattern
    names = [strategy_pattern(name) for name in strategies]
    kept, dropped = [], []
    for sentence in _SENTENCE.split((text or '').strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if (_DIGIT.search(sentence) or (_METRIC_WORD.search(sentence) and _COMPARATIVE.search(sentence))
                or _AVAILABILITY.search(sentence) or any(p.search(sentence) for p in names)):
            dropped.append(sentence)
        else:
            kept.append(sentence)
    return ' '.join(kept), dropped
