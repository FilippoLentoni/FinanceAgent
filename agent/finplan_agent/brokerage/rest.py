"""Own-account, view-only Coinbase Advanced Trade REST access using ES256 CDP keys."""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from ..core.errors import AgentError
from ..tools.mcp_client import HttpResponse

HOST='api.coinbase.com'
PREFIX='/api/v3/brokerage'
SECRET_NAME='finplan/beta/financeagent/coinbase-brokerage-readonly'
_UUID=re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_PRODUCT=re.compile(r'^[A-Z0-9][A-Z0-9.-]{0,79}$')
_EXACT={PREFIX+s for s in ('/key_permissions','/accounts','/products','/orders/historical/batch','/orders/historical/fills','/portfolios')}


def allowed_path(path):
    if path in _EXACT:return True
    for stem,pattern in (('/accounts/',_UUID),('/products/',_PRODUCT),('/orders/historical/',_UUID),('/portfolios/',_UUID)):
        if path.startswith(PREFIX+stem) and pattern.fullmatch(path[len(PREFIX+stem):]):return True
    return False


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def request_jwt(credential, path, *, now=None):
    if not allowed_path(path):raise PermissionError('unapproved read path')
    name=credential.get('name','');pem=credential.get('privateKey','')
    if not re.fullmatch(r'organizations/[^/\s]+/apiKeys/[^/\s]+',name):raise ValueError('downloaded Coinbase API key name is invalid')
    try:key=serialization.load_pem_private_key(pem.encode(),password=None)
    except Exception:raise ValueError('Coinbase private key is not a valid unencrypted ECDSA key') from None
    if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):raise ValueError('ECDSA P-256/ES256 key is required')
    timestamp=int(time.time() if now is None else now)
    header={'alg':'ES256','typ':'JWT','kid':name,'nonce':secrets.token_hex()}
    payload={'sub':name,'iss':'cdp','nbf':timestamp,'exp':timestamp+120,'uri':'GET '+HOST+path}
    segments=[b64(json.dumps(x,separators=(',',':')).encode()) for x in (header,payload)]
    message='.'.join(segments).encode()
    r,s=decode_dss_signature(key.sign(message,ec.ECDSA(hashes.SHA256())))
    return '.'.join(segments+[b64(r.to_bytes(32,'big')+s.to_bytes(32,'big'))])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def transport(url,headers,timeout):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!=HOST or not allowed_path(parsed.path) or parsed.fragment:raise PermissionError('unapproved REST endpoint')
    req=urllib.request.Request(url,headers=headers,method='GET')
    try:response=urllib.request.build_opener(_NoRedirect).open(req,timeout=timeout)
    except urllib.error.HTTPError as exc:response=exc
    except (urllib.error.URLError,TimeoutError,OSError):raise AgentError.dependency('Coinbase REST transport is unavailable') from None
    with response:
        body=response.read(262145)
        if len(body)>262144:raise AgentError.dependency('Coinbase REST response exceeds byte limit')
        return HttpResponse(response.status,{k.lower():v for k,v in response.headers.items()},body)


def secret_loader(secret_id, *, secretsmanager):
    """Secrets are loaded only when explicitly configured; never included in diagnostic output."""
    def load():
        value=secretsmanager.get_secret_value(SecretId=secret_id)
        return json.loads(value['SecretString'])
    return load


