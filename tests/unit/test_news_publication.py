import base64
import copy
import hashlib
import json

import pytest
from finplan_agent.payments.news_publication import publication, checksum


def receipt():
    raw=json.dumps({'results':[{'title':'ETF market moves','url':'https://example.org/news','publishedDate':'2026-10-15T00:00:00Z'}]}).encode()
    return {'profile':'ETF_MARKET','status':'received_unvalidated','http_status':200,
            'response_base64':base64.b64encode(raw).decode(),'response_checksum':'sha256:'+hashlib.sha256(raw).hexdigest(),
            'request':{'query':'ETF market news'},'retrieved_at':'2026-10-15T03:00:00Z','reserved_atomic':11000,
            'settlement_headers':{'x-payment-response':base64.b64encode(json.dumps({'success':True,'network':'eip155:8453','transaction':'synthetic-transaction'}).encode()).decode()},
            'private_payment_response':{'secret-proof':'never publish'},'session_id':'private-session'}


RIGHTS={'status':'research_only','scope':'search_metadata','evidence_url':'https://example.org/terms','evidence_checksum':'sha256:'+'0'*64}


def test_publication_contains_metadata_and_settlement_but_no_wallet_or_proof():
    result=publication(receipt(),rights=RIGHTS)
    assert result['payload_checksum']==checksum(result['payload'])
    assert result['receipt_checksum']==checksum(result['payment_receipt'])
    assert result['quality_status']=='validated'
    assert 'private-session' not in json.dumps(result) and 'never publish' not in json.dumps(result)
    assert result['rights']['status']=='research_only'


def test_publication_quarantines_missing_or_future_publication_time():
    record=receipt();raw=json.dumps({'results':[{'title':'Test','url':'https://example.org/news','publishedDate':'2027-01-01T00:00:00Z'}]}).encode()
    record['response_base64']=base64.b64encode(raw).decode();record['response_checksum']='sha256:'+hashlib.sha256(raw).hexdigest()
    result=publication(record,rights=RIGHTS)
    assert result['quality_status']=='unverified' and result['payload']['records'][0]['published_at'] is None


@pytest.mark.parametrize('change',[{'response_checksum':'sha256:'+'0'*64},{'settlement_headers':{}},{'profile':'BTC'},{'http_status':402}])
def test_unverified_deliveries_cannot_be_published(change):
    record=receipt();record.update(change)
    with pytest.raises(ValueError):publication(record,rights=RIGHTS)


def test_collector_never_asserts_training_rights():
    rights={**RIGHTS,'status':'research_and_training'}
    with pytest.raises(ValueError):publication(receipt(),rights=rights)


def test_company_news_cannot_be_relabelled_as_etf_news():
    record=receipt();record['profile']='GOOGL'
    result=publication(record,rights=RIGHTS)
    assert result['symbols']==['GOOGL'] and result['payload']['records'][0]['symbols']==['GOOGL']
    with pytest.raises(ValueError):publication(record,rights=RIGHTS,symbols=('VOO',))


def test_mixed_delivery_curation_preserves_raw_and_payment_identity():
    from scripts.publish_paid_news import curated_subset
    document=publication(receipt(),rights=RIGHTS)
    document['payload']['records'].append({'title':'Unknown date','url':'https://example.org/unknown','published_at':None,'symbols':['SPY','VOO']})
    original=copy.deepcopy(document)
    derived=curated_subset(document)
    assert document==original and len(document['payload']['records'])==2
    assert len(derived['payload']['records'])==1 and derived['quality_status']=='validated'
    assert derived['retrieved_at']==document['retrieved_at']
    assert derived['payment_receipt']==document['payment_receipt'] and derived['receipt_checksum']==document['receipt_checksum']
    assert derived['payload_checksum']==checksum(derived['payload'])
