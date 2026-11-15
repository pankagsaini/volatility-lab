"""Saved daily prices for model validation, independent of presentation."""
import pandas as pd
from market_data import IST, history_cutoff
from volatility_lab.runtime.settings import data_dir

def full_price_history(symbol):
    """Merge valid saved closes; a delayed provider cannot erase a newer session."""
    DATA = data_dir()
    from vol_lib import validate_ohlc
    import sqlite3
    frames=[]
    for path in reversed(sorted((DATA/'underlying_history').glob(symbol.lower()+'_*.csv'))):
        try:
            candidate=validate_ohlc(pd.read_csv(path)).reset_index()
            frames.insert(0,candidate)
            if len(candidate)>=300:break
        except (OSError,ValueError,KeyError):continue
    for path in [DATA/f'{symbol.lower()}_one_year.csv'] + ([DATA/'nifty_daily.csv'] if symbol=='NIFTY' else []):
        try:frames.append(validate_ohlc(pd.read_csv(path)).reset_index())
        except (OSError,ValueError,KeyError):continue
    archive=DATA/'upstox'/'research.sqlite'
    if archive.exists():
        try:
            from upstox_data import INDICES as keys
            from contextlib import closing
            with closing(sqlite3.connect('file:'+archive.as_posix()+'?mode=ro',uri=True,timeout=5)) as con:
                recent=pd.read_sql_query("SELECT stamp AS Date,open AS Open,high AS High,low AS Low,close AS Close FROM candles WHERE instrument=? AND interval='day' ORDER BY stamp",con,params=(keys[symbol],))
            if not recent.empty:
                recent['Date']=pd.to_datetime(recent.Date,utc=True).dt.tz_convert(IST).dt.tz_localize(None).dt.normalize()
                frames.append(validate_ohlc(recent).reset_index())
        except (OSError,ValueError,KeyError,sqlite3.Error):pass
    if not frames:raise ValueError('No valid saved daily candles are available for '+symbol)
    merged=pd.concat(frames,ignore_index=True).drop_duplicates('Date',keep='last').sort_values('Date')
    merged=merged[merged.Date<=pd.Timestamp(history_cutoff())]
    return validate_ohlc(merged).reset_index()
