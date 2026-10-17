import base64
import json
import urllib.error
from types import SimpleNamespace
import pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec,ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from finplan_agent.brokerage.rest import CoinbaseRestReader, PREFIX,request_jwt,transport,configured_reader_factory,secret_loader
from finplan_agent.tools.mcp_client import HttpResponse

PORTFOLIO='00000000-0000-4000-8000-aaaaaaaaaaab'
OTHER='00000000-0000-4000-8000-aaaaaaaaaaac'


def credential():
    key=ec.generate_private_key(ec.SECP256R1())
    return key,{'name':'organizations/test/apiKeys/test','privateKey':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()}


def decode(value):return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def test_es256_signature_exact_get_uri_and_two_minute_expiry():
    key,data=credential();token=request_jwt(data,PREFIX+'/products',now=100)
    head,claims,sig=token.split('.')
    assert json.loads(decode(head))['alg']=='ES256'
    assert json.loads(decode(claims))=={'sub':data['name'],'iss':'cdp','nbf':100,'exp':220,'uri':'GET api.coinbase.com/api/v3/brokerage/products'}
    raw=decode(sig);assert len(raw)==64
    key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32],'big'),int.from_bytes(raw[32:],'big')),(head+'.'+claims).encode(),ec.ECDSA(hashes.SHA256()))
    assert request_jwt(data,PREFIX+'/products',now=100)!=token
    for path in (PREFIX+'/orders',PREFIX+'/products/../../accounts',PREFIX+'/products?x=y','https://other/anything'):
        with pytest.raises(PermissionError):request_jwt(data,path,now=100)
    bad=ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    with pytest.raises(ValueError):request_jwt({**data,'privateKey':bad},PREFIX+'/accounts')


class Fake:
    def __init__(self):self.calls=[];self.permission={'can_view':True,'can_trade':False,'can_transfer':False,'portfolio_uuid':PORTFOLIO};self.payload={'products':[]}
    def __call__(self,url,headers,timeout):
        self.calls.append((url,headers))
        return HttpResponse(200,{},json.dumps(self.permission if url.endswith('/key_permissions') else self.payload).encode())


def test_only_view_keys_and_expected_portfolio_scope():
    _,data=credential();fake=Fake();client=CoinbaseRestReader(lambda:data,expected_portfolio_uuid=PORTFOLIO,transport_fn=fake,clock=lambda:100)
    assert client.capabilities()['status']=='authenticated'
    assert client.read('coinbase_products_list',{})=={'products':[]}
    assert fake.calls[-1][0].endswith('/products?product_type=EQUITY&limit=1')
    jwt=fake.calls[-1][1]['Authorization'].split()[1]
    assert json.loads(decode(jwt.split('.')[1]))['uri'].endswith('/products')
    for field,value in (('can_trade',True),('can_transfer',True),('can_view',False),('portfolio_uuid',OTHER)):
        original=dict(fake.permission);fake.permission[field]=value
        with pytest.raises(PermissionError):client.read('coinbase_products_list',{})
        fake.permission=original
    with pytest.raises(PermissionError):client.read('coinbase_orders_create',{})
    with pytest.raises(ValueError):client.read('coinbase_products_list',{'url':'https://evil'})
    fake.payload={'accounts':[{'retail_portfolio_id':OTHER}]}
    with pytest.raises(PermissionError):client.read('coinbase_accounts_list',{})
    unscoped=CoinbaseRestReader(lambda:data,transport_fn=fake)
    assert unscoped.capabilities()['status']=='scope_confirmation_required'
    with pytest.raises(PermissionError):unscoped.read('coinbase_accounts_list',{})


def test_response_bounds_invalid_json_and_redirects():
    _,data=credential()
    for response in (HttpResponse(302,{'location':'https://evil'},b'{}'),HttpResponse(200,{},b'x'*262145),HttpResponse(200,{},b'not json')):
        client=CoinbaseRestReader(lambda:data,expected_portfolio_uuid=PORTFOLIO,transport_fn=lambda *a:response)
        with pytest.raises(Exception):client.capabilities()
    with pytest.raises(PermissionError):transport('https://evil.example'+PREFIX+'/accounts',{},1)
    from finplan_agent.brokerage.rest import _NoRedirect
    assert _NoRedirect().redirect_request(None,None,302,'m',{},'https://evil') is None


def test_secret_loading_and_opt_in_owner_factory():
    _,data=credential();calls=[]
    sm=SimpleNamespace(get_secret_value=lambda **k:(calls.append(k) or {'SecretString':json.dumps(data)}))
    loader=secret_loader('secret-arn',secretsmanager=sm)
    assert calls==[] and loader()==data
    assert configured_reader_factory({},secretsmanager=sm) is None
    config={'FINPLAN_COINBASE_BROKERAGE_READ_ENABLED':'true','FINPLAN_COINBASE_BROKERAGE_KEY_SECRET_ARN':'secret-arn','FINPLAN_COINBASE_BROKERAGE_PORTFOLIO_UUID':PORTFOLIO,'FINPLAN_COINBASE_BROKERAGE_OWNER_ACTOR_ID':'owner'}
    factory=configured_reader_factory(config,secretsmanager=sm)
    assert isinstance(factory(SimpleNamespace(actor_id='owner')),CoinbaseRestReader)
    with pytest.raises(PermissionError):factory(SimpleNamespace(actor_id='other'))


def test_exact_hash_product_ids_and_bounded_catalog_pagination():
    _,data=credential();fake=Fake()
    ident='a'*64
    client=CoinbaseRestReader(lambda:data,expected_portfolio_uuid=PORTFOLIO,transport_fn=fake)
    fake.payload={'product_id':ident,'product_type':'EQUITY'}
    assert client.read('coinbase_products_get',{'product_id':ident})['product_id']==ident
    for invalid in ('a'*63,'../accounts','a'*64+'/orders','abc-def'):
        with pytest.raises(ValueError):client.read('coinbase_products_get',{'product_id':invalid})
    fake.payload={'products':[{'product_id':ident,'product_type':'EQUITY','equity_product_details':{'ticker':'SPY','equity_subtype':'EQUITY_PRODUCT_SUBTYPE_ETF','equity_trading_flags':{'tradable':True}}}],'pagination':{'has_next':True,'next_cursor':'cursor1'}}
    found=client.scan_etfs(tickers=['SPY'],max_pages=2)
    assert found['all_requested_found'] and found['pages']==1 and not found['order_permission_verified']
    missing=client.scan_etfs(tickers=['VOO'],max_pages=1)
    assert not missing['all_requested_found'] and not missing['catalog_exhausted']
    with pytest.raises(ValueError):client.scan_etfs(max_pages=65)
