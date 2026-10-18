"""Recommendation run log: item builder, dedupe, daily cap and status derivation.

Stdlib only (plus a DynamoDB ``Table`` resource passed in), because this module is bundled with
:mod:`finplan_agent.recommendation_runs.trigger` into the inline trigger Lambda code
(``infra/stacks/recommendation_runs.py``). Keep it free of package imports.

Table ``finplan-<env>-financeagent-recommendation-runs``: one item per (run, portfolio),
``pk`` = run_id, ``sk`` = portfolio_id; GSI ``recent`` (``env``, ``started_key``) lists newest
first. Items reference immutable decisions by ``pd_`` id; they never copy them. Input identities
(formation, completed session, news window, export, artifact checksum, revision) come only from the
returned decision provenance, never from the clock. Floats are stored as ``Decimal``.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

METHODS = ('ppo', 'min_variance', 'mean_variance', 'cvar')
#: Producer precondition reasons that mean "not due yet" rather than a failure.
NOT_DUE_REASONS = frozenset({'morning_formation_not_due'})
MAX_TARGETS = 20
CAP_SK = 'cap'


def tool_request(method, portfolio_id, settings=None):
    """The exact tool and arguments ``scripts/daily_financial_report.py`` requests per method."""
    if method not in METHODS:
        raise ValueError('unsupported method')
    if method == 'ppo':
        return 'recommend_portfolio', {'portfolio_id': portfolio_id}
    return 'recommend_classical_portfolio', {'portfolio_id': portfolio_id, 'algorithm': method, 'settings': dict(settings or {})}


def scheduled_window(value, now):
    """Schedule time truncated to the hour, ISO UTC (``2026-10-17T16:00:00Z``)."""
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        at = at.astimezone(UTC) if at.tzinfo else None
    except ValueError:
        at = None
    return (at or now).strftime('%Y-%m-%dT%H:00:00Z')


def on_demand_run_id(now, hex8):
    return 'od-' + now.strftime('%Y%m%dT%H%M%SZ') + '-' + hex8


def new_york_date(now):
    """America/New_York calendar date of ``now`` (US DST rule; no tz database needed in Lambda)."""
    def sunday(month, nth):
        first = datetime(now.year, month, 1, tzinfo=UTC)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    summer = sunday(3, 2) + timedelta(hours=7) <= now < sunday(11, 1) + timedelta(hours=6)
    return (now - timedelta(hours=4 if summer else 5)).date().isoformat()


def excerpt(text, limit):
    data = str(text or '').encode()[:max(0, int(limit))]
    return data.decode(errors='ignore')


def to_item(value):
    """JSON-compatible value with floats as Decimal (DynamoDB rejects float)."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def leaks(value, secrets):
    text = json.dumps(value, default=str)
    return any(s and s in text for s in secrets)


def _error(final, tool):
    rows = ((final.get('answer') or {}).get('evidence') or [])
    errors = [r.get('error') for r in rows if r.get('tool') == tool and not r.get('ok') and r.get('error')]
    return final.get('error') or (errors[0] if errors else None)


def _code(error):
    if not isinstance(error, dict):
        return None
    reason = (error.get('details') or {}).get('reason')
    return reason if error.get('code') == 'PRECONDITION_FAILED' and reason else error.get('code')


def _record(answer):
    docs = [r.get('result') for r in answer.get('evidence') or [] if r.get('ok') and isinstance(r.get('result'), dict)]
    docs += list(answer.get('portfolio_decisions') or []) + list(answer.get('portfolio_analyses') or [])
    if answer.get('recommendation'):
        docs.append({'recommendation': answer['recommendation']})
    docs = [d for d in docs if isinstance(d, dict)]
    return next((d for d in docs if d.get('recommendation') and d.get('decision_id')), next((d for d in docs if d.get('recommendation')), {}))


def _targets(rec):
    rows = [(r.get('instrument_id'), r.get('weight')) for r in rec.get('target_weights') or [] if isinstance(r, dict)]
    if rec.get('cash_weight') is not None:
        rows.append(('CASH', rec['cash_weight']))
    rows = [(k, w) for k, w in rows if isinstance(k, str) and isinstance(w, (int, float)) and not isinstance(w, bool)]
    return dict(sorted(rows, key=lambda r: -r[1])[:MAX_TARGETS])


