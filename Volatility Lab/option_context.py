"""Read-only IV context sourced only from Google Cloud captures."""
import sqlite3
from contextlib import closing
import pandas as pd
from history_policy import compatible

def recorded_context(path,symbol,config):
    with closing(sqlite3.connect(path)) as con:
        frame=pd.read_sql_query("SELECT rowid AS Revision,date AS Date,expiry AS Expiry,iv AS IV,spread AS Spread,source AS Source,config AS Config FROM expiry_observations WHERE symbol=? AND source='Upstox supplied IV (Google Cloud)'",con,params=[symbol],parse_dates=['Date'])
    if frame.empty:return frame
    
    # Continuous Front-Month Roll Policy:
    # On expiration day (DTE = 0), contracts undergo gamma pinning and Black-Scholes
    # vega vanishing (T -> 0), producing numerical inversion artifacts.
    # Continuous index series roll on expiry day to the next active forward contract (Expiry > Date).
    valid = frame[pd.to_numeric(frame.IV, errors='coerce').gt(0)].copy()
    unexpired = valid[pd.to_datetime(valid.Expiry) > valid.Date].copy()
    if not unexpired.empty:
        frame = unexpired
    else:
        frame = valid[(pd.to_datetime(valid.Expiry) >= valid.Date) & pd.to_numeric(valid.IV).le(80.0)].copy()

    frame['Current']=frame.Config.eq(config)
    # Nearest recorded unexpired contract per day, preferring current settings on ties.
    frame=frame.sort_values(['Date','Expiry','Current','Revision'],ascending=[True,True,False,False]).drop_duplicates('Date').sort_values('Date')
    # Recorded changes in the risk-free rate are part of market history, not a method change.
    # Dividend assumptions, quote-quality convention and spread filters must still match.
    families={value:min(compatible(path,value)) for value in frame.Config.unique()}
    frame['Series']=frame.Expiry+' | '+frame.Config.map(families)+' | '+frame.Source
    frame['Source']=frame.Source+' · expiry '+frame.Expiry+' · '+frame.Config.where(~frame.Current,'current settings')
    return frame.reset_index(drop=True)


def transition_context(path,symbol,config):
    """Return broker-observed IV only; derived estimates stay out of live charts."""
    return recorded_context(path,symbol,config)
