"""Single-host operator procurement; never imported by the hosted planning graph."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .execution import PrivateLedger, digest

ENDPOINT = 'https://blockrun.ai/api/v1/exa/search'
RECIPIENT = '0xe9030014f5dae217d0a152f02a043567b16c1abf'
ASSET = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
CAP = 10_000_000
PER_CALL = 30_000
CONFIG = '0' * 32
MAX_BYTES = 1_048_576
PROFILES = {
    'BTC': 'BTC cryptocurrency market news',
    'ETH': 'ETH cryptocurrency market news',
    'ETF_MARKET': 'Latest US ETF market news S&P 500 bond yields Federal Reserve inflation international equities',
}


def query_body(profile):
    if profile not in PROFILES:
        raise ValueError('unsupported research profile')
    return {'query': PROFILES[profile], 'category': 'news', 'numResults': 5}



class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirects are not authorized for paid requests')


def fetch(body, headers=None):
    request = Request(ENDPOINT, data=json.dumps(body, sort_keys=True, separators=(',', ':')).encode(),
                      headers={'Content-Type': 'application/json', **(headers or {})}, method='POST')
    try:
        response = build_opener(NoRedirect()).open(request, timeout=30)
    except HTTPError as exc:
        response = exc
    with response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('provider response exceeds the delivery bound')
        return response.status, dict(response.headers), data


def challenge_from(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    encoded = headers.get('payment-required') or headers.get('x-payment-required')
    if not encoded:
        match = re.search(r'X402 requirements="([A-Za-z0-9+/=_-]+)"', headers.get('www-authenticate', ''))
        if match:
            encoded = match[1]
    if not encoded or len(encoded) > 65536:
        raise ValueError('missing or oversized payment challenge')
    return json.loads(base64.b64decode(encoded, validate=True))


def validate(challenge):
    if challenge.get('x402Version') != 2 or challenge.get('resource', {}).get('url') != ENDPOINT:
        raise ValueError('unapproved payment resource/version')
    options = challenge.get('accepts')
    if not isinstance(options, list) or len(options) != 1:
        raise ValueError('ambiguous payment options')
    p = options[0]
    if (p.get('network') != 'eip155:8453' or p.get('scheme') != 'exact'
            or str(p.get('asset', '')).lower() != ASSET or str(p.get('payTo', '')).lower() != RECIPIENT):
        raise ValueError('unapproved chain, token, scheme or recipient')
    amount = p.get('amount')
    if not isinstance(amount, str) or not re.fullmatch(r'[1-9][0-9]{0,5}', amount) or int(amount) > PER_CALL:
        raise ValueError('quote exceeds USD 0.03 per request')
    timeout = p.get('maxTimeoutSeconds')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('invalid proof expiry')
    if (p.get('extra') or {}).get('assetTransferMethod', 'eip3009') != 'eip3009':
        raise ValueError('allowance mechanisms are not authorized')
    return int(amount)


class NewsTrial:
    def __init__(self, directory):
        self.ledger = PrivateLedger(directory)

    def prepare(self):
        with self.ledger.lock():
            if self.ledger.path(CONFIG).exists():
                return self.ledger.read(CONFIG)
            config = {'cap_atomic': CAP, 'status': 'prepared', 'started_at': None,
                      'endpoint': ENDPOINT, 'recipient': RECIPIENT,
                      'research_use': 'quarantined; rights and quality unverified'}
            self.ledger.write(CONFIG, config)
            return config

    def reserve(self, asset, challenge, body, *, now):
        """Caller holds the lock across funding, reservation, signing and delivery."""
        if asset not in PROFILES:
            raise ValueError('unsupported research asset')
        config = self.ledger.read(CONFIG)
        if config.get('cap_atomic') != CAP or config.get('endpoint') != ENDPOINT or config.get('recipient') != RECIPIENT:
            raise ValueError('trial configuration changed')
        if not config['started_at']:
            config.update(started_at=now.isoformat(), expires_at=(now + timedelta(days=7)).isoformat(), status='active')
            self.ledger.write(CONFIG, config)
        if now < datetime.fromisoformat(config['started_at']) or now >= datetime.fromisoformat(config['expires_at']):
            raise ValueError('trial is outside its authorized seven-day window')
        slot = now.strftime('%Y%m%d%H') + '-' + asset
        identifier = hashlib.sha256(slot.encode()).hexdigest()[:32]
        if self.ledger.path(identifier).exists():
            raise ValueError('hourly slot already reserved; reconcile without another purchase')
        amount = validate(challenge)
        reserved = sum(int(json.loads(p.read_text()).get('reserved_atomic', 0)) for p in self.ledger.directory.glob('*.json'))
        if reserved + amount > CAP:
            raise ValueError('USD 10 cumulative trial cap exhausted')
        record = {'slot': slot, 'reserved_atomic': amount, 'request': body, 'request_checksum': digest(body),
                  'challenge': challenge, 'challenge_checksum': digest(challenge), 'status': 'reserved',
                  'client_token': str(uuid.UUID(hex=identifier)), 'reserved_at': now.isoformat(), 'profile': asset}
        self.ledger.write(identifier, record)
        return identifier, record

    def collect(self, asset, *, management, execution, state, project_preflight, now=None, transport=fetch, resume_id=None):
        now = now or datetime.now(UTC)
        body = query_body(asset)
        with self.ledger.lock():
            # Fail before activating, reserving or creating any paid session.
            project_preflight()
            common = {'paymentManagerArn': state['manager_arn'], 'userId': state['user_id']}
            instrument = management.get_payment_instrument(**common, paymentInstrumentId=state['instrument_id'])['paymentInstrument']
            if instrument.get('status') != 'ACTIVE' or any(instrument.get(k) != v for k,v in common.items()):
                raise ValueError('wallet is not active or ownership changed')
            balance = management.get_payment_instrument_balance(**common, paymentInstrumentId=state['instrument_id'],
                paymentConnectorId=state['connector_id'], chain='BASE', token='USDC')
            # SDK response is checked before any session/signing; unknown layouts fail closed.
            token = balance['tokenBalance']
            if (balance.get('paymentInstrumentId') != state['instrument_id'] or token.get('chain') != 'BASE'
                    or token.get('token') != 'USDC' or token.get('decimals') != 6):
                raise ValueError('unexpected wallet balance identity or denomination')
            amount = Decimal(str(token['amount'])) / 1_000_000
            if not amount.is_finite() or amount < Decimal('0.03'):
                raise ValueError('funded Base USDC wallet required')
            if resume_id is None:
                status, headers, _ = transport(body)
                if status != 402:
                    raise ValueError('expected an explicit unpaid payment quote')
                challenge = challenge_from(headers)
                identifier, record = self.reserve(asset, challenge, body, now=now)
            else:
                identifier = resume_id
                record = self.ledger.read(identifier)
                config = self.ledger.read(CONFIG)
                if not datetime.fromisoformat(config['started_at']) <= now < datetime.fromisoformat(config['expires_at']):
                    raise ValueError('trial is outside its authorized seven-day window')
                challenge = record['challenge']
                if (record['status'] not in ('reserved', 'signing_unknown')
                        or record['request'] != body or record['request_checksum'] != digest(body)
                        or record['challenge_checksum'] != digest(challenge)
                        or validate(challenge) != record['reserved_atomic']):
                    raise ValueError('receipt cannot be resumed with changed inputs or after delivery')
                if now >= datetime.fromisoformat(record['reserved_at']) + timedelta(minutes=15):
                    raise ValueError('original session window expired; investigate without a replacement purchase')
            try:
                session = management.create_payment_session(**common, limits={'maxSpendAmount': {'value': '0.03', 'currency': 'USD'}},
                    expiryTimeInMinutes=15, clientToken=record['client_token'])['paymentSession']
                record.update(session_id=session['paymentSessionId'], status='signing_unknown')
                self.ledger.write(identifier, record)
                signed = execution.process_payment(**common, paymentSessionId=record['session_id'], paymentInstrumentId=state['instrument_id'],
                    paymentType='CRYPTO_X402', paymentInput={'cryptoX402': {'version': '2', 'payload': challenge['accepts'][0]}},
                    clientToken=record['client_token'])
                record.update(private_payment_response=signed, status='delivery_unknown')
                self.ledger.write(identifier, record)
                if signed.get('status') != 'PROOF_GENERATED':
                    raise ValueError('payment proof was not generated')
                proof = signed['paymentOutput']['cryptoX402']['payload']
                # v2 payment header contains the complete PaymentPayload, including selected requirements.
                if 'x402Version' not in proof:
                    proof = {'x402Version': 2, 'resource': challenge['resource'], 'accepted': challenge['accepts'][0], 'payload': proof}
                encoded = base64.b64encode(json.dumps(proof, separators=(',', ':')).encode()).decode()
                code, response_headers, data = transport(body, {'PAYMENT-SIGNATURE': encoded, 'X-PAYMENT': encoded})
                record.update(http_status=code, retrieved_at=datetime.now(UTC).isoformat(), response_checksum='sha256:' + hashlib.sha256(data).hexdigest())
                # Archive the exact received bytes privately, including failures, before parsing.
                record['response_base64'] = base64.b64encode(data).decode()
                record['settlement_headers'] = {k:v for k,v in response_headers.items() if k.lower() in ('payment-response','x-payment-response','x-payment-receipt')}
                record['status'] = 'received_unvalidated' if 200 <= code < 300 else 'delivery_failed'
                self.ledger.write(identifier, record)
                if record['status'] != 'received_unvalidated':
                    raise ValueError('paid delivery failed; reservation retained')
                result = json.loads(data)
                rows = result.get('results') or result.get('body', {}).get('results')
                if not isinstance(rows, list) or not rows:
                    raise ValueError('response contains no usable search results; evidence quarantined')
                return {'receipt_id': identifier, 'status': record['status'], 'results': len(rows),
                        'reserved_usd': str(Decimal(record['reserved_atomic'])/1_000_000), 'research_use': 'quarantined'}
            except Exception:
                # Keep every reservation: unknown outcomes must never buy a replacement automatically.
                raise
