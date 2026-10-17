"""Read-only hosted LangGraph recommendations with frozen cross-method evidence.

The token/config and outputs are private operator files. This runner never confirms,
resolves, trades, trains, or refreshes market data. Missing methods/history stay visible.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import math
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'agent')]
from finplan_agent.config.settings import SsmNames  # noqa: E402
from finplan_agent.graph.activity import sanitize  # noqa: E402
from scripts.beta_login import NoRedirect, validate_metadata  # noqa: E402
from scripts.daily_paper_validation import token_for  # noqa: E402

METHODS = ('ppo', 'min_variance', 'mean_variance', 'cvar')
READ_TOOLS = frozenset({'recommend_portfolio', 'recommend_classical_portfolio', 'list_portfolio_decisions',
                        'get_portfolio_decision', 'explain_portfolio_decision', 'compare_portfolio_decisions',
                        'evaluate_portfolio_decision', 'research_market_events'})


def private_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write('\n')
    temporary.replace(path)


def report_token(session, config: dict) -> str:
    if config.get('auth_mode', 'user_refresh_token') == 'user_refresh_token':
        return token_for(session, config)
    if config['auth_mode'] != 'ci_client_credentials':
        raise ValueError('unsupported report authentication mode')
    # Existing read-only CI machine client; never impersonate a human or resolve decisions.
    names = SsmNames('beta')
    ssm = session.client('ssm')
    meta = json.loads(ssm.get_parameter(Name=names.authorizer_metadata_ref)['Parameter']['Value'])
    validate_metadata(meta)
    secret_ref = ssm.get_parameter(Name=names.ci_test_client_secret_ref)['Parameter']['Value']
    secret = json.loads(session.client('secretsmanager').get_secret_value(SecretId=secret_ref)['SecretString'])
    basic = base64.b64encode((secret['client_id'] + ':' + secret['client_secret']).encode()).decode()
    request = urllib.request.Request(meta['token_endpoint'], data=b'grant_type=client_credentials', headers={
        'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        token = json.load(response)
    if token.get('token_type', '').lower() != 'bearer' or not token.get('access_token'):
        raise ValueError('invalid machine-token response')
    return token['access_token']


class UnavailableReader:
    def __init__(self, error: dict, output: Path):
        self.error, self.output = error, output

    def call(self, label: str, tool: str, arguments: dict) -> dict:
        if tool not in READ_TOOLS:
            raise ValueError('daily reports permit read-only evidence tools only')
        result = {'status': 'not_available', 'tool': tool, 'error': self.error, 'receipt': label + '.json'}
        private_json(self.output / result['receipt'], {'payload': {'tool': tool, 'arguments': arguments}, **result})
        return result


class HostedReader:
    def __init__(self, session, config: dict, output: Path):
        self.session, self.config, self.output = session, config, output
        self.token = report_token(session, config)
        runtime = session.client('ssm').get_parameter(Name='/finplan/beta/financeagent/agent/runtime-ref')['Parameter']['Value']
        region = session.region_name or 'us-east-2'
        self.url = 'https://bedrock-agentcore.' + region + '.amazonaws.com/runtimes/' + urllib.parse.quote(runtime, safe='') + '/invocations?qualifier=DEFAULT'
        self.sequence = 0

    def call(self, label: str, tool: str, arguments: dict) -> dict:
        if tool not in READ_TOOLS:
            raise ValueError('daily reports permit read-only evidence tools only')
        self.sequence += 1
        print(json.dumps({'stage': label, 'tool': tool}), flush=True)
        payload = {'tool_request': {'name': tool, 'arguments': arguments}, 'stream': False}
        sid = 'daily_financial_report_' + uuid.uuid4().hex
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sid})
        try:
            with urllib.request.urlopen(request, timeout=420) as response:
                final = json.load(response)
        except urllib.error.HTTPError as error:
            final = {'status': 'failed', 'error': {'code': 'HOSTED_HTTP_' + str(error.code),
                     'message': 'Hosted invocation failed; response retained in private receipt.',
                     'body': error.read(65536).decode(errors='replace')}}
        except (OSError, ValueError) as error:
            final = {'status': 'failed', 'error': {'code': 'HOSTED_TRANSPORT_ERROR', 'message': type(error).__name__}}
        # The runtime normally redacts secrets itself; never persist a bearer even on an error.
        if self.token in json.dumps(final, default=str):
            final = {'status': 'failed', 'error': {'code': 'CREDENTIAL_ECHO_REDACTED'}}
        receipt = sanitize({'payload': payload, 'session_id': sid, 'final': final})
        private_json(self.output / f'{self.sequence:02d}-{label}.json', receipt)
        answer = final.get('answer') or {}
        evidence = answer.get('evidence') or []
        matching = [row for row in evidence if row.get('tool') == tool]
        valid = (final.get('status') == 'completed' and answer.get('claim_check', {}).get('passed') is True
                 and bool(matching) and all(row.get('ok') for row in evidence))
        if not valid:
            errors = [row.get('error') for row in evidence if not row.get('ok')]
            return {'status': 'not_available', 'tool': tool, 'error': sanitize(final.get('error') or errors or
                    {'code': 'HOSTED_EVIDENCE_CHECK_FAILED', 'status': final.get('status')}), 'receipt': f'{self.sequence:02d}-{label}.json'}
        return {'status': 'available', 'tool': tool, 'answer': answer, 'receipt': f'{self.sequence:02d}-{label}.json'}


def producer_documents(result: dict) -> list[dict]:
    answer = result.get('answer') or {}
    documents = [row.get('result') for row in answer.get('evidence', []) if row.get('ok') and isinstance(row.get('result'), dict)]
    documents.extend(answer.get('portfolio_decisions') or [])
    documents.extend(answer.get('portfolio_analyses') or [])
    if answer.get('recommendation'):
        documents.append({'recommendation': answer['recommendation']})
    return documents


def recommendation_record(result: dict) -> dict:
    documents = producer_documents(result)
    return next((row for row in documents if row.get('recommendation') and row.get('decision_id')),
                next((row for row in documents if row.get('recommendation')), {}))


def collection(result: dict, key: str) -> list[dict]:
    return next((row[key] for row in producer_documents(result) if isinstance(row.get(key), list)), [])


def check_recommendation(record: dict, method: str, portfolio_id: str, reference: dict | None, expected: set[str]) -> list[str]:
    rec = record.get('recommendation') or {}
    state = rec.get('portfolio_state') or {}
    problems = []
    if rec.get('strategy') != method:
        problems.append('requested_method_not_served')
    if state.get('portfolio_id') != portfolio_id:
        problems.append('portfolio_mismatch')
    if not record.get('decision_id'):
        problems.append('immutable_decision_id_missing')
    if not all(rec.get(key) for key in ('input_snapshot_id', 'snapshot_checksum', 'as_of')):
        problems.append('market_provenance_missing')
    weights = [row.get('weight') for row in rec.get('target_weights', [])] + [rec.get('cash_weight')]
    if (not weights or any(isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0 or weight > 1 for weight in weights)
            or abs(sum(weights) - 1) > 1e-6):
        problems.append('invalid_target_weights')
    if rec.get('solution_status') not in ('feasible', 'optimal') or (rec.get('constraint_outcome') or {}).get('violations') or (rec.get('constraint_outcome') or {}).get('action') not in ('accepted', 'projected'):
        problems.append('infeasible_recommendation')
    if reference and any(rec.get(key) != reference.get(key) for key in ('input_snapshot_id', 'as_of', 'snapshot_checksum', 'portfolio_state')):
        problems.append('cross_method_inputs_differ')
    if reference:
        current_news = (rec.get('diagnostics') or {}).get('news_evidence') or {}
        reference_news = (reference.get('diagnostics') or {}).get('news_evidence') or {}
        current_values = {key: current_news.get(key) for key in ('consumed', 'features', 'provenance')}
        reference_values = {key: reference_news.get(key) for key in ('consumed', 'features', 'provenance')}
        if current_values != reference_values:
            problems.append('cross_method_news_inputs_differ')
    if expected and {row.get('instrument_id') for row in rec.get('target_weights', [])} != expected:
        problems.append('requested_universe_not_served')
    if any(row.get('reference_price', 0) <= 0 or row.get('price_as_of') != rec.get('as_of') for row in rec.get('decisions', [])):
        problems.append('reference_prices_inconsistent')
    return problems


def previous_decision(rows: list[dict], method: str, rec: dict) -> dict | None:
    # A same-day retry is not yesterday. Compare exact universe/portfolio/method only.
    for row in rows:
        old = row.get('recommendation') or {}
        strategy = row.get('algorithm') or row.get('strategy') or old.get('strategy')
        day = row.get('reference_date') or old.get('as_of')
        if strategy == method and day and day < rec['as_of']:
            return row
    return None


def generate(reader, config: dict, now: datetime) -> dict:
    pid = config['portfolio_id']
    history = reader.call('history', 'list_portfolio_decisions', {'portfolio_id': pid, 'limit': 100})
    rows = collection(history, 'decisions')
    report = {'generated_at': now.isoformat(), 'local_date': now.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
              'portfolio_id': pid, 'mode': 'advisory_saved_paper', 'holdings_changed': False,
              'expected_instruments': config.get('expected_instruments', []), 'methods': [],
              'method_selection': 'No statistically validated winner is inferred from one allocation. PPO optimizes its frozen sequential training objective; minimum variance minimizes modeled risk; mean variance balances modeled return and risk; CVaR penalizes modeled tail loss. Compare out-of-sample net-of-cost evidence, constraints and data readiness before choosing.',
              'history_status': history['status'], 'classical_settings': config.get('classical_settings', {}),
              'decision_cutoff_comparison': 'Each producer reads approved latest data at its current decision cutoff; snapshot identity, completed date, returned holdings and actual news features/provenance are checked after each call. Calls are sequential rather than atomic; a change invalidates controlled comparison.',
              'constraint_comparison': 'Sequential default reads must return identical market and holdings evidence to qualify as aligned. Classical settings are pinned across optimizer families. PPO constraints/horizon are frozen in its trained artifact and may differ; inspect producer diagnostics before treating the methods as an identical feasible-set experiment.'}
    reference = None
    for method in config.get('methods', METHODS):
        if method not in METHODS:
            raise ValueError('unsupported report method')
        arguments = {'portfolio_id': pid}
        # Invocation schemas couple snapshot_id and as_of. Supplying both would
        # reinterpret current morning news at a historical close, so use approved
        # defaults and validate all returned snapshot/state/feature identities.
        tool = 'recommend_portfolio' if method == 'ppo' else 'recommend_classical_portfolio'
        if method != 'ppo':
            arguments['algorithm'] = method
            arguments['settings'] = dict(config.get('classical_settings', {}))
        result = reader.call(method + '-recommendation', tool, arguments)
        item = {'method': method, 'status': result['status'], 'recommendation_receipt': result['receipt'], 'explainability': {}}
        report['methods'].append(item)
        if result['status'] != 'available':
            item['error'] = result.get('error')
            for level in ('1_recommendation', '2_plan_change', '3_realization_and_context'):
                item['explainability'][level] = {'status': 'not_available', 'reason': 'recommendation_unavailable'}
            continue
        record = recommendation_record(result)
        rec = record.get('recommendation') or {}
        checks = check_recommendation(record, method, pid, reference, set(config.get('expected_instruments', [])))
        item['comparison_issues'] = [issue for issue in checks if issue.startswith('cross_method_')]
        issues = [issue for issue in checks if not issue.startswith('cross_method_')]
        item.update(recommendation=rec, decision_id=record.get('decision_id'), readiness_issues=issues,
                    narrative=result.get('answer', {}).get('narrative', ''))
        if any(issue != 'requested_universe_not_served' for issue in issues):
            item['status'] = 'not_ready'
            for level in ('1_recommendation', '2_plan_change', '3_realization_and_context'):
                item['explainability'][level] = {'status': 'not_available', 'reason': 'recommendation_evidence_invalid'}
            continue
        if reference is None:
            reference = rec
        did = record['decision_id']
        news = (rec.get('diagnostics') or {}).get('news_evidence') or {}
        item['model_assumptions'] = {'settings': rec.get('settings') or (rec.get('diagnostics') or {}).get('settings'), 'constraint_outcome': rec.get('constraint_outcome'), 'diagnostics': {key: value for key, value in (rec.get('diagnostics') or {}).items() if key in ('action_smoothing', 'environment', 'constraints', 'action_transform', 'horizon_sessions', 'feature_window_sessions', 'input_features')}, 'target_cash_weight': rec.get('cash_weight'), 'estimated_turnover': rec.get('estimated_turnover')}
        item['news_evidence'] = news
        item['news_status'] = 'consumed_under_declared_cutoff' if news.get('consumed') else 'not_consumed'
        if config.get('require_news_features', True) and not news.get('consumed'):
            issues.append('approved_news_features_not_consumed')
        item['status'] = 'not_ready' if issues else 'available'
        # These are the existing deployed explanation levels, not invented numerical tiers.
        item['explainability']['1_recommendation'] = reader.call(method + '-why', 'explain_portfolio_decision', {'portfolio_id': pid, 'decision_id': did})
        old = previous_decision(rows, method, rec)
        item['explainability']['2_plan_change'] = (reader.call(method + '-change', 'compare_portfolio_decisions', {
            'portfolio_id': pid, 'previous_decision_id': old['decision_id'], 'current_decision_id': did}) if old else
            {'status': 'not_available', 'reason': 'no_prior_compatible_market_date', 'history_status': history['status']})
        evaluated = old['decision_id'] if old else did
        outcome = reader.call(method + '-outcome', 'evaluate_portfolio_decision', {'portfolio_id': pid, 'decision_id': evaluated})
        outcome['evaluated_decision_id'] = evaluated
        analyses = producer_documents(outcome)
        analysis_id = next((row['analysis_id'] for row in analyses if row.get('analysis_id')), None)
        if analysis_id:
            outcome['dated_context'] = reader.call(method + '-context', 'research_market_events', {'analysis_id': analysis_id})
        else:
            outcome['dated_context'] = {'status': 'not_available', 'reason': 'outcome_analysis_unavailable'}
        item['explainability']['3_realization_and_context'] = outcome
    report['reference_market_date'] = reference.get('as_of') if reference else None
    report['input_snapshot_id'] = reference.get('input_snapshot_id') if reference else None
    local = now.astimezone(ZoneInfo('America/New_York')).date()
    report['session_label'] = ('market_closed_latest_completed_session' if (config.get('_calendar_context') or {}).get('is_trading_session') is False or local.weekday() >= 5 else 'latest_approved_completed_session_not_live_intraday')
    report['comparison'] = compare_methods(report['methods'])
    comparable = [item for item in report['methods'] if item.get('decision_id') and
                  not any(issue not in ('requested_universe_not_served', 'approved_news_features_not_consumed') for issue in item.get('readiness_issues', []))]
    cross = []
    for item in comparable[1:]:
        first = comparable[0]
        cross.append({'previous_method': first['method'], 'current_method': item['method'],
                      'controlled_inputs_aligned': not bool(first.get('comparison_issues') or item.get('comparison_issues')),
                      'alignment_issues': item.get('comparison_issues', []),
                      **reader.call(first['method'] + '-versus-' + item['method'], 'compare_portfolio_decisions', {
                          'portfolio_id': pid, 'previous_decision_id': first['decision_id'], 'current_decision_id': item['decision_id']})})
    report['cross_method_explanations'] = cross
    report['pipeline_readiness'] = pipeline_readiness(config, now, report['reference_market_date'])
    validation_issues = [] if history['status'] == 'available' else ['history_tool_unavailable']
    for item in report['methods']:
        if not item.get('decision_id'):
            continue
        for level in ('1_recommendation', '3_realization_and_context'):
            if item['explainability'][level]['status'] != 'available':
                validation_issues.append(item['method'] + ':' + level + '_tool_unavailable')
    if any(item['status'] != 'available' for item in cross):
        validation_issues.append('cross_method_explanation_tool_unavailable')
    report['validation_issues'] = validation_issues
    report['status'] = 'complete' if (report['methods'] and all(item['status'] == 'available' for item in report['methods'])
        and not report['pipeline_readiness']['issues'] and not validation_issues) else 'partial_readiness'
    return report


def compare_methods(methods: list[dict]) -> dict:
    usable = [item for item in methods if item.get('recommendation') and not any(issue not in ('requested_universe_not_served', 'approved_news_features_not_consumed') for issue in item.get('readiness_issues', []))]
    if len(usable) < 2:
        return {'status': 'not_available', 'reason': 'fewer_than_two_comparable_methods'}
    base = usable[0]['recommendation']
    same = all(all(item['recommendation'].get(key) == base.get(key) for key in ('input_snapshot_id', 'as_of', 'snapshot_checksum', 'portfolio_state')) for item in usable)
    if not same or any(item.get('comparison_issues') for item in usable):
        return {'status': 'not_available', 'reason': 'inputs_differ', 'method_alignment_issues': {item['method']: item.get('comparison_issues', []) for item in usable}}
    targets = {item['method']: {row['instrument_id']: row['weight'] for row in item['recommendation']['target_weights']} | {'CASH': item['recommendation']['cash_weight']} for item in usable}
    return {'status': 'available', 'same_market_and_holdings': True, 'target_weights': targets,
            'recommendations_differ': any(target != next(iter(targets.values())) for target in targets.values()),
            'winner': 'not_established'}


def markdown(report: dict) -> str:
    lines = ['# Daily financial recommendation', '', f"Generated: {report['generated_at']}", '',
             f"Status: {report['status']}. Market evidence: {report.get('reference_market_date') or 'unavailable'}. {report['session_label']}.", '',
             'These are saved paper holdings and indicative proposed trades. No holdings were changed.', '', report['method_selection'], '', report.get('constraint_comparison', '')]
    for item in report['methods']:
        lines.extend(['', '## ' + item['method'], '', 'Readiness: ' + item['status'] + '.'])
        if item.get('model_assumptions'):
            lines.extend(['', 'Model assumptions and constraints: ' + json.dumps(item['model_assumptions'], default=str) + '.', '',
                          'PPO action smoothing and cash/turnover constraints can retain cash while an optimizer fixes cash at zero; these assumptions must be compared before interpreting different allocations. Unreported artifact settings remain unknown.'])
        if item.get('comparison_issues'):
            lines.extend(['', 'Method comparison limitation: ' + ', '.join(item['comparison_issues']) + '. These recommendations remain independently frozen evidence.'])
        if item.get('readiness_issues'):
            lines.append('Issues: ' + ', '.join(item['readiness_issues']) + '.')
        if item.get('narrative'):
            lines.extend(['', item['narrative'], '', 'News: ' + item.get('news_status', 'not_verified') + '.'])
        else:
            lines.extend(['', json.dumps(item.get('error'), default=str)])
        for level, detail in item['explainability'].items():
            lines.extend(['', '### ' + level.replace('_', ' '), '', 'Evidence status: ' + detail['status'] + '.'])
            if detail.get('answer', {}).get('narrative'):
                lines.extend(['', detail['answer']['narrative']])
            elif detail.get('reason'):
                lines.append(detail['reason'])
            elif detail.get('error'):
                lines.append(json.dumps(detail['error'], default=str))
            context = detail.get('dated_context', {})
            if context:
                lines.extend(['', 'Dated market context: ' + context.get('status', 'not_available') + '.', context.get('answer', {}).get('narrative', context.get('reason', ''))])
    lines.extend(['', '## Controlled method comparison', '', json.dumps(report['comparison'], indent=2), ''])
    for item in report.get('cross_method_explanations', []):
        lines.extend(['', '### ' + item['previous_method'] + ' versus ' + item['current_method'], '',
                      'Controlled inputs aligned: ' + str(item.get('controlled_inputs_aligned')) + '. ' + ', '.join(item.get('alignment_issues', [])), '',
                      item.get('answer', {}).get('narrative') or json.dumps(item.get('error'), default=str)])
    lines.extend(['', 'Pipeline readiness: ' + json.dumps(report.get('pipeline_readiness'), default=str), '',
                  report.get('constraint_comparison', ''), '', 'Explanation validation issues: ' + ', '.join(report.get('validation_issues', [])), ''])
    return '\n'.join(lines)


def calendar_evidence(now: datetime) -> dict:
    from finplan_agent.core.market_calendar import evidence
    return evidence(now)


def pipeline_readiness(config: dict, now: datetime, market_date: str | None) -> dict:
    calendar = config.get('_calendar_context') or {}
    issues = []
    if not calendar:
        issues.append('exchange_calendar_not_verified')
    elif market_date != calendar.get('expected_completed_session'):
        issues.append('latest_expected_completed_prices_not_served')
    day = now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    directory = Path(config.get('news_status_dir', ROOT.parent / '.worktmp/news-backfill/daily-status')).expanduser()
    candidates = sorted(directory.glob('*/*.json'))
    marker = None
    for candidate in reversed(candidates):
        try:
            document = json.loads(candidate.read_text())
            if document.get('decision_session', '') <= day:
                marker = {'path': str(candidate), **document}
                break
        except (OSError, ValueError):
            continue
    if calendar.get('is_trading_session'):
        if not marker or marker.get('decision_session') != day:
            issues.append('today_news_collector_marker_missing')
        elif not marker.get('all_profiles_published'):
            issues.append('today_news_profiles_not_all_published')
    return {'exchange_calendar': calendar, 'served_completed_session': market_date, 'news_collection': marker,
            'new_collection_expected': calendar.get('is_trading_session'), 'issues': issues,
            'meaning': 'Collection publication and training eligibility differ from each model actual feature consumption.'}


def main() -> int:
    import boto3
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.config.stat().st_mode & 0o077:
        raise ValueError('report configuration must be private (chmod 600)')
    config = json.loads(args.config.read_text())
    if config.get('environment') != 'beta':
        raise ValueError('this operator workflow is beta only')
    now = datetime.now(UTC)
    try:
        config['_calendar_context'] = calendar_evidence(now)
    except (OSError, ValueError, subprocess.SubprocessError):
        config['_calendar_context'] = {}
    day = now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    output = Path(config['output_dir']).expanduser() / day
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (output / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempt = output / 'attempts' / uuid.uuid4().hex
        try:
            reader = HostedReader(boto3.Session(region_name=config.get('region', 'us-east-2')), config, attempt)
        except Exception as error:
            # Preserve daily readiness even when authentication/SSM is unavailable.
            # Exception text can contain request details; retain only the class and HTTP status.
            detail = {'code': 'HOSTED_INITIALIZATION_UNAVAILABLE', 'error_type': type(error).__name__}
            if isinstance(error, urllib.error.HTTPError):
                detail['http_status'] = error.code
            reader = UnavailableReader(detail, attempt)
        report = generate(reader, config, now)
        report['receipts_directory'] = str(attempt)
        private_json(output / 'report.json', sanitize(report))
        (output / 'report.md').write_text(markdown(report))
        print(json.dumps({'status': report['status'], 'report': str(output / 'report.md'), 'methods': {row['method']: row['status'] for row in report['methods']}}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
