"""Provider IV/Greeks ingestion. Never solve IV or infer missing Greeks."""
import numpy as np
import pandas as pd

def chain_from_response(raw,expiry):
    records=[];audit=[]
    for row in raw:
        if str(row.get('expiry'))!=str(expiry):raise ValueError('Broker returned a different expiry')
        strike=float(row['strike_price'])
        for side,kind in [('call_options','CE'),('put_options','PE')]:
            leg=row.get(side) or {};g=leg.get('option_greeks') or {};m=leg.get('market_data') or {}
            def number(v):
                try:return float(v) if np.isfinite(float(v)) else np.nan
                except (TypeError,ValueError):return np.nan
            iv=number(g.get('iv'));delta=number(g.get('delta'))
            valid=np.isfinite(iv) and iv>0
            audit.append(dict(strike=strike,option_type=kind,accepted=valid,reason='broker_iv_supplied' if valid else 'broker_iv_missing_or_nonpositive',source='Upstox supplied IV'))
            if not valid:continue
            records.append(dict(strike=strike,option_type=kind,iv=iv/100,delta=delta,source='Upstox supplied IV',
                gamma=number(g.get('gamma')),theta=number(g.get('theta')),vega=number(g.get('vega')),
                bid=number(m.get('bid_price')),ask=number(m.get('ask_price')),ltp=number(m.get('ltp')),
                volume=m.get('volume'),oi=m.get('oi'),instrument_key=leg.get('instrument_key')))
    frame=pd.DataFrame(records,columns=['strike','option_type','iv','delta','source','gamma','theta','vega','bid','ask','ltp','volume','oi','instrument_key'])
    frame.attrs['quote_quality']=audit
    return frame
