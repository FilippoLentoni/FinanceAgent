"""Schema-discovered Coinbase MCP access, never a research-wallet credential adapter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator
from ..tools.mcp_client import GatewayMcpClient, HttpResponse
from ..core.errors import AgentError

ENDPOINT = 'https://agents.coinbase.com/mcp'
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bounded_transport(url, headers, body, timeout):
    if url != ENDPOINT:
        raise ValueError('unexpected brokerage endpoint')
    request = urllib.request.Request(url, data=body, headers=dict(headers), method='POST')
    try:
        response = urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AgentError.dependency('Coinbase brokerage transport is unavailable') from None
    with response:
        data = response.read(262145)
        if len(data) > 262144:
            raise AgentError.dependency('Coinbase brokerage response exceeds byte limit')
        return HttpResponse(response.status, {k.lower():v for k,v in response.headers.items()},data)


READS = frozenset('coinbase_'+s for s in ('portfolios_list','portfolios_get','balance','products_list','products_get','orders_get','orders_list','orders_fills'))
WRITES = frozenset({'coinbase_orders_create','coinbase_orders_cancel'})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ExecutionAuthorization:
    """Operator-created per-plan authorization; never construct from model output."""
    account_id: str
    draft_checksum: str
    expires_at: str
    max_notional_usd: str
    approval_id: str
    cancel_order_ids: tuple[str, ...] = ()


class CoinbaseBrokerage:
    def __init__(self, token_provider: Callable[[], str], *, transport=None, execution_enabled=False, journal: Path | None = None):
        self.client = GatewayMcpClient(ENDPOINT, token_provider, transport=transport or bounded_transport, timeout_seconds=20)
        self.execution_enabled = execution_enabled
        self.journal = journal
        self._schemas = None

    def capabilities(self):
        specs = self.client.list_tools()
        self._schemas = {s.name: s.input_schema for s in specs if s.name in READS | WRITES}
        return {'status':'authenticated', 'read_tools':sorted(set(self._schemas) & READS),
                'write_tools_advertised':sorted(set(self._schemas) & WRITES),
                'live_execution_enabled':self.execution_enabled,
                'etf_eligibility_verified':False,
                'portfolio_isolation':'ETF isolation is not assumed; isolated agent portfolios are documented for spot crypto only.'}

    def _validate(self, name, arguments):
        if self._schemas is None:
            self.capabilities()
        if name not in self._schemas:
            raise ValueError('exact requested Coinbase capability is unavailable')
        Draft202012Validator(self._schemas[name]).validate(arguments)

    def read(self, name, arguments):
        if name not in READS:
            raise PermissionError('only exact independently authenticated read capabilities are permitted')
        self._validate(name, arguments)
        outcome = self.client.call_tool(name, arguments)
        if not outcome.ok:
            raise RuntimeError('Coinbase read failed: '+str((outcome.error or {}).get('code','DEPENDENCY_UNAVAILABLE')))
        return outcome.result

    def submit(self, draft, payload, authorization: ExecutionAuthorization, *, now):
        """Future activation boundary. No automatic retries after an ambiguous result."""
        from .planning import number
        if not self.execution_enabled:
            raise PermissionError('live execution is disabled')
        if not self.journal or not authorization.approval_id:
            raise PermissionError('durable journal and explicit operator approval are required')
        if utc(authorization.expires_at) <= utc(now):
            raise PermissionError('execution authorization expired')
        bound = {k:v for k,v in draft.items() if k != 'checksum'}
        if checksum(bound) != draft.get('checksum') or authorization.draft_checksum != draft.get('checksum'):
            raise PermissionError('draft checksum mismatch')
        if authorization.account_id != draft['account_id']:
            raise PermissionError('account authorization mismatch')
        if number(draft['notional_usd']) > number(authorization.max_notional_usd):
            raise PermissionError('execution exceeds authorized notional')
        if utc(now) > utc(draft['expires_at']):
            raise PermissionError('draft expired; re-read state and prepare again')
        if payload.get('product_id') != draft['product_id'] or payload.get('side') != draft['side']:
            raise PermissionError('order payload differs from approved draft')
        # Restrict this initial adapter to the documented share-sized limit syntax to bound execution price.
        expected = {'product_id':draft['product_id'], 'side':draft['side'], 'type':'limit',
                    'base_size':draft['quantity'], 'limit_price':draft['reference_price'],
                    'client_order_id':draft['checksum'][:32]}
        if payload != expected:
            raise PermissionError('payload must exactly match the approved share-sized limit draft')
        self._validate('coinbase_orders_create', payload)  # Unsupported account schemas fail closed.
        key = checksum({'approval':authorization.approval_id, 'draft':draft['checksum']})
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.journal) as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, payload TEXT, state TEXT, response TEXT)')
            prior = db.execute('SELECT state,response FROM submissions WHERE id=?',(key,)).fetchone()
            if prior:
                return {'state':prior[0], 'response':json.loads(prior[1]) if prior[1] else None, 'replayed':True}
            db.execute('INSERT INTO submissions VALUES (?,?,?,NULL)',(key, canonical(payload), 'unknown'))
        self.journal.chmod(0o600)
        outcome = self.client.call_tool('coinbase_orders_create',payload)
        # Even errors can represent a committed order; reconcile by client_order_id.
        if not outcome.ok:
            return {'state':'unknown','response':None,'replayed':False,'requires_reconciliation':True}
        with sqlite3.connect(self.journal) as db:
            db.execute('UPDATE submissions SET state=?, response=? WHERE id=?',('submitted',canonical(outcome.result),key))
        return {'state':'submitted','response':outcome.result,'replayed':False}

    def cancel(self, order_id, authorization: ExecutionAuthorization, *, now):
        if not self.execution_enabled or utc(authorization.expires_at) <= utc(now) or not authorization.approval_id:
            raise PermissionError('explicit active execution authorization is required')
        if order_id not in authorization.cancel_order_ids:
            raise PermissionError('order is outside the approved cancellation scope')
        args = {'order_ids':[order_id]}
        self._validate('coinbase_orders_cancel',args)
        return self.client.call_tool('coinbase_orders_cancel',args)


def readiness(client=None):
    if client is None:
        return {'status':'setup_required','authenticated':False,'live_execution_enabled':False,
                'etf_eligibility_verified':False,
                'message':'Coinbase ETF brokerage is not connected. The AgentCore Payments research wallet cannot read brokerage holdings or submit ETF orders. A separately approved Coinbase client and brokerage consent are required; no holdings or ETF eligibility have been verified.'}
    result = client.capabilities()
    return {**result, 'authenticated':True,
            'message':'Brokerage authentication succeeded; eligible ETF products and account scopes must still be verified. This is capability discovery, not an order or execution quote.'}
