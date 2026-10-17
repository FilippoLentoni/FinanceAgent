"""Recommendation trigger Lambda (``finplan-<env>-financeagent-recommendation-trigger``).

Stdlib + boto3 only; bundled with :mod:`.runlog` into the inline Lambda code at synth time
(``infra/stacks/recommendation_runs.py``), so only ``from .runlog import`` may reference package code.

Mirrors ``scripts/daily_financial_report.py`` ``report_token``/``HostedReader``: the read-only CI
machine client (secret named by ``/finplan/<env>/financeagent/secret-ref/ci-test-client``, token
endpoint from ``authorizer-metadata-ref``) obtains a client-credentials token, then the deployed
Runtime (``agent/runtime-ref``) is called with one deterministic ``tool_request`` per method, one
session per call, strictly sequentially. It only requests recommendations and records them: it never
confirms, resolves, trades, trains or refreshes data, and never logs or stores a credential.

Events: scheduled ``{"trigger": "scheduled", "window": "<aws.scheduler.scheduled-time>"}``;
on demand ``{"trigger": "on_demand", "portfolio_ids": [...]?, "methods": [...]?}``.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime

from .runlog import METHODS, RunLog, failed_entry, leaks, method_entry, on_demand_run_id, portfolio_status, run_status, scheduled_window, tool_request

STOP_CODES = frozenset({'BUDGET_EXCEEDED'})
REFS = {'meta': '/finplan/{}/financeagent/agent/authorizer-metadata-ref', 'secret': '/finplan/{}/financeagent/secret-ref/ci-test-client',
        'runtime': '/finplan/{}/financeagent/agent/runtime-ref'}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def machine_token(ssm, secrets, env, opener):
    """Client-credentials token of the read-only CI machine client (``report_token`` ci mode)."""
    meta = json.loads(ssm.get_parameter(Name=REFS['meta'].format(env))['Parameter']['Value'])
    endpoint = urllib.parse.urlsplit(meta['token_endpoint'])
    if endpoint.scheme != 'https' or not (endpoint.hostname or '').endswith('.amazoncognito.com') or endpoint.path != '/oauth2/token' or endpoint.query:
        raise ValueError('invalid token endpoint')
    secret_id = ssm.get_parameter(Name=REFS['secret'].format(env))['Parameter']['Value']
    secret = json.loads(secrets.get_secret_value(SecretId=secret_id)['SecretString'])
    basic = base64.b64encode((secret['client_id'] + ':' + secret['client_secret']).encode()).decode()
    request = urllib.request.Request(meta['token_endpoint'], data=b'grant_type=client_credentials',
                                     headers={'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'})
    with opener.open(request, timeout=30) as response:
        token = json.load(response)
    if str(token.get('token_type', '')).lower() != 'bearer' or not token.get('access_token'):
        raise ValueError('invalid machine-token response')
    return token['access_token'], [secret['client_secret'], basic]


def invoke(opener, url, token, tool, arguments, timeout):
    """One hosted ``tool_request``; returns (session_id, final) with failures as error envelopes."""
    sid = 'recommendation_trigger_' + uuid.uuid4().hex
    payload = {'tool_request': {'name': tool, 'arguments': arguments}, 'stream': False}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sid})
    try:
        with opener.open(request, timeout=timeout) as response:
            final = json.load(response)
    except urllib.error.HTTPError as error:
        final = {'status': 'failed', 'error': {'code': 'HOSTED_HTTP_' + str(error.code)}}
    except (OSError, ValueError):
        final = {'status': 'failed', 'error': {'code': 'HOSTED_TRANSPORT_ERROR'}}
    return sid, final if isinstance(final, dict) else {'status': 'failed', 'error': {'code': 'HOSTED_RESPONSE_INVALID'}}


def config(environ):
    return {'env': environ['FINPLAN_ENV'], 'table': environ['RUNS_TABLE'], 'portfolio_ids': json.loads(environ['PORTFOLIO_IDS']),
            'methods': json.loads(environ['METHODS']), 'daily_cap': int(environ['DAILY_CAP']),
            'excerpt_bytes': int(environ['EXCERPT_BYTES']), 'settings': json.loads(environ.get('CLASSICAL_SETTINGS') or '{}'),
            'region': environ.get('AWS_REGION', 'us-east-2')}


def run(event, cfg, table, ssm, secrets, opener, now, remaining_ms=lambda: 900000):
    """Execute one trigger event; returns the response document (never raises for hosted failures)."""
    event = event if isinstance(event, dict) else {}
    trigger = event.get('trigger')
    portfolios = event.get('portfolio_ids') or cfg['portfolio_ids']
    methods = event.get('methods') or cfg['methods']
    if (trigger not in ('scheduled', 'on_demand') or not isinstance(portfolios, list) or not isinstance(methods, list)
            or not set(portfolios) <= set(cfg['portfolio_ids']) or not set(methods) <= set(METHODS) or trigger == 'scheduled' and 'portfolio_ids' in event):
        return {'run_id': None, 'status': 'failed', 'error_code': 'INVALID_REQUEST', 'portfolios': []}
    methods = [m for m in METHODS if m in methods]
    window = scheduled_window(event.get('window'), now) if trigger == 'scheduled' else None
    run_id = 'sched-' + window if window else on_demand_run_id(now, uuid.uuid4().hex[:8])
    started = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    log, state, out = RunLog(table, cfg['env']), {}, []
    for pid in dict.fromkeys(portfolios):
        item = log.begin(run_id, pid, started, trigger, window, methods)
        if item is None:
            out.append({'portfolio_id': pid, 'status': 'duplicate', 'methods': {}})
            continue
        if not log.take_cap(now, cfg['daily_cap']):
            item['status'] = 'cap_reached'
        else:
            item['methods'] = _methods(cfg, ssm, secrets, opener, pid, methods, state, remaining_ms)
            item['status'] = portfolio_status(item['methods'])
            codes = {m['error_code'] for m in item['methods'].values() if m['status'] == 'failed'}
            if item['status'] == 'failed' and len(codes) == 1:
                item['error_code'] = codes.pop()
        item['finished_at'] = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        if leaks(item, state.get('secrets', [])):
            item = {k: item[k] for k in ('pk', 'sk', 'run_id', 'portfolio_id', 'env', 'started_key', 'started_at', 'finished_at', 'trigger', 'window', 'requested_methods')}
            item.update(status='failed', error_code='CREDENTIAL_ECHO_REDACTED', methods={})
        log.finish(item)
        out.append({'portfolio_id': pid, 'status': item['status'],
                    'methods': {m: {k: e.get(k) for k in ('status', 'decision_id', 'error_code')} for m, e in item['methods'].items()}})
    return {'run_id': run_id, 'status': run_status([p['status'] for p in out]), 'portfolios': out}


def _methods(cfg, ssm, secrets, opener, pid, methods, state, remaining_ms):
    if 'token' not in state:
        try:
            state['token'], state['secrets'] = machine_token(ssm, secrets, cfg['env'], opener)
            runtime = ssm.get_parameter(Name=REFS['runtime'].format(cfg['env']))['Parameter']['Value']
            state['url'] = 'https://bedrock-agentcore.' + cfg['region'] + '.amazonaws.com/runtimes/' + urllib.parse.quote(runtime, safe='') + '/invocations?qualifier=DEFAULT'
            state['secrets'].append(state['token'])
        except Exception:  # exception text may carry request details; record only a code
            state['token'] = None
    out = {}
    for method in methods:
        tool, arguments = tool_request(method, pid, cfg['settings'])
        budget = remaining_ms() / 1000 - 60
        if not state['token']:
            out[method] = failed_entry(tool, 'HOSTED_INITIALIZATION_UNAVAILABLE')
        elif state.get('stop'):
            out[method] = failed_entry(tool, 'SKIPPED_AFTER_' + state['stop'])
        elif budget < 30:
            out[method] = failed_entry(tool, 'LAMBDA_TIME_EXHAUSTED')
        else:
            sid, final = invoke(opener, state['url'], state['token'], tool, arguments, min(420, budget))
            if leaks(final, state['secrets']):
                final = {'status': 'failed', 'error': {'code': 'CREDENTIAL_ECHO_REDACTED'}}
            out[method] = method_entry(method, tool, final, sid, pid, cfg['excerpt_bytes'])
            if out[method]['error_code'] in STOP_CODES:
                state['stop'] = out[method]['error_code']
        print(json.dumps({'portfolio_id': pid, 'method': method, 'status': out[method]['status'], 'error_code': out[method]['error_code']}))
    return out


def handler(event, context):
    import boto3
    cfg = config(os.environ)
    table = boto3.resource('dynamodb', region_name=cfg['region']).Table(cfg['table'])
    result = run(event, cfg, table, boto3.client('ssm', region_name=cfg['region']), boto3.client('secretsmanager', region_name=cfg['region']),
                 urllib.request.build_opener(_NoRedirect), datetime.now(UTC), context.get_remaining_time_in_millis)
    print(json.dumps({'run_id': result['run_id'], 'status': result['status']}))
    return result
