"""Replay one completed paid ETF receipt to the authenticated beta central news API."""
import argparse
import copy
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'agent')]
from finplan_agent.payments.execution import PrivateLedger
from finplan_agent.payments.news_publication import publication, canonical, checksum
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
        if not record.get('central_import_body'):
            rights=json.loads((LEDGER/'rights-assessment.json').read_text())['rights']
            record['central_import_body']=publication(record,rights=rights)
            ledger.write(identifier,record)
        url=endpoint+'/v1/research-news'
        def post(document):
            body=canonical(document)
            headers={'Content-Type':'application/json','X-Finplan-Contract-Version':'1.8.0','X-Correlation-Id':'news_'+uuid.uuid4().hex}
            request=AWSRequest(method='POST',url=url,data=body,headers=headers)
            SigV4Auth(session.get_credentials().get_frozen_credentials(),'execute-api','us-east-2').add_auth(request)
            with build_opener(NoRedirect()).open(Request(url,data=body,headers=dict(request.headers.items()),method='POST'),timeout=60) as response:
                raw=response.read(1_048_577)
                if len(raw)>1_048_576:raise ValueError('central API response exceeds bound')
                result=json.loads(raw)
            if not result.get('manifest',{}).get('record_id'):raise ValueError('central publication returned no immutable manifest')
            return result
        if not record.get('central_import_response'):
            record['central_import_response']=post(record['central_import_body'])
            ledger.write(identifier,record)
        result=record['central_import_response']
        # Preserve the complete quarantined raw manifest. A derived valid subset
        # retains its payment transaction/checksum, not a new charge or timestamp.
        subset=curated_subset(record['central_import_body']) if not result['manifest']['eligible'] else None
        if subset:
            if not record.get('central_curated_body'):
                record['central_curated_body']=subset
                record['central_curated_source_record_id']=result['manifest']['record_id']
                ledger.write(identifier,record)
            if not record.get('central_curated_response'):
                record['central_curated_response']=post(record['central_curated_body'])
            result=record['central_curated_response']
        record['central_publication_complete']=True
        ledger.write(identifier,record)
        return result


def curated_subset(document):
    rows=[row for row in document['payload']['records'] if row['published_at'] is not None]
    if not rows or len(rows)==len(document['payload']['records']):return None
    result=copy.deepcopy(document)
    result['provider']='blockrun-exa-curated-v1-'+document['payload_checksum'].split(':')[1][:16]
    result['payload']['records']=rows
    result['payload_checksum']=checksum(result['payload'])
    result['quality_status']='validated'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--receipt',required=True);args=parser.parse_args()
    result=publish(args.receipt);manifest=result['manifest']
    print(json.dumps({'record_id':manifest['record_id'],'available_at':manifest['available_at'],
                      'eligible':manifest['eligible'],'exclusion_reasons':manifest['exclusion_reasons']}))
