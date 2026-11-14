"""Descriptive HV regime; fixed thresholds, strictly prior reference values."""
from datetime import time, timedelta
import numpy as np
import pandas as pd
from vol_lib import validate_ohlc
from expiry_forecast import completed_cutoff

VERSION='hv-regime-v1'


def regime_input(root,symbol):
    paths=sorted((root/'underlying_history').glob(symbol.lower()+'_*.csv'),reverse=True)
    if symbol=='NIFTY':paths.append(root/'nifty_daily.csv')
    paths.append(root/(symbol.lower()+'_one_year.csv'))
    for path in paths:
        if path.exists():return path
    raise ValueError('No saved underlying history available.')


def classify(percentile):
    if percentile is None:return 'Insufficient history'
    if percentile<=20:return 'Low'
    if percentile>=80:return 'Elevated'
    return 'Typical'


def regime_summary(frame,as_of):
    stamp=pd.Timestamp(as_of)
    if pd.isna(stamp) or stamp.tzinfo is None:raise ValueError('A dated, timezone-aware quote is required.')
    stamp=stamp.tz_convert('Asia/Kolkata')
    cutoff=completed_cutoff(stamp)
    data=validate_ohlc(frame)
    data=data[data.index.date<=cutoff]
    if len(data)<31:raise ValueError('At least 31 completed candles are required.')
    end=data.index[-1];start=end-pd.DateOffset(years=1)
    returns=np.log(data.Close/data.Close.shift())
    records=[]
    for window in (10,30):
        hv=returns.rolling(window).std(ddof=1)*np.sqrt(245)*100
        current=float(hv.iloc[-1])
        prior=hv[(hv.index>=start)&(hv.index<end)].dropna()
        percentile=None
        if len(prior)>=200 and prior.index.min()<=start+pd.Timedelta(days=10):
            percentile=float(100*((prior<current).sum()+.5*(prior==current).sum())/len(prior))
        records.append({'Measure':f'HV{window}','HV %':current,'Prior-year percentile':percentile,
            'Regime':classify(percentile),'Prior sessions':len(prior),
            'As of':str(end.date()),'Reference start':str(prior.index.min().date()) if len(prior) else None,
            'Reference end':str(prior.index.max().date()) if len(prior) else None})
    return pd.DataFrame(records)
