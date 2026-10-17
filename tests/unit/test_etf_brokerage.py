import json
from dataclasses import replace
import pytest

from finplan_agent.brokerage.coinbase import CoinbaseBrokerage, ExecutionAuthorization, readiness
from finplan_agent.brokerage.planning import prepare_order, reconcile
from finplan_agent.tools.mcp_client import HttpResponse

NOW='2026-10-15T02:00:00Z'
PRODUCT={'account_id':'acct1','asset_type':'ETF','eligible':True,'product_id':'SPY-USD','eligibility_evidence_id':'proof1'}


def draft(**overrides):
    args=dict(account_id='acct1',product=PRODUCT,side='BUY',quantity='0.1',reference_price='100',reference_at=NOW,now=NOW,available_cash='100',held_quantity='1',max_notional_usd='20')
    args.update(overrides)
    return prepare_order(**args)


def test_draft_bounds_and_determinism():
    assert draft()==draft()
    assert draft()['notional_usd']=='10.0'
    for override in ({'quantity':'NaN'},{'quantity':True},{'reference_price':'Infinity'}, {'product':{**PRODUCT,'asset_type':'STOCK'}}, {'product':{**PRODUCT,'account_id':'other'}}, {'now':'2026-10-15T02:10:00Z'},{'available_cash':'9'}, {'side':'SELL','quantity':'2'}, {'max_notional_usd':'5'}):
        with pytest.raises(ValueError):draft(**override)


def test_reconciliation_duplicate_and_conflicting_fill():
    fill={'fill_id':'f1','product_id':'SPY-USD','side':'BUY','quantity':'0.1','price':'100','fee':'.1'}
    state=reconcile(cash='100',holdings={},fills=[fill,fill])
    assert state['cash']=='89.9'
    assert state['holdings']=={'SPY-USD':'0.1'}
    assert reconcile(**state,fills=[fill])==state
    with pytest.raises(ValueError):reconcile(**state,fills=[{**fill,'fee':'.2'}])
    with pytest.raises(ValueError):reconcile(cash='1',holdings={},fills=[fill])
    with pytest.raises(ValueError):reconcile(cash='100',holdings={},fills=[{**fill,'side':'SELL'}])


class Transport:
    def __init__(self):self.calls=[]
    def __call__(self,url,headers,body,timeout):
        request=json.loads(body);self.calls.append(request)
        method=request['method']
        if method=='notifications/initialized':return HttpResponse(202,{},b'')
        if method=='tools/list':
            result={'tools':[{'name':name,'inputSchema':{'type':'object'}} for name in ['coinbase_portfolios_list','coinbase_orders_create','coinbase_orders_cancel']]}
        elif method=='tools/call':result={'structuredContent':{'order_id':'order1'}}
        else:result={}
        return HttpResponse(200,{},json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}).encode())


def test_exact_read_capabilities_no_writes():
    transport=Transport();client=CoinbaseBrokerage(lambda:'private-token',transport=transport)
    assert client.capabilities()['read_tools']==['coinbase_portfolios_list']
    assert client.read('coinbase_portfolios_list',{})=={'order_id':'order1'}
    with pytest.raises(PermissionError):client.read('coinbase_orders_create',{})
    with pytest.raises(ValueError):client.read('coinbase_products_get',{})
    assert readiness()['status']=='setup_required'
    with pytest.raises(Exception):CoinbaseBrokerage(lambda:'',transport=transport).capabilities()


def test_explicit_authorization_and_durable_submit_replay(tmp_path):
    transport=Transport();prepared=draft()
    authorization=ExecutionAuthorization('acct1',prepared['checksum'],'2026-10-15T02:04:00Z','20','operator-approval1')
    payload={'product_id':'SPY-USD','side':'BUY','type':'limit','base_size':'0.1','limit_price':'100','client_order_id':prepared['checksum'][:32]}
    client=CoinbaseBrokerage(lambda:'token',transport=transport,journal=tmp_path/'orders.sqlite')
    with pytest.raises(PermissionError):client.submit(prepared,payload,authorization,now=NOW)
    client.execution_enabled=True
    for invalid in (replace(authorization,account_id='other'), replace(authorization,draft_checksum='wrong'),replace(authorization,max_notional_usd='1'),replace(authorization,expires_at=NOW)):
        with pytest.raises(PermissionError):client.submit(prepared,payload,invalid,now=NOW)
    with pytest.raises(PermissionError):client.submit(prepared,{**payload,'base_size':'2'},authorization,now=NOW)
    first=client.submit(prepared,payload,authorization,now=NOW)
    second=client.submit(prepared,payload,authorization,now=NOW)
    assert first['state']=='submitted' and second['replayed']
    writes=[r for r in transport.calls if r['method']=='tools/call' and r['params']['name']=='coinbase_orders_create']
    assert len(writes)==1
    with pytest.raises(PermissionError):client.cancel('order1',authorization,now=NOW)
    assert client.cancel('order1',replace(authorization,cancel_order_ids=('order1',)),now=NOW).ok


def test_unknown_submission_never_resubmits(tmp_path):
    transport=Transport();prepared=draft()
    authorization=ExecutionAuthorization('acct1',prepared['checksum'],'2026-10-15T02:04:00Z','20','approval2')
    payload={'product_id':'SPY-USD','side':'BUY','type':'limit','base_size':'0.1','limit_price':'100','client_order_id':prepared['checksum'][:32]}
    class TimeoutTransport:
        def __call__(self,url,headers,body,timeout):
            request=json.loads(body)
            if request['method']=='tools/call':raise TimeoutError('ambiguous broker outcome')
            return transport(url,headers,body,timeout)
    client=CoinbaseBrokerage(lambda:'token',transport=TimeoutTransport(),execution_enabled=True,journal=tmp_path/'orders.sqlite')
    with pytest.raises(TimeoutError):client.submit(prepared,payload,authorization,now=NOW)
    replay=client.submit(prepared,payload,authorization,now=NOW)
    assert replay=={'state':'unknown','response':None,'replayed':True}
