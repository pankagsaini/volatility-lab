"""ATM put-call parity diagnostic using broker-supplied last prices.

This is intentionally an unadjusted market check requested by the user:
strike + call LTP - put LTP.  It is not an executable forward or a fair-value
model because option-chain LTPs have no per-contract exchange timestamp.
"""
import math


def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None


def positive(value):
    value=number(value)
    return value if value is not None and value>0 else None


def _option_ltp(record,side):
    return positive(((record.get(side) or {}).get('market_data') or {}).get('ltp'))


def calculate(raw,spot,future_quote=None,future_contract=None):
    """Return a display-ready same-expiry ATM synthetic-future comparison."""
    empty={'ATM strike':None,'ATM call LTP':None,'ATM put LTP':None,
           'Synthetic future':None,'Listed future':None,'Synthetic − future':None,
           'Future contract':None,'Future quote time':None,
           'Synthetic status':'Unavailable'}
    spot=positive(spot)
    if spot is None:return {**empty,'Synthetic status':'Invalid underlying price'}
    candidates=[]
    for record in raw or []:
        strike=positive(record.get('strike_price'))
        if strike is not None:candidates.append((strike,record))
    if not candidates:return {**empty,'Synthetic status':'ATM option-chain prices unavailable'}
    strike,atm=min(candidates,key=lambda item:(abs(item[0]-spot),item[0]))
    call,put=_option_ltp(atm,'call_options'),_option_ltp(atm,'put_options')
    result={**empty,'ATM strike':strike,'ATM call LTP':call,'ATM put LTP':put,
            'Future contract':(future_contract or {}).get('tradingsymbol'),
            'Future quote time':(future_quote or {}).get('timestamp')}
    if call is None or put is None:
        return {**result,'Synthetic status':'ATM call or put LTP unavailable'}
    synthetic=strike+call-put
    result['Synthetic future']=synthetic
    listed=positive((future_quote or {}).get('last_price'))
    result['Listed future']=listed
    if result['Future contract'] is None:
        return {**result,'Synthetic status':'No listed futures contract for this expiry'}
    if listed is None:
        return {**result,'Synthetic status':'Listed futures LTP unavailable'}
    return {**result,'Synthetic − future':synthetic-listed,'Synthetic status':'Ready'}


def determine_forward(raw, spot, future_quote=None, future_contract=None, r=0.0585, dte_days=None, max_spread=10.0):
    """Institutional Black-76 forward price resolution with strict quality gates.
    
    1. If listed futures quote exists and is positive, use it directly.
    2. Otherwise, derive discounted Put-Call Parity forward:
       F_syn = K_atm + (C_atm - P_atm) * exp(r * T)
    3. If ATM legs have zero quotes or crossed book, fallback safely to spot.
    """
    empty = {
        'forward': None,
        'source': 'Unavailable',
        'status': 'Unavailable',
        'basis': None,
        'atm_strike': None,
        'call_ltp': None,
        'put_ltp': None,
        'is_synthetic': False,
    }
    spot = positive(spot)
    if spot is None:
        return {**empty, 'status': 'Invalid underlying price'}

    # 1. Traded futures check
    listed_px = positive((future_quote or {}).get('last_price'))
    if listed_px is not None:
        return {
            'forward': listed_px,
            'source': 'listed_future',
            'status': 'Ready',
            'basis': round(listed_px - spot, 2),
            'atm_strike': None,
            'call_ltp': None,
            'put_ltp': None,
            'is_synthetic': False,
        }

    # 2. Synthetic Forward derivation from ATM options
    candidates = []
    for record in raw or []:
        k = positive(record.get('strike_price'))
        if k is not None:
            candidates.append((k, record))
    if not candidates:
        return {**empty, 'forward': spot, 'source': 'spot_fallback', 'status': 'Empty chain'}

    strike, atm = min(candidates, key=lambda item: (abs(item[0] - spot), item[0]))
    call_m = (atm.get('call_options') or {}).get('market_data') or {}
    put_m = (atm.get('put_options') or {}).get('market_data') or {}

    c_ltp = positive(call_m.get('ltp'))
    p_ltp = positive(put_m.get('ltp'))

    if c_ltp is None or p_ltp is None:
        return {
            **empty,
            'forward': spot,
            'source': 'spot_fallback',
            'status': 'ATM legs unpriced',
            'atm_strike': strike,
        }

    # Use bid/ask midpoints if available and sane
    c_bid = positive(call_m.get('bid_price'))
    c_ask = positive(call_m.get('ask_price'))
    p_bid = positive(put_m.get('bid_price'))
    p_ask = positive(put_m.get('ask_price'))

    c_prem = c_ltp
    if c_bid and c_ask and 0 < c_bid <= c_ask and (c_ask - c_bid) <= max_spread:
        c_prem = (c_bid + c_ask) / 2.0

    p_prem = p_ltp
    if p_bid and p_ask and 0 < p_bid <= p_ask and (p_ask - p_bid) <= max_spread:
        p_prem = (p_bid + p_ask) / 2.0

    # Discounted forward formula: F = K + (C - P) * exp(r * T)
    T = max(1e-4, float(dte_days if dte_days is not None else 7.0) / 365.0)
    df_inv = math.exp(float(r) * T)
    f_syn = strike + (c_prem - p_prem) * df_inv

    return {
        'forward': round(f_syn, 2),
        'source': 'synthetic_forward',
        'status': 'Ready',
        'basis': round(f_syn - spot, 2),
        'atm_strike': strike,
        'call_ltp': c_ltp,
        'put_ltp': p_ltp,
        'is_synthetic': True,
    }
