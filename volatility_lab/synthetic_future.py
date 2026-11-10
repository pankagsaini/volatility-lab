"""Forward Price & ATM Parity Resolution Engine (Black-76 and Cost-of-Carry).

Provides forward price determination and parity diagnostics:
1. determine_forward(): Institutional forward resolution prioritizing:
   a) Traded listed futures quote when fresh and available (source='listed_future').
   b) Discounted Put-Call parity forward F = K_atm + (C_atm - P_atm)*exp(r*T) using
      ATM bid/ask midpoints with quality spread caps (source='synthetic_forward').
   c) Theoretical cost-of-carry forward F = S * exp((r - q)*T) (source='cost_of_carry_forward').
2. theoretical_forward(): Explicit cost-of-carry theoretical forward from spot, rate, and dividend yield.
3. calculate(): Diagnostic unadjusted market check (strike + call LTP - put LTP).
"""
import math


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def _option_ltp(record, side):
    return positive(((record.get(side) or {}).get('market_data') or {}).get('ltp'))


def theoretical_forward(spot, years, rate=0.0585, dividend=0.0):
    """Derive theoretical cost-of-carry forward: F = S * exp((r - q) * T)."""
    spot_val = positive(spot)
    if spot_val is None:
        return None
    t_val = max(0.0, float(years))
    return round(spot_val * math.exp((float(rate) - float(dividend)) * t_val), 2)


def calculate(raw, spot, future_quote=None, future_contract=None):
    """Return a display-ready same-expiry ATM synthetic-future comparison."""
    empty = {
        'ATM strike': None,
        'ATM call LTP': None,
        'ATM put LTP': None,
        'Synthetic future': None,
        'Listed future': None,
        'Synthetic − future': None,
        'Basis': None,
        'Future contract': None,
        'Future quote time': None,
        'Synthetic status': 'No option chain data',
    }
    spot = positive(spot)
    if spot is None:
        return {**empty, 'Synthetic status': 'Spot unavailable'}
    candidates = []
    for record in raw or []:
        strike = positive(record.get('strike_price'))
        if strike is not None:
            candidates.append((strike, record))
    if not candidates:
        return empty
    strike, atm = min(candidates, key=lambda item: (abs(item[0] - spot), item[0]))
    call = _option_ltp(atm, 'call_options')
    put = _option_ltp(atm, 'put_options')
    result = {
        'ATM strike': strike,
        'ATM call LTP': call,
        'ATM put LTP': put,
        'Synthetic future': None,
        'Listed future': None,
        'Synthetic − future': None,
        'Basis': None,
        'Future contract': (future_contract or {}).get('tradingsymbol'),
        'Future quote time': (future_quote or {}).get('timestamp'),
    }
    if call is None or put is None:
        return {**result, 'Synthetic status': 'ATM call or put LTP unavailable'}
    synthetic = strike + call - put
    result['Synthetic future'] = synthetic
    listed = positive((future_quote or {}).get('last_price'))
    result['Listed future'] = listed
    if result['Future contract'] is None:
        return {**result, 'Synthetic status': 'No listed futures contract for this expiry'}
    if listed is None:
        return {**result, 'Synthetic status': 'Listed futures LTP unavailable'}
    return {**result, 'Synthetic − future': synthetic - listed, 'Synthetic status': 'Ready'}


def determine_forward(
    raw,
    spot,
    future_quote=None,
    future_contract=None,
    r=0.0585,
    q=0.0,
    dte_days=None,
    max_spread=10.0,
    quote_time=None,
):
    """Institutional Black-76 forward price resolution with strict quality gates.
    
    Order of Resolution:
    1. Traded futures: If listed futures quote exists and is positive, use it.
    2. Synthetic forward: Derive discounted Put-Call Parity forward:
       F_syn = K_atm + (C_atm - P_atm) * exp(r * T)
    3. Theoretical forward: Cost-of-carry forward F_theo = S * exp((r - q) * T).
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
        'quote_time': None,
    }
    spot_val = positive(spot)
    if spot_val is None:
        return {**empty, 'status': 'Invalid underlying price'}

    q_time = (future_quote or {}).get('timestamp') or quote_time
    T = max(1e-4, float(dte_days if dte_days is not None else 7.0) / 365.0)

    # 1. Traded futures check
    listed_px = positive((future_quote or {}).get('last_price'))
    if listed_px is not None:
        return {
            'forward': listed_px,
            'source': 'listed_future',
            'status': 'Ready',
            'basis': round(listed_px - spot_val, 2),
            'atm_strike': None,
            'call_ltp': None,
            'put_ltp': None,
            'is_synthetic': False,
            'quote_time': q_time,
        }

    # 2. Synthetic Forward derivation from ATM options
    candidates = []
    for record in raw or []:
        k = positive(record.get('strike_price'))
        if k is not None:
            candidates.append((k, record))
    if candidates:
        strike, atm = min(candidates, key=lambda item: (abs(item[0] - spot_val), item[0]))
        call_m = (atm.get('call_options') or {}).get('market_data') or {}
        put_m = (atm.get('put_options') or {}).get('market_data') or {}

        c_ltp = positive(call_m.get('ltp'))
        p_ltp = positive(put_m.get('ltp'))

        if c_ltp is not None and p_ltp is not None:
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

            df_inv = math.exp(float(r) * T)
            f_syn = strike + (c_prem - p_prem) * df_inv

            return {
                'forward': round(f_syn, 2),
                'source': 'synthetic_forward',
                'status': 'Ready',
                'basis': round(f_syn - spot_val, 2),
                'atm_strike': strike,
                'call_ltp': c_ltp,
                'put_ltp': p_ltp,
                'is_synthetic': True,
                'quote_time': q_time,
            }

    # 3. Fallback to Theoretical Cost-of-Carry Forward
    f_theo = round(spot_val * math.exp((float(r) - float(q)) * T), 2)
    return {
        'forward': f_theo,
        'source': 'cost_of_carry_forward',
        'status': 'Theoretical cost-of-carry',
        'basis': round(f_theo - spot_val, 2),
        'atm_strike': None,
        'call_ltp': None,
        'put_ltp': None,
        'is_synthetic': True,
        'quote_time': q_time,
    }
