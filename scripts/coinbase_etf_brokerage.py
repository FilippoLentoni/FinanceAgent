#!/usr/bin/env python3
"""Read-only credential probe. Never accepts a token on the command line."""
import argparse
import json
import os

from finplan_agent.brokerage.coinbase import CoinbaseBrokerage, readiness


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['status'])
    parser.parse_args()
    key_arn=os.environ.get('FINPLAN_COINBASE_BROKERAGE_KEY_SECRET_ARN')
    arn=os.environ.get('FINPLAN_COINBASE_BROKERAGE_TOKEN_SECRET_ARN')
    if not arn and not key_arn:
        print(json.dumps(readiness(),indent=2));return
    import boto3
    def token():
        secret=boto3.client('secretsmanager').get_secret_value(SecretId=arn)
        return json.loads(secret['SecretString'])['access_token']
    try:
        if key_arn:
            from finplan_agent.brokerage.rest import CoinbaseRestReader,secret_loader
            result=readiness(CoinbaseRestReader(secret_loader(key_arn,secretsmanager=boto3.client('secretsmanager')),
                       expected_portfolio_uuid=os.environ.get('FINPLAN_COINBASE_BROKERAGE_PORTFOLIO_UUID')))
        else:
            result=readiness(CoinbaseBrokerage(token))
    except Exception as exc:
        # Never print provider bodies, tokens, secret values or AWS exception details.
        result={'status':'unavailable','authenticated':False,'error_type':type(exc).__name__}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
