"""Timestamped Upstox observations imported from Google Cloud only."""
from contextlib import closing
import json
import sqlite3
import pandas as pd


def save(path, config, row):
    if not row.get('As of') or row.get('Capture source')!='Google Drive':return
    with closing(sqlite3.connect(path)) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS intraday_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of))')
        key=(row['Instrument'],row['Expiry'],config,row['As of'])
        existing=con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=?',key).fetchone()
        if existing:
            try:trusted=json.loads(existing[0]).get('Capture source')=='Google Drive'
            except (TypeError,ValueError):trusted=False
            if trusted:return
            con.execute('UPDATE intraday_observations SET payload=? WHERE symbol=? AND expiry=? AND config=? AND as_of=?',
                        (json.dumps(row),*key))
        else:
            con.execute('INSERT INTO intraday_observations VALUES (?,?,?,?,?)',(*key,json.dumps(row)))


def load(path, symbol, expiry, config):
    if not path.exists():return pd.DataFrame()
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='intraday_observations'").fetchone():return pd.DataFrame()
        rows=con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? ORDER BY as_of',
                         (symbol,str(expiry),config)).fetchall()
    records=[]
    for (raw,) in rows:
        try:row=json.loads(raw)
        except (TypeError,ValueError):continue
        if row.get('Capture source')=='Google Drive' and row.get('Source')=='Upstox supplied IV':records.append(row)
    frame=pd.DataFrame(records)
    if len(frame):
        from market_table import aligned_spread
        try:prices=pd.read_csv(path.parent/f'{symbol.lower()}_one_year.csv')
        except (OSError,ValueError):prices=None
        # RV alignment depends on session/source, not on each intraday quote's IV.
        alignments={}
        for i,row in frame.iterrows():
            key=(str(pd.Timestamp(row['As of']).date()),row.get('Source',''))
            if key not in alignments:alignments[key]=aligned_spread(0.,row['As of'],key[1],prices)
            values=dict(alignments[key])
            iv=pd.to_numeric(row.get('IV'),errors='coerce')
            values['IV-RV Spread']=iv-values['HV30'] if pd.notna(iv) else float('nan')
            for column,value in values.items():frame.loc[i,column]=value
        frame['As of']=pd.to_datetime(frame['As of'],format='ISO8601',utc=True).dt.tz_convert('Asia/Kolkata')
        frame=frame.sort_values('As of',kind='stable').reset_index(drop=True)
    return frame
