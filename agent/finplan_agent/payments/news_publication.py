"""Sanitized immutable search metadata, separate from private wallet/proof receipts."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def checksum(value):
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()


PROFILE_SYMBOLS={'ETF_MARKET':('SPY','VOO'),'GOOGL':('GOOGL',)}


def publication(record, *, rights, symbols=None):
    expected=PROFILE_SYMBOLS.get(record.get('profile'))
    if not expected or record.get('status') != 'received_unvalidated' or record.get('http_status') != 200:
        raise ValueError('only delivered approved financial search metadata can be published')
    if symbols is not None and tuple(symbols)!=expected:
        raise ValueError('publication instrument scope must match its fixed query profile')
    symbols=expected
    response = base64.b64decode(record['response_base64'], validate=True)
    if 'sha256:' + hashlib.sha256(response).hexdigest() != record['response_checksum']:
        raise ValueError('private delivery integrity failure')
    settlement = None
    for key, value in record.get('settlement_headers', {}).items():
        if key.lower() in ('x-payment-response', 'payment-response'):
            settlement = json.loads(base64.b64decode(value, validate=True))
    if (not settlement or settlement.get('success') is not True
            or settlement.get('network') != 'eip155:8453' or not settlement.get('transaction')):
        raise ValueError('successful Base settlement receipt required')
    if not isinstance(rights, dict) or rights.get('scope') != 'search_metadata' or rights.get('status') == 'research_and_training':
        raise ValueError('this collector cannot assert training or full-content rights')
    retrieved = datetime.fromisoformat(record['retrieved_at'].replace('Z', '+00:00'))
    if retrieved.tzinfo is None:
        raise ValueError('retrieval timezone missing')
    raw = json.loads(response)
    rows = raw.get('results') or raw.get('body', {}).get('results')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise ValueError('invalid delivered result count')
    output = []
    validated = True
    for row in rows:
        title, url, published = row.get('title'), row.get('url'), row.get('publishedDate')
        if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(url, str) or len(url) > 2048:
            raise ValueError('invalid metadata title or URL')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('unsafe metadata URL')
        stamp = None
        if isinstance(published, str):
            try:
                dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
                if dt.tzinfo and retrieved-timedelta(days=7) <= dt <= retrieved:
                    stamp = dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')
            except ValueError:
                pass
        validated = validated and stamp is not None
        output.append({'title': title, 'url': url, 'published_at': stamp, 'symbols': list(symbols)})
    payload = {'records': output}
    receipt = {'settlement_status': 'success', 'amount_usdc': record['reserved_atomic'] / 1_000_000,
               'transaction_id': settlement['transaction']}
    return {'provider': 'blockrun-exa', 'query': record['request']['query'], 'symbols': list(symbols),
            'retrieved_at': retrieved.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
            'payload_checksum': checksum(payload), 'receipt_checksum': checksum(receipt),
            'payload': payload, 'payment_receipt': receipt, 'rights': rights,
            'quality_status': 'validated' if validated else 'unverified', 'contract_version': '1.8.0'}