class CoinbaseRestReader:
    execution_enabled=False
    def __init__(self, credentials: Callable[[],dict], *, expected_portfolio_uuid=None, transport_fn=None, clock=time.time):
        if expected_portfolio_uuid is not None and not _UUID.fullmatch(expected_portfolio_uuid):raise ValueError('explicit portfolio UUID is invalid')
        self.credentials=credentials;self.expected_portfolio_uuid=expected_portfolio_uuid
        self.transport=transport_fn or transport;self.clock=clock

    def _get(self,path,query=None):
        if not allowed_path(path):raise PermissionError('unapproved read path')
        token=request_jwt(self.credentials(),path,now=self.clock())
        url='https://'+HOST+path
        if query:url+='?'+urllib.parse.urlencode(query,doseq=True)
        response=self.transport(url,{'Authorization':'Bearer '+token,'Accept':'application/json'},20)
        if len(response.body)>262144:raise AgentError.dependency('Coinbase REST response exceeds byte limit')
        if response.status!=200:raise AgentError.dependency('Coinbase REST read failed',http_status=response.status)
        try:doc=json.loads(response.body)
        except (ValueError,UnicodeError):raise AgentError.dependency('Coinbase REST returned invalid JSON') from None
        if not isinstance(doc,dict):raise AgentError.dependency('Coinbase REST returned an invalid response object')
        return doc

    def permissions(self):
        result=self._get(PREFIX+'/key_permissions')
        if result.get('can_view') is not True or result.get('can_trade') is not False or result.get('can_transfer') is not False:
            raise PermissionError('a View-only key with Trade and Transfer disabled is required')
        if not _UUID.fullmatch(str(result.get('portfolio_uuid',''))):raise PermissionError('brokerage key has no explicit portfolio scope')
        if self.expected_portfolio_uuid and result['portfolio_uuid']!=self.expected_portfolio_uuid:raise PermissionError('brokerage key portfolio differs from configured scope')
        return result

    def capabilities(self):
        permissions=self.permissions()
        if not self.expected_portfolio_uuid:
            return {'status':'scope_confirmation_required','authenticated':False,'interface':'advanced_trade_rest',
                    'portfolio_uuid':permissions['portfolio_uuid'],'read_tools':[], 'live_execution_enabled':False,'etf_eligibility_verified':False}
        return {'status':'authenticated','authenticated':True,'interface':'advanced_trade_rest',
                'read_tools':['coinbase_portfolios_list','coinbase_portfolios_get','coinbase_balance','coinbase_products_list','coinbase_products_get','coinbase_orders_get','coinbase_orders_list','coinbase_orders_fills'],
                'live_execution_enabled':False,'etf_eligibility_verified':False,
                'portfolio_isolation':'Scope is verified against key_permissions; ETF eligibility is still account-specific.'}

    def read(self,name,arguments):
        if name not in {'coinbase_accounts_list','coinbase_balance','coinbase_products_list','coinbase_products_get','coinbase_portfolios_list','coinbase_portfolios_get','coinbase_orders_get','coinbase_orders_list','coinbase_orders_fills'}:
            raise PermissionError('unapproved REST read operation')
        self.permissions()
        if not self.expected_portfolio_uuid:raise PermissionError('operator must confirm the expected portfolio UUID before account reads')
        args=dict(arguments)
        if name=='coinbase_balance':name='coinbase_accounts_list'
        if name=='coinbase_accounts_list':path=PREFIX+'/accounts';allowed={'limit','cursor'}
        elif name=='coinbase_products_list':path=PREFIX+'/products';allowed={'limit','cursor','product_type','product_ids'};args.setdefault('product_type','EQUITY')
        elif name=='coinbase_products_get':
            ident=args.pop('product_id',None)
            if not isinstance(ident,str) or not _PRODUCT.fullmatch(ident):raise ValueError('invalid product identifier')
            path=PREFIX+'/products/'+ident;allowed=set()
        elif name=='coinbase_portfolios_list':path=PREFIX+'/portfolios';allowed=set()
        elif name=='coinbase_portfolios_get':
            ident=args.pop('portfolio_id',None)
            if ident!=self.expected_portfolio_uuid:raise PermissionError('portfolio read is outside configured scope')
            path=PREFIX+'/portfolios/'+ident;allowed=set()
        elif name=='coinbase_orders_get':
            ident=args.pop('order_id',None)
            if not isinstance(ident,str) or not _UUID.fullmatch(ident):raise ValueError('invalid order identifier')
            path=PREFIX+'/orders/historical/'+ident;allowed=set()
        elif name=='coinbase_orders_list':path=PREFIX+'/orders/historical/batch';allowed={'limit','cursor','product_ids'}
        elif name=='coinbase_orders_fills':path=PREFIX+'/orders/historical/fills';allowed={'limit','cursor','order_ids','product_ids'}
        else:raise PermissionError('unapproved REST read operation')
        if set(args)-allowed:raise ValueError('unsupported query arguments')
        if 'limit' in args and (isinstance(args['limit'],bool) or not isinstance(args['limit'],int) or not 1<=args['limit']<=100):raise ValueError('limit must be an integer between 1 and 100')
        if 'cursor' in args and (not isinstance(args['cursor'],str) or len(args['cursor'])>2048):raise ValueError('invalid pagination cursor')
        if 'product_type' in args and args['product_type']!='EQUITY':raise ValueError('ETF reader requires EQUITY product catalog')
        for field,pattern in (('product_ids',_PRODUCT),('order_ids',_UUID)):
            if field in args and (not isinstance(args[field],list) or not 1<=len(args[field])<=25 or any(not isinstance(x,str) or not pattern.fullmatch(x) for x in args[field])):raise ValueError('invalid bounded identifier list')
        result=self._get(path,args)
        # Reject cross-portfolio payloads instead of exposing or silently filtering them.
        for collection in ('accounts','orders','fills','portfolios'):
            for row in result.get(collection,[]):
                scope=row.get('retail_portfolio_id') or row.get('portfolio_uuid') or (row.get('uuid') if collection=='portfolios' else None)
                if scope and scope!=self.expected_portfolio_uuid:raise PermissionError('response contains data outside configured portfolio')
        for container in ('order','breakdown'):
            scope=result.get(container,{}).get('retail_portfolio_id') or result.get(container,{}).get('portfolio_uuid')
            if scope and scope!=self.expected_portfolio_uuid:raise PermissionError('response contains data outside configured portfolio')
        return result


def configured_reader_factory(environ, *, secretsmanager):
    """Explicit deployment hook; does not grant IAM or connect by default."""
    if environ.get('FINPLAN_COINBASE_BROKERAGE_READ_ENABLED')!='true':return None
    secret_id=environ.get('FINPLAN_COINBASE_BROKERAGE_KEY_SECRET_ARN')
    portfolio=environ.get('FINPLAN_COINBASE_BROKERAGE_PORTFOLIO_UUID')
    actor_id=environ.get('FINPLAN_COINBASE_BROKERAGE_OWNER_ACTOR_ID')
    if not secret_id or not actor_id or not portfolio or not _UUID.fullmatch(portfolio):
        raise ValueError('explicit secret, portfolio scope and owner actor are required')
    def factory(caller):
        if caller.actor_id!=actor_id:raise PermissionError('brokerage reader belongs to another caller')
        return CoinbaseRestReader(secret_loader(secret_id,secretsmanager=secretsmanager),expected_portfolio_uuid=portfolio)
    return factory
