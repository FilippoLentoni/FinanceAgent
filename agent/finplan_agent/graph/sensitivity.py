"""Bounded hypothetical optimizer alternatives frozen to an issued decision's holdings."""
from __future__ import annotations

import math

from ..providers.base import ToolCall


def sensitivity_plan(workflow, results, turn):
    if any(not row.get('ok') for row in results):
        return {'workflow': workflow, 'calls': []}
    saved = next((row['result'].get('decision') for row in results if row.get('tool') == 'get_portfolio_decision'), None)
    if not saved:
        return {'workflow': workflow, 'calls': []}
    rec = saved.get('recommendation', {})
    settings = rec.get('settings', {})
    book = rec.get('portfolio_state', {})
    variants = workflow['variants']
    reason = None
    if rec.get('strategy') not in ('min_variance', 'mean_variance', 'cvar'):
        reason = 'PPO sensitivity to objective or feature changes requires a separately reviewed experiment; optimizer alternatives cannot explain a changed PPO policy.'
    elif not book.get('positions') or not book.get('portfolio_value') or not rec.get('input_snapshot_id') or not rec.get('as_of'):
        reason = 'The issued recommendation lacks frozen marked holdings needed for a controlled sensitivity analysis.'
    elif any(v['parameter'] not in settings or not math.isclose(settings[v['parameter']], v['base'], abs_tol=1e-8) for v in variants):
        reason = 'The stated base values do not match the stored optimizer settings. Use the issued settings as the sensitivity baseline.'
    elif any(not math.isfinite(v['value']) or not (0 < v['value'] <= 100 if v['parameter'] == 'risk_aversion' else 0.2 <= v['value'] <= 1) for v in variants):
        reason = 'Alternative settings exceed the supported optimizer bounds.'
    if reason:
        return {'workflow': {'mode': 'lifecycle_complete'}, 'calls': [], 'clarification': reason}
    if any(row.get('tool') == 'recommend_classical_portfolio' for row in results):
        return {'workflow': workflow, 'calls': []}
    nav = book['portfolio_value']
    holdings = {'weights': [{'instrument_id': pos['instrument_id'], 'weight': pos['quantity'] * pos['reference_price'] / nav} for pos in book['positions']],
                'cash_weight': book['current_cash'] / nav, 'portfolio_value': nav, 'high_watermark': book['high_watermark']}
    calls = []
    for index, variant in enumerate(variants):
        arguments = {'algorithm': rec['strategy'], 'input_snapshot_id': rec['input_snapshot_id'], 'as_of': rec['as_of'],
                     'holdings': holdings, 'settings': {**settings, variant['parameter']: variant['value']}}
        calls.append(ToolCall(id=f'sensitivity-{turn}-{index}', name='recommend_classical_portfolio', arguments=arguments))
    return {'workflow': {**workflow, 'source_decision_id': saved['decision_id']}, 'calls': calls}
