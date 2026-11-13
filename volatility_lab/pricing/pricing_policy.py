"""Black-76 first; matched futures, then tight ATM parity, then dividend-aware BSM.

This module selects current valuation inputs. It never rewrites broker IV history.
Spreads are percentages of each leg's midpoint; both must be strictly below 3%.
"""
from datetime import datetime, timezone
import math


def number(value):
    try:
        if value is None or isinstance(value, bool) or value == '':
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            numeric = float(value)
            return datetime.fromtimestamp(numeric / (1000 if abs(numeric) > 1e11 else 1), timezone.utc)
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def fresh(value, as_of, max_age=120):
    observed, reference = timestamp(value), timestamp(as_of)
    return observed is not None and reference is not None and -30 <= (reference-observed).total_seconds() <= max_age


def book(quote, as_of, max_age=120):
    quote = quote or {}
    stamp = quote.get('quote_timestamp') or quote.get('timestamp')
    if not fresh(stamp, as_of, max_age):
        return None
    depth = quote.get('depth') or {}
    buy, sell = (depth.get('buy') or [{}])[0], (depth.get('sell') or [{}])[0]
    bid = number(buy.get('price', quote.get('bid_price', quote.get('bid'))))
    ask = number(sell.get('price', quote.get('ask_price', quote.get('ask'))))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    for qty in [buy.get('quantity', quote.get('bid_qty')), sell.get('quantity', quote.get('ask_qty'))]:
        if qty is not None and (number(qty) is None or number(qty) <= 0):
            return None
    mid = (bid+ask)/2
    return {'mid': mid, 'spread': 100*(ask-bid)/mid, 'timestamp': timestamp(stamp)}


def recorded_inputs(payload, symbol, as_of):
    """Read only sourced inputs known at the time of an archived broker capture."""
    reference = timestamp(as_of)
    rate, div = payload.get('rate_source') or {}, payload.get('dividend_source') or {}
    r, q = number(rate.get('model_rate')), number(div.get('model_yield'))
    checked = timestamp(rate.get('checked_at'))
    expected = 'https://www.niftyindices.com/Factsheet/' + ('ind_nifty50.pdf' if symbol == 'NIFTY' else 'ind_nifty_bank.pdf')
    if not (reference and checked and 0 <= (reference-checked).total_seconds() <= 7*86400
            and rate.get('source') == 'https://www.rbi.org.in/' and rate.get('sha256')
            and r is not None and 0 <= r < .30):
        r = None
    published = timestamp(str(div.get('as_of')) + 'T00:00:00+05:30')
    div_checked = timestamp(div.get('checked_at'))
    if not (reference and published and div_checked and div_checked <= reference
            and 0 <= (reference-published).total_seconds() <= 62*86400
            and div.get('source') == expected and div.get('sha256') and q is not None and 0 <= q <= .15):
        q = None
    return r, q


def select_model(chain_rows, *, spot, expiry, as_of, spot_timestamp, years, rate, dividend,
                 future_quote=None, max_quote_age_seconds=120):
    result = {'model': None, 'source': 'unavailable', 'forward': None, 'synthetic_forward': None,
              'basis': None, 'confidence': 'UNAVAILABLE', 'is_synthetic': False,
              'rate': number(rate), 'dividend': number(dividend), 'spread_percent': None,
              'reason': 'Verified RBI rate or time to expiry unavailable'}
    spot, years = number(spot), number(years)
    if years is None or years <= 0 or result['rate'] is None:
        return result
    spot_ok = spot is not None and spot > 0 and fresh(spot_timestamp, as_of, max_quote_age_seconds)
    candidates = [n for n in chain_rows if str(n.get('expiry', ''))[:10] == str(expiry)[:10]
                  and (number(n.get('strike_price', n.get('strike'))) or 0) > 0]
    if candidates and spot_ok:
        node = min(candidates, key=lambda n: (abs(float(n.get('strike_price', n.get('strike')))-spot),
                                            float(n.get('strike_price', n.get('strike')))))
        strike = float(node.get('strike_price', node.get('strike')))
        result['atm_strike'] = strike
        def side(name):
            leg = node.get(name) or {}
            return book(leg.get('market_data', leg), as_of, max_quote_age_seconds)
        call, put = side('call_options'), side('put_options')
        if call and put:
            spread = result['spread_percent'] = max(call['spread'], put['spread'])
            paired = abs((call['timestamp']-put['timestamp']).total_seconds()) <= 5
            if spread < 3 and paired:
                forward = strike + math.exp(result['rate']*years)*(call['mid']-put['mid'])
                if math.isfinite(forward) and forward > 0:
                    result['synthetic_forward'] = forward
    future = book(future_quote, as_of, max_quote_age_seconds)
    if future and str((future_quote or {}).get('expiry', ''))[:10] == str(expiry)[:10]:
        result.update(model='black76', source='listed_future', forward=future['mid'],
                      confidence='VALIDATED_BOOK', reason='Fresh future with matching expiry', expiry=str(expiry)[:10])
    elif result['synthetic_forward'] is not None:
        result.update(model='black76', source='synthetic_forward', forward=result['synthetic_forward'],
                      confidence='VALIDATED_OPTION_BOOKS', is_synthetic=True,
                      reason='Matched ATM call/put spreads below 3%', expiry=str(expiry)[:10])
    elif spot_ok and result['dividend'] is not None:
        forward = spot*math.exp((result['rate']-result['dividend'])*years)
        result.update(model='bsm', source='cost_of_carry_forward', forward=forward, confidence='THEORETICAL',
                      reason='No eligible matched future or tight synthetic forward')
    else:
        result['reason'] = 'Fresh spot or verified NSE dividend yield unavailable'
    if spot_ok and result['forward'] is not None:
        result['basis'] = result['forward']-spot
    return result


def price_observed_option(price, strike, years, kind, selection, *, spot):
    """Return model IV and Greeks separately from any captured broker observations."""
    from option_math import calculate_iv, calculate_greeks
    model = selection.get('model')
    if model not in ('black76', 'bsm'):
        return {'iv': None, 'model': None}
    underlying = selection['forward'] if model == 'black76' else spot
    dividend = selection.get('dividend') if model == 'bsm' else 0.0
    iv = calculate_iv(price, underlying, strike, years, selection['rate'], kind, model=model, dividend=dividend)
    if not math.isfinite(iv):
        return {'iv': None, 'model': model}
    return {'iv': iv, 'model': model, **calculate_greeks(underlying, strike, years, selection['rate'], iv,
                                                      kind, model=model, dividend=dividend)}
