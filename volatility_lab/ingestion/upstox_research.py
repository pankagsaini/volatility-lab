"""Source-labelled research views from verified Upstox candle archives."""
from contextlib import closing
from datetime import datetime
import sqlite3
import numpy as np
import pandas as pd
from upstox_archive import ROOT,connect
from upstox_data import INDICES

def minute_data(symbol):
    if not (ROOT/'research.sqlite').exists():return pd.DataFrame()
    with closing(connect()) as con, con:
        frame=pd.read_sql_query("SELECT stamp,open,high,low,close,volume FROM candles WHERE instrument=? AND interval='1minute' ORDER BY stamp",con,params=(INDICES[symbol],))
    if not frame.empty:frame['stamp']=pd.to_datetime(frame.stamp,utc=True).dt.tz_convert('Asia/Kolkata')
    return frame

def realized_variance(symbol):
    frame=minute_data(symbol)
    if frame.empty:return pd.DataFrame(columns=['Date','RV','AvailableAt','Source'])
    rows=[]
    for day,g in frame.groupby(frame.stamp.dt.date):
        expected=pd.date_range(str(day)+' 09:15',periods=375,freq='min',tz='Asia/Kolkata')
        g=g.set_index('stamp').reindex(expected)
        if g.close.isna().any() or g.open.isna().any():continue
        prices=np.r_[float(g.open.iloc[0]),g.close.iloc[4::5].to_numpy()]
        rv=float(np.square(np.diff(np.log(prices))).sum())
        rows.append({'Date':pd.Timestamp(day),'RV':rv,'AvailableAt':pd.Timestamp(str(day)+' 16:30',tz='Asia/Kolkata'),'Source':'Upstox 5-minute sampled RV; complete 375-minute session'})
    return pd.DataFrame(rows,columns=['Date','RV','AvailableAt','Source'])



def futures_minute_pair(symbol,front,back):
    from futures_data import pair_values
    if not (ROOT/'research.sqlite').exists():return pd.DataFrame()
    with closing(connect()) as con, con:
        raw=pd.read_sql_query("SELECT a.stamp,a.close,a.volume,c.expiry FROM candles a JOIN contracts c ON c.instrument=a.instrument WHERE c.symbol=? AND c.kind='FUT' AND a.interval='1minute' AND c.expiry IN (?,?) ORDER BY a.stamp",con,params=(symbol,str(front),str(back)))
    if raw.empty:return pd.DataFrame()
    raw=raw[raw.volume>0]
    # Conflicting duplicate contract records are excluded, not averaged.
    counts=raw.groupby(['stamp','expiry']).close.transform('nunique')
    raw=raw[counts==1].drop_duplicates(['stamp','expiry'])
    pivot=raw.pivot(index='stamp',columns='expiry',values='close')
    if str(front) not in pivot or str(back) not in pivot:return pd.DataFrame()
    rows=[]
    for stamp,r in pivot.dropna(subset=[str(front),str(back)]).iterrows():
        rows.append({'Date':pd.Timestamp(stamp),**pair_values(float(r[str(front)]),float(r[str(back)]),pd.Timestamp(front),pd.Timestamp(back)),'Source':'Upstox matched one-minute closes'})
    return pd.DataFrame(rows)
