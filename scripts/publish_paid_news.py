"""Replay one completed paid ETF receipt to the authenticated beta central news API."""
import argparse
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'agent')]
from finplan_agent.payments.execution import PrivateLedger
from finplan_agent.payments.news_publication import publication, canonical
from finplan_agent.payments.news_trial import NoRedirect

LEDGER = ROOT.parent/'.worktmp/paid-news-trial-v1'


def publish(identifier):
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    s=boto3.Session(region_name='us-east-2')
    account=s.client('sts').get_caller_identity()['Account']
    role=f'arn:aws:iam::{account}:role/finplan-beta-financialplanning-research-news-publisher'
    endpoint=s.client('ssm').get_parameter(Name='/finplan/beta/financialplanning/api/plan-endpoint')['Parameter']['Value'].rstrip('/')
    credentials=s.client('sts').assume_role(RoleArn=role,RoleSessionName='etf-news-publication')['Credentials']
    session=boto3.Session(aws_access_key_id=credentials['AccessKeyId'],aws_secret_access_key=credentials['SecretAccessKey'],aws_session_token=credentials['SessionToken'],region_name='us-east-2')
    ledger=PrivateLedger(LEDGER)
    with ledger.lock():
        record=ledger.read(identifier)
        if record.get('central_import_response'):
            return record['central_import_response']
        if not record.get('central_import_body'):
            rights=json.loads((LEDGER/'rights-assessment.json').read_text())['rights']
            record['central_import_body']=publication(record,rights=rights)
            ledger.write(identifier,record)
        body=canonical(record['central_import_body'])
        url=endpoint+'/v1/research-news'
        headers={'Content-Type':'application/json','X-Finplan-Contract-Version':'1.8.0','X-Correlation-Id':'news_'+uuid.uuid4().hex}
        request=AWSRequest(method='POST',url=url,data=body,headers=headers)
        SigV4Auth(session.get_credentials().get_frozen_credentials(),'execute-api','us-east-2').add_auth(request)
        with build_opener(NoRedirect()).open(Request(url,data=body,headers=dict(request.headers.items()),method='POST'),timeout=60) as response:
            raw=response.read(1_048_577)
            if len(raw)>1_048_576:raise ValueError('central API response exceeds bound')
            result=json.loads(raw)
        if not result.get('manifest',{}).get('record_id'):raise ValueError('central publication returned no immutable manifest')
        record['central_import_response']=result
        ledger.write(identifier,record)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--receipt',required=True);args=parser.parse_args()
    result=publish(args.receipt);manifest=result['manifest']
    print(json.dumps({'record_id':manifest['record_id'],'available_at':manifest['available_at'],
                      'eligible':manifest['eligible'],'exclusion_reasons':manifest['exclusion_reasons']}))
