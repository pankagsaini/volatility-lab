"""Timestamped Upstox observations imported from Google Cloud only."""
from contextlib import closing
import json
import sqlite3
import pandas as pd


def broker_sourced(row):
    sources = ('Upstox supplied Greeks', 'Upstox supplied IV', 'Upstox supplied IV (Google Cloud)')
    return (isinstance(row, dict) and row.get('Source') in sources
            and row.get('Greeks source') in (None, 'Upstox supplied Greeks'))


def historical_fields(row):
    if broker_sourced(row):
        return row
    # Earlier snapshots sometimes stored model output alongside genuine spot quotes.
    fields = ('IV', 'Call IV', 'Put IV', '25 Delta reversal', 'IV-RV Spread',
              'Reversal put delta', 'Reversal call delta', 'IV - GARCH', 'IV - EWMA')
    return {**row, **{name: None for name in fields if name in row}}


def save(path, config, row):
    if not broker_sourced(row) or not row.get('As of') or row.get('Capture source')!='Google Drive':return
    with closing(sqlite3.connect(path)) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS intraday_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of))')
        key=(row['Instrument'],row['Expiry'],config,row['As of'])
        con.execute('INSERT INTO intraday_observations VALUES (?,?,?,?,?) '
                    'ON CONFLICT(symbol,expiry,config,as_of) DO UPDATE SET payload=excluded.payload',
                    (*key,json.dumps(row)))


def load(path, symbol, expiry, config=None):
    if not path.exists():return pd.DataFrame()
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='intraday_observations'").fetchone():return pd.DataFrame()
        rows=[]
        if config:
            rows=con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? ORDER BY as_of',
                             (symbol,str(expiry),config)).fetchall()
        if not rows:
            rows=con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? ORDER BY as_of',
                             (symbol,str(expiry))).fetchall()
    records=[]
    for (raw,) in rows:
        try:row=json.loads(raw)
        except (TypeError,ValueError):continue
        if row.get('Capture source') in ('Google Drive', 'Google Cloud', 'Local') or 'Upstox' in str(row.get('Source', '')):
            records.append(historical_fields(row))
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
