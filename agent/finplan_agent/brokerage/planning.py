"""ETF order preparation and actual-fill reconciliation, independent of model inference."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from .coinbase import checksum, utc


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError('boolean is not a financial amount')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid financial amount') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('amount must be finite and nonnegative')
    return result


def prepare_order(*, account_id, product, side, quantity, reference_price, reference_at, now,
                  available_cash, held_quantity, max_notional_usd, max_reference_age_seconds=300):
    if not account_id or product.get('asset_type') != 'ETF' or product.get('eligible') is not True or product.get('account_id') != account_id:
        raise ValueError('authenticated account-specific ETF eligibility is required')
    if not product.get('eligibility_evidence_id') or not product.get('product_id'):
        raise ValueError('product and retained eligibility evidence are required')
    age = (utc(now)-utc(reference_at)).total_seconds()
    if age < 0 or age > max_reference_age_seconds:
        raise ValueError('reference price is future-dated or stale')
    if side not in ('BUY','SELL'):
        raise ValueError('side must be BUY or SELL')
    qty = number(quantity,positive=True); price = number(reference_price,positive=True)
    notional = qty*price
    if notional > number(max_notional_usd,positive=True):
        raise ValueError('order exceeds notional limit')
    if side == 'BUY' and notional > number(available_cash):
        raise ValueError('insufficient cash before fees; no leverage permitted')
    if side == 'SELL' and qty > number(held_quantity):
        raise ValueError('insufficient shares; shorting is disabled')
    doc = {'account_id':account_id,'product_id':product['product_id'],'side':side,
           'quantity':str(qty),'reference_price':str(price),'reference_at':reference_at,
           'formed_at':now,'expires_at':(utc(reference_at)+timedelta(seconds=max_reference_age_seconds)).isoformat(),
           'notional_usd':str(notional),'eligibility_evidence_id':product['eligibility_evidence_id'],
           'live_execution_enabled':False,'status':'draft','quote_type':'reference_only',
           'limitations':'Not a Coinbase preview or executable quote; fees, market session and account sizing must be checked before separately authorized execution.'}
    return {**doc,'checksum':checksum(doc)}


def reconcile(*, cash, holdings, fills, seen_fills=None):
    balance = number(cash)
    positions = {k:number(v) for k,v in holdings.items()}
    seen = dict(seen_fills or {})
    for fill in fills:
        ident = fill.get('fill_id')
        if not ident:
            raise ValueError('immutable fill identifier is required')
        digest = checksum(fill)
        if ident in seen:
            if seen[ident] != digest:
                raise ValueError('conflicting duplicate fill')
            continue
        qty = number(fill['quantity'],positive=True); price = number(fill['price'],positive=True); fee = number(fill['fee'])
        product = fill['product_id']; held = positions.get(product,Decimal(0))
        if fill['side'] == 'BUY':
            balance -= qty*price+fee; held += qty
        elif fill['side'] == 'SELL':
            balance += qty*price-fee; held -= qty
        else:
            raise ValueError('invalid fill side')
        if balance < 0 or held < 0:
            raise ValueError('fill violates cash or long-only holdings boundary')
        positions[product] = held; seen[ident] = digest
    return {'cash':str(balance),'holdings':{k:str(v) for k,v in positions.items()},'seen_fills':seen}