def method_entry(method, tool, final, session_id, portfolio_id, excerpt_bytes):
    """One ``methods`` map entry from a hosted ``final`` response (status issued/not_due/failed)."""
    answer = final.get('answer') or {}
    usage = final.get('usage') or {}
    error = _error(final, tool)
    record = _record(answer)
    rec = record.get('recommendation') or {}
    entry = {'tool': tool, 'session_id': session_id, 'decision_id': record.get('decision_id'), 'error_code': _code(error),
             'usage': {k: usage.get(k) for k in ('estimated_cost_usd', 'input_tokens', 'output_tokens')}}
    if entry['decision_id']:
        news = (rec.get('diagnostics') or {}).get('news_evidence') or {}
        prov = news.get('provenance') or {}
        entry.update(
            input_snapshot_id=rec.get('input_snapshot_id'), completed_session=rec.get('as_of'),
            formation_at=rec.get('decision_at') or news.get('decision_time'),
            news={'source_day': prov.get('source_day'), 'window_start': prov.get('window_start'), 'window_end': prov.get('window_end'),
                  'checksum': prov.get('document_checksum'), 'quality': prov.get('quality_status'), 'consumed': news.get('consumed')} if news else None,
            export_run_id=rec.get('export_run_id'), policy_artifact_checksum=rec.get('policy_artifact_checksum'),
            portfolio_revision=(rec.get('portfolio_state') or {}).get('revision'), targets=_targets(rec),
            narrative_excerpt=excerpt(answer.get('narrative'), excerpt_bytes), claim_check_passed=(answer.get('claim_check') or {}).get('passed'))
        if rec.get('strategy') not in (None, method):
            entry['error_code'] = 'requested_method_not_served'
        elif (rec.get('portfolio_state') or {}).get('portfolio_id') not in (None, portfolio_id):
            entry['error_code'] = 'portfolio_mismatch'
    if entry['decision_id'] and entry['error_code'] in (None, 'BUDGET_EXCEEDED'):
        entry['status'] = 'issued'
    elif not entry['decision_id'] and isinstance(error, dict) and error.get('code') == 'PRECONDITION_FAILED' and entry['error_code'] in NOT_DUE_REASONS:
        entry['status'] = 'not_due'
    else:
        entry['status'] = 'failed'
        entry['error_code'] = entry['error_code'] or 'HOSTED_EVIDENCE_CHECK_FAILED'
    return entry


def failed_entry(tool, code):
    return {'tool': tool, 'status': 'failed', 'decision_id': None, 'error_code': code}


def portfolio_status(methods):
    states = [m.get('status') for m in methods.values()]
    if states and all(s == 'issued' for s in states):
        return 'completed'
    if 'issued' in states:
        return 'partial'
    if states and all(s == 'not_due' for s in states):
        return 'not_due'
    return 'failed'


def run_status(statuses):
    unique = set(statuses)
    return unique.pop() if len(unique) == 1 else ('partial' if unique else 'failed')


class RunLog:
    """Writes of one invocation to the run table (a boto3 DynamoDB ``Table`` resource)."""

    def __init__(self, table, env):
        self.table, self.env = table, env

    def begin(self, run_id, portfolio_id, started_at, trigger, window, methods):
        """Conditional first write; False when this (run, portfolio) already exists (duplicate)."""
        item = {'pk': run_id, 'sk': portfolio_id, 'run_id': run_id, 'portfolio_id': portfolio_id, 'env': self.env,
                'started_key': started_at + '#' + run_id, 'started_at': started_at, 'trigger': trigger, 'window': window,
                'status': 'running', 'requested_methods': list(methods), 'methods': {}}
        try:
            self.table.put_item(Item=item, ConditionExpression='attribute_not_exists(pk)')
        except Exception as error:  # botocore ClientError; no botocore import in the inline bundle
            if _client_code(error) == 'ConditionalCheckFailedException':
                return None
            raise
        return item

    def take_cap(self, now, cap):
        """Count one run against the America/New_York daily cap; False when the cap is reached."""
        try:
            self.table.update_item(Key={'pk': 'cap#' + new_york_date(now), 'sk': CAP_SK}, UpdateExpression='ADD n :one',
                                   ConditionExpression='attribute_not_exists(n) OR n < :cap', ExpressionAttributeValues={':one': 1, ':cap': int(cap)})
        except Exception as error:
            if _client_code(error) == 'ConditionalCheckFailedException':
                return False
            raise
        return True

    def finish(self, item):
        self.table.put_item(Item=to_item(item))


def _client_code(error):
    return ((getattr(error, 'response', None) or {}).get('Error') or {}).get('Code')
