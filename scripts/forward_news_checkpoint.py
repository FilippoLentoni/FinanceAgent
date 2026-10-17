"""Scheduled read-only corpus checkpoint; persist unresolved inputs, never promote."""
import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = datetime(2026, 10, 30, 20, 15, tzinfo=UTC)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-now', action='store_true', help='read the preceding fourteen days now; no training')
    args = parser.parse_args()
    now = datetime.now(UTC)
    if now < CUTOFF and not args.verify_now:
        print(json.dumps({'status': 'checkpoint_not_due', 'jobs_submitted': 0}))
        return 0
    import boto3
    session = boto3.Session(region_name='us-east-2')
    account = session.client('sts').get_caller_identity()['Account']
    endpoint = session.client('ssm').get_parameter(Name='/finplan/beta/financialplanning/api/plan-endpoint')['Parameter']['Value']
    role = f'arn:aws:iam::{account}:role/finplan-beta-financialplanning-research-news-publisher'
    credentials = session.client('sts').assume_role(RoleArn=role, RoleSessionName='forward-news-checkpoint')['Credentials']
    environment = dict(os.environ)
    environment.update(AWS_ACCESS_KEY_ID=credentials['AccessKeyId'], AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'], AWS_SESSION_TOKEN=credentials['SessionToken'], AWS_DEFAULT_REGION='us-east-2')
    finish = now if args.verify_now else CUTOFF
    directory = ROOT.parent/'.worktmp/news-backfill'/('readiness-now-'+now.strftime('%Y%m%dT%H%M%SZ') if args.verify_now else 'checkpoint-20261030')
    # Each scheduled invocation freezes locally using the existing central read route.
    # No --execute: rights and approved prices remain unresolved, not silently bypassed.
    # Keep each central response within the immutable five-symbol contract.
    universe = ('GOOGL','AMZN','NVDA','MSFT','AAPL','META','TSLA','VOO')
    reports = []
    failed = False
    for index in range(0, len(universe), 4):
        symbols = universe[index:index+4]
        output = directory / ('batch-' + str(index // 4 + 1))
        result = subprocess.run([str(ROOT.parent/'FinanceModel/.venv/bin/python'), str(ROOT.parent/'FinanceModel/scripts/ppo_forward_checkpoint.py'), '--capture-only', '--endpoint', endpoint, '--region', 'us-east-2', '--news-start', (finish-timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ'), '--checkpoint-at', finish.strftime('%Y-%m-%dT%H:%M:%SZ'), '--symbols', ','.join(symbols), '--output', str(output)], env=environment, capture_output=True, text=True, timeout=180)
        report_path = output/'report.json'
        if not report_path.exists():
            reports.append({'symbols':symbols,'status':'checkpoint_process_failed','returncode':result.returncode})
            failed = True
            continue
        report = json.loads(report_path.read_text())
        reports.append({'symbols':symbols,'status':report['status'],'blockers':report.get('blockers',[]),'delivery_count':report.get('delivery_count'),'jobs_submitted':report.get('jobs_submitted',0),'report':str(report_path)})
        failed |= 'central_corpus_read_failed' in report.get('blockers',[]) or result.returncode not in (0,2)
    summary = {'status':'checkpoint_failed' if failed else 'readiness_captured','universe':universe,'batches':reports,'jobs_submitted':0,'eight_asset_training_status':'pending_contract_and_aligned_dataset_integration'}
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return 1 if failed else 0



if __name__ == '__main__':
    raise SystemExit(main())
