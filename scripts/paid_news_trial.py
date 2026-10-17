"""Prepare or run one operator-controlled paid news slot; no background activation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'agent')]
from finplan_agent.payments.news_trial import NewsTrial
from scripts.research_payments import budget_preflight

DIRECTORY = ROOT.parent / '.worktmp/paid-news-trial-v1'
STATE = ROOT.parent / '.worktmp/agentcore-payments-20261013/state.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'preflight', 'collect'])
    parser.add_argument('--asset', choices=['BTC', 'ETH', 'ETF_MARKET', 'GOOGL'])
    parser.add_argument('--resume-receipt', help='Explicitly retry signing with the original reservation and idempotency token')
    args = parser.parse_args()
    trial = NewsTrial(DIRECTORY)
    if args.action == 'prepare':
        print(json.dumps(trial.prepare())); return
    import boto3
    from botocore.config import Config
    s = boto3.Session(region_name='us-east-2')
    state = json.loads(STATE.read_text())
    def client(kind):
        c = s.client('sts').assume_role(RoleArn=state['roles'][kind+'RoleArn'], RoleSessionName='bounded-paid-news-'+kind.lower())['Credentials']
        return boto3.Session(aws_access_key_id=c['AccessKeyId'], aws_secret_access_key=c['SecretAccessKey'], aws_session_token=c['SessionToken'], region_name='us-east-2').client('bedrock-agentcore', config=Config(retries={'total_max_attempts': 1}))
    budget_preflight(s)
    management = client('Management')
    if args.action == 'preflight':
        try:
            balance = management.get_payment_instrument_balance(paymentManagerArn=state['manager_arn'], userId=state['user_id'],
                paymentInstrumentId=state['instrument_id'], paymentConnectorId=state['connector_id'], chain='BASE', token='USDC')
        except management.exceptions.ValidationException:
            print(json.dumps({'status':'funding_required','chain':'Base mainnet','token':'USDC','payments':0})); return
        token = balance['tokenBalance']
        print(json.dumps({'status':'balance_observed','chain':token['chain'],'token':token['token'],'amount_atomic':token['amount'],'decimals':token['decimals']})); return
    if not args.asset:
        parser.error('collect requires --asset')
    print(json.dumps(trial.collect(args.asset, management=management, execution=client('Execution'), state=state, project_preflight=lambda: budget_preflight(s), resume_id=args.resume_receipt)))


if __name__ == '__main__':
    main()
