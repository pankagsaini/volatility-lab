"""Independent arithmetic checks against saved raw broker responses and closes.

Passing verifies ingestion/arithmetic, not the broker's IV model or quote freshness.
No network calls or modifications to market history.
"""
import math
import statistics
import pandas as pd
from synthetic_future import calculate as synthetic_future
from market_calendar import expiry_close
from pricing_policy import recorded_inputs


def finite(value):
    try:return math.isfinite(float(value))
    except (TypeError,ValueError):return False


def delta_reference(raw, side, target=.25):
    points={}
    for record in raw:
        greek=(record.get(side) or {}).get('option_greeks') or {}
        iv,delta=greek.get('iv'),greek.get('delta')
        if not finite(iv) or not finite(delta) or float(iv)<=0:continue
        d=float(delta)
        if (side=='call_options' and not 0<=d<=1) or (side=='put_options' and not -1<=d<=0):continue
        points.setdefault(abs(d),[]).append(float(iv))
    points={d:statistics.mean(v) for d,v in points.items()}
    if target in points:return points[target]
    lower=[d for d in points if d<target]; upper=[d for d in points if d>target]
    if not lower or not upper:return None
    lo,hi=max(lower),min(upper)
    return points[lo]+(target-lo)/(hi-lo)*(points[hi]-points[lo])


def check_row(row,payload,prices):
    checks=[]
    def check(name,actual,expected,tolerance=1e-7):
        comparable=finite(actual) and finite(expected)
        difference=float(actual)-float(expected) if comparable else None
        status=('Match' if abs(difference)<=tolerance else 'Mismatch') if comparable else 'Not verifiable'
        checks.append({'Check':name,'Lab':float(actual) if finite(actual) else None,
                       'Raw/reference':float(expected) if finite(expected) else None,
                       'Difference':difference,'Result':status})
    raw=payload.get('broker_option_chain') or []
    raw=[r for r in raw if str(r.get('expiry'))==str(row.get('Expiry'))]
    spot=payload.get('spot')
    if not raw or not finite(spot):return [{'Check':'Saved broker chain','Result':'Not verifiable'}]
    atm=min(raw,key=lambda r:(abs(float(r['strike_price'])-float(spot)),float(r['strike_price'])))
    call=(atm.get('call_options') or {}).get('option_greeks',{}).get('iv')
    put=(atm.get('put_options') or {}).get('option_greeks',{}).get('iv')
    mean=(float(call)+float(put))/2 if finite(call) and finite(put) and min(float(call),float(put))>0 else None
    check('Spot',row.get('Price'),spot)
    check('Call IV %',row.get('Call IV'),call)
    check('Put IV %',row.get('Put IV'),put)
    check('ATM mean IV %',row.get('IV'),mean)
    p=delta_reference(raw,'put_options');c=delta_reference(raw,'call_options')
    check('25D put − call · pp',row.get('25 Delta reversal'),p-c if p is not None and c is not None else None)
    hv=None
    if prices is not None and row.get('RV through'):
        dates=pd.to_datetime(prices.Date).dt.normalize()
        cutoff=pd.Timestamp(row['RV through'])
        selected=prices.loc[dates<=cutoff].copy()
        selected['Date']=dates.loc[selected.index]
        selected=selected.sort_values('Date')
        closes=pd.to_numeric(selected.Close,errors='coerce').tail(31).tolist()
        if len(closes)==31 and all(finite(v) and v>0 for v in closes) and selected.Date.iloc[-1]==cutoff:
            returns=[math.log(b/a) for a,b in zip(closes,closes[1:])]
            hv=statistics.stdev(returns)*math.sqrt(245)*100
    check('HV30 %',row.get('HV30'),hv)
    check('IV − HV30 · pp',row.get('IV-RV Spread'),mean-hv if mean is not None and hv is not None else None)
    expiry = str(row.get('Expiry') or '')[:10]
    def same_month(left, right):
        try:
            a, b = pd.Timestamp(left), pd.Timestamp(right)
            return (a.year, a.month) == (b.year, b.month)
        except (TypeError, ValueError):
            return False
    matching_futures=[
        item for item in payload.get('contracts', [])
        if str(item.get('instrument_type', '')).upper() == 'FUT'
        and same_month(item.get('expiry'), expiry)
    ]
    # The audit must not pick an arbitrary contract when the saved response is
    # ambiguous about which monthly future belongs to this option expiry.
    future=matching_futures[0] if len(matching_futures)==1 else None
    future_symbol=(future.get('tradingsymbol') or future.get('trading_symbol')) if future else None
    future_quote=(payload.get('quotes') or {}).get('NFO:'+future_symbol,{}) if future_symbol else None
    if isinstance(future_quote, dict) and future:
        future_quote = dict(future_quote)
        future_quote['expiry'] = str(future.get('expiry') or '')[:10]
    as_of = payload.get('broker_received_at') or payload.get('valuation')
    spot_quote = payload.get('spot_quote') or {}
    years = float('nan')
    try:
        valuation = pd.Timestamp(as_of)
        end = pd.Timestamp(expiry_close(expiry))
        if valuation.tzinfo is not None:
            years = (end - valuation.tz_convert('Asia/Kolkata')).total_seconds() / (365.0 * 86400.0)
    except (TypeError, ValueError):
        pass
    # Reconstruct only explicitly timestamped two-sided books.  Nested broker
    # chain rows and LTPs are not valid inputs to parity valuation.
    normalized_quotes=[]
    for source_row in raw:
        try:
            strike=float(source_row.get('strike_price'))
        except (TypeError,ValueError):
            continue
        for side,kind in (('call_options','CE'),('put_options','PE')):
            market=(source_row.get(side) or {}).get('market_data') or {}
            normalized_quotes.append({
                'expiry':str(source_row.get('expiry') or '')[:10],
                'strike':strike,
                'option_type':kind,
                'bid':market.get('bid_price'),
                'ask':market.get('ask_price'),
                'timestamp':market.get('quote_timestamp') or market.get('timestamp'),
            })
    rate, dividend = recorded_inputs(payload.get('pricing_inputs') or payload, row.get('Instrument'), as_of)
    synthetic=synthetic_future(
        normalized_quotes, spot, years=years, future_quote=future_quote, future_contract=future,
        rate=rate, dividend=dividend,
        expiry=expiry, as_of=as_of,
        spot_timestamp=spot_quote.get('timestamp'),
    )
    check('ATM synthetic future',row.get('Synthetic future'),synthetic.get('Synthetic future'))
    check('Month future LTP',row.get('Month future LTP', row.get('Listed future')),synthetic.get('Month future LTP'))
    check('Synthetic − future',row.get('Basis', row.get('Synthetic − future')),synthetic.get('Basis'))
    return checks


def comparison_context(row,payload):
    raw=payload.get('broker_option_chain') or []
    spot=payload.get('spot')
    atm=min(raw,key=lambda r:(abs(float(r['strike_price'])-float(spot)),float(r['strike_price']))) if raw and finite(spot) else {}
    return {'Instrument':row.get('Instrument'),'Expiry':row.get('Expiry'),'ATM strike':atm.get('strike_price'),
            'IV basis':'Spot-nearest strike; mean of Upstox call/put IV',
            'RV through':row.get('RV through'),'RV basis':'30 log returns; sample SD; sqrt(245)',
            'Retrieved at':payload.get('broker_received_at'),'Greek exchange time':'Not supplied by chain API'}
