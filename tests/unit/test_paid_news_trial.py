import base64
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from finplan_agent.payments.news_trial import ASSET, CAP, CONFIG, ENDPOINT, RECIPIENT, NewsTrial, validate

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def challenge(amount='11000'):
    return {'x402Version':2,'resource':{'url':ENDPOINT},'accepts':[{'scheme':'exact','network':'eip155:8453','asset':ASSET,'payTo':RECIPIENT,'amount':amount,'maxTimeoutSeconds':300}]}


def trial(tmp_path):
    t=NewsTrial(tmp_path/'ledger');t.prepare();return t


def test_cap_retains_unknown_reservations_and_rejects_duplicate_and_expired_slots(tmp_path):
    t=trial(tmp_path)
    with t.ledger.lock():
        id, r=t.reserve('BTC',challenge(),{},now=NOW)
        r.update(status='delivery_unknown',reserved_atomic=CAP-10000);t.ledger.write(id,r)
        with pytest.raises(ValueError,match='already reserved'):t.reserve('BTC',challenge(),{},now=NOW)
        with pytest.raises(ValueError,match='cap exhausted'):t.reserve('ETH',challenge(),{},now=NOW)
        with pytest.raises(ValueError,match='seven-day'):t.reserve('ETH',challenge(),{},now=NOW+timedelta(days=7))


@pytest.mark.parametrize('field,value',[('payTo','0x'+'1'*40),('network','eip155:84532'),('asset','0x'+'2'*40),('amount','31000'),('scheme','upto'),('maxTimeoutSeconds',True)])
def test_unapproved_challenges_fail(field,value):
    c=challenge();c['accepts'][0][field]=value
    with pytest.raises(ValueError):validate(c)


class Client:
    def __init__(self, fail=False, balance='10000000'):
        self.calls=[];self.fail=fail;self.balance=balance
    def get_payment_instrument(self,**kw):return {'paymentInstrument':{**kw,'status':'ACTIVE'}}
    def get_payment_instrument_balance(self,**kw):return {'paymentInstrumentId':kw['paymentInstrumentId'],'tokenBalance':{'chain':'BASE','token':'USDC','decimals':6,'amount':self.balance}}
    def create_payment_session(self,**kw):
        from botocore.session import get_session
        from botocore.validate import validate_parameters
        validate_parameters(kw,get_session().get_service_model('bedrock-agentcore').operation_model('CreatePaymentSession').input_shape)
        self.calls.append('session');return {'paymentSession':{'paymentSessionId':'payment-session-'+'s'*20}}
    def process_payment(self,**kw):
        from botocore.session import get_session
        from botocore.validate import validate_parameters
        validate_parameters(kw,get_session().get_service_model('bedrock-agentcore').operation_model('ProcessPayment').input_shape)
        self.calls.append('sign')
        if self.fail:raise TimeoutError('unknown signing result')
        return {'status':'PROOF_GENERATED','paymentOutput':{'cryptoX402':{'payload':{'signature':'synthetic-proof'}}}}


STATE={'manager_arn':'arn:aws:bedrock-agentcore:us-east-2:111111111111:payment-manager/finplanbetaresearch-abcdefghij','user_id':'test-user','instrument_id':'payment-instrument-'+'i'*20,'connector_id':'connector-test'}


def transport(body,headers=None):
    if headers is None:return 402,{'payment-required':base64.b64encode(json.dumps(challenge()).encode()).decode()},b''
    proof=json.loads(base64.b64decode(headers['PAYMENT-SIGNATURE']));assert proof['accepted']['amount']=='11000'
    return 200,{},json.dumps({'results':[{'title':'Synthetic test','publishedDate':'2026-10-15'}]}).encode()


def test_delivery_is_quarantined_and_unknown_signing_is_not_retried(tmp_path):
    t=trial(tmp_path);c=Client(fail=True)
    with pytest.raises(TimeoutError):t.collect('BTC',management=c,execution=c,state=STATE,project_preflight=lambda:None,now=NOW,transport=transport)
    with pytest.raises(ValueError,match='already reserved'):t.collect('BTC',management=c,execution=c,state=STATE,project_preflight=lambda:None,now=NOW,transport=transport)
    assert c.calls==['session','sign']
    c.fail=False;r=t.collect('ETH',management=c,execution=c,state=STATE,project_preflight=lambda:None,now=NOW,transport=transport)
    assert r['research_use']=='quarantined' and r['results']==1
    assert sum(json.loads(p.read_text()).get('reserved_atomic',0) for p in t.ledger.directory.glob('*.json'))==22000


def test_no_funding_never_activates_trial_or_creates_session(tmp_path):
    t=trial(tmp_path);c=Client(balance='0')
    with pytest.raises(ValueError,match='funded'):t.collect('BTC',management=c,execution=c,state=STATE,project_preflight=lambda:None,now=NOW,transport=transport)
    assert c.calls==[] and t.ledger.read(CONFIG)['started_at'] is None
