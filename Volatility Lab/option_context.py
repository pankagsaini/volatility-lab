"""Read-only IV context sourced only from Google Cloud captures."""
import sqlite3
from contextlib import closing
import pandas as pd
from history_policy import compatible

def recorded_context(path,symbol,config,max_iv=150.0):
    if isinstance(config, dict):
        max_iv = float(config.get('max_iv', max_iv))
    with closing(sqlite3.connect(path, timeout=15)) as con:
        frame=pd.read_sql_query("SELECT rowid AS Revision,date AS Date,expiry AS Expiry,iv AS IV,spread AS Spread,source AS Source,config AS Config FROM expiry_observations WHERE symbol=? AND source='Upstox supplied IV (Google Cloud)'",con,params=[symbol],parse_dates=['Date'])
    if frame.empty:return frame

    valid = frame[pd.to_numeric(frame.IV, errors='coerce').gt(0) & pd.to_numeric(frame.IV, errors='coerce').le(max_iv)].copy()
    unexpired = valid[pd.to_datetime(valid.Expiry) > valid.Date].copy()
    if not unexpired.empty:
        frame = unexpired
    else:
        frame = valid[(pd.to_datetime(valid.Expiry) >= valid.Date) & pd.to_numeric(valid.IV).le(max_iv)].copy()

    frame['Current']=frame.Config.eq(config)
    frame=frame.sort_values(['Date','Expiry','Current','Revision'],ascending=[True,True,False,False]).drop_duplicates('Date').sort_values('Date')
    families={value:min(compatible(path,value)) for value in frame.Config.unique()}
    frame['Series']=frame.Expiry+' | '+frame.Config.map(families)+' | '+frame.Source
    frame['Source']=frame.Source+' · expiry '+frame.Expiry+' · '+frame.Config.where(~frame.Current,'current settings')
    return frame.reset_index(drop=True)


def transition_context(path,symbol,config):
    """Return broker-observed IV only; derived estimates stay out of live charts."""
    return recorded_context(path,symbol,config)
