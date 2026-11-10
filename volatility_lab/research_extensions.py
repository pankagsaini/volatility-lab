"""Events, volatility-of-volatility and aligned cross-index diagnostics."""
import io
from datetime import time,timedelta
import numpy as np
import pandas as pd
from vol_lib import validate_ohlc


def completed(frame,as_of):
    stamp=pd.Timestamp(as_of)
    if pd.isna(stamp) or stamp.tzinfo is None:raise ValueError('Timezone-aware valuation required')
    stamp=stamp.tz_convert('Asia/Kolkata')
    cutoff=stamp.date() if stamp.time()>=time(15,40) else stamp.date()-timedelta(days=1)
    data=validate_ohlc(frame)
    return data[data.index.date<=cutoff]


def vol_of_vol(frame,as_of):
    data=completed(frame,as_of)
    r=np.log(data.Close/data.Close.shift())
    hv=r.rolling(30).std()*np.sqrt(245)*100
    change=hv.diff()
    out=pd.DataFrame({'Date':data.index,'HV30 %':hv.values,
        'VoV20 · pp per session':change.rolling(20).std().values,
        'VoV60 · pp per session':change.rolling(60).std().values})
    return out


def cross_index(frames,as_of,window=60):
    if window not in (20,60,120,245):raise ValueError('Unsupported correlation window')
    prices=pd.concat({s:completed(f,as_of).Close for s,f in frames.items()},axis=1).sort_index()
    # Union dates first: a missing price invalidates adjacent returns instead of
    # correlating a two-session return with another index's one-session return.
    returns=np.log(prices/prices.shift()).dropna(how='any').tail(window)
    if len(returns)<window:raise ValueError(f'Need {window} aligned complete return intervals; available {len(returns)}.')
    correlation=returns.corr()
    dispersion=returns.std(axis=1,ddof=0)*100
    return correlation,pd.DataFrame({'Date':returns.index,'Cross-index return dispersion · pp':dispersion.values})


def validate_events(raw):
    events=pd.read_csv(io.BytesIO(raw))
    required=['Event','Date','Timezone','Instrument','Source']
    if not set(required).issubset(events):raise ValueError('Event CSV requires '+', '.join(required))
    if events[required].isna().any().any():raise ValueError('All event fields are required')
    for row in events.itertuples():
        stamp=pd.Timestamp(row.Date)
        if pd.isna(stamp):raise ValueError('Invalid event date')
        if str(row.Date)!=stamp.strftime('%Y-%m-%d'):raise ValueError('Dates must use YYYY-MM-DD without a time')
        try:stamp.tz_localize(str(row.Timezone))
        except Exception as error:raise ValueError('Unknown event timezone') from error
        if row.Instrument not in ['ALL','NIFTY','BANKNIFTY']:raise ValueError('Unknown event instrument')
        if not str(row.Source).startswith('https://'):raise ValueError('Provide an HTTPS source URL')
    events['Date']=pd.to_datetime(events.Date).dt.strftime('%Y-%m-%d')
    return events[required].drop_duplicates()


def upcoming(events,symbol,as_of,expiry):
    records=[]
    now=pd.Timestamp(as_of)
    if now.tzinfo is None:raise ValueError('Timezone-aware valuation required')
    end=pd.Timestamp(str(expiry)+'T15:40:00+05:30')
    for row in events.itertuples():
        if row.Instrument not in ('ALL',symbol):continue
        start=pd.Timestamp(row.Date).tz_localize(row.Timezone)
        finish=start+pd.DateOffset(days=1)
        if finish<=now or start>end+pd.Timedelta(days=30):continue
        relation='Before expiry' if finish<=end else 'After expiry' if start>end else 'Expiry-day timing uncertain'
        records.append({'Event':row.Event,'Date':row.Date,'Timezone':row.Timezone,
                        'Relative to expiry':relation,'Source':row.Source})
    return pd.DataFrame(records)


def constituent_dispersion(raw,index_iv,expiry,as_of):
    frame=pd.read_csv(io.BytesIO(raw))
    required=['Constituent','Weight','IV','Expiry','AsOf','Source']
    if not set(required).issubset(frame):raise ValueError('Requires '+', '.join(required))
    if len(frame)<2 or frame[required].isna().any().any() or frame.Constituent.duplicated().any():
        raise ValueError('Unique complete constituent rows required')
    w=pd.to_numeric(frame.Weight).to_numpy();iv=pd.to_numeric(frame.IV).to_numpy()/100
    if not np.isfinite(w).all() or not np.isfinite(iv).all() or (w<=0).any() or (iv<=0).any():raise ValueError('Invalid weights or IV')
    if not np.isclose(w.sum(),1.,atol=1e-6,rtol=0):raise ValueError('Provide full constituent weights summing to 1; no silent normalization')
    if not np.isfinite(index_iv) or index_iv<=0:raise ValueError('Selected index IV unavailable')
    if not (pd.to_datetime(frame.Expiry).dt.date==pd.Timestamp(expiry).date()).all():raise ValueError('All expiry dates must match the index')
    for value in frame.AsOf:
        stamp=pd.Timestamp(value)
        if stamp.tzinfo is None or stamp!=pd.Timestamp(as_of):raise ValueError('All valuation timestamps must exactly match the index')
    individual=float(np.sum(w*w*iv*iv));denominator=float(np.sum(w*iv)**2-individual)
    if denominator<=0:raise ValueError('Degenerate constituent variance')
    rho=((index_iv/100)**2-individual)/denominator
    return {'ATM implied correlation proxy':rho,'Weighted individual variance':individual,
            'Index variance proxy':(index_iv/100)**2,
            'Status':'Within algebraic bounds' if -1<=rho<=1 else 'Inconsistent inputs / approximation outside [-1,1]'}
