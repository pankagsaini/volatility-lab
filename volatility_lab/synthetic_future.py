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
