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


def exclude_modeled_daily(frame, connection, symbol):
    """Quarantine summaries contradicted by their retained intraday provenance."""
    if frame.empty or not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='intraday_observations'"
    ).fetchone():
        return frame
    from volatility_lab.pricing.pricing_policy import number

    modeled, broker = set(), {}
    rows = connection.execute(
        'SELECT expiry,config,as_of,payload FROM intraday_observations WHERE symbol=?', (symbol,)
    )
    for expiry, config, as_of, payload in rows:
        try:
            row = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        key = (str(as_of)[:10], expiry, config)
        if broker_sourced(row):
            broker.setdefault(key, set()).add(number(row.get('IV')))
        elif row.get('Source') or row.get('Greeks source'):
            modeled.add(key)
    accepted = []
    for row in frame.itertuples():
        key = (str(row.Date)[:10], row.Expiry, row.Config)
        accepted.append(key not in modeled or number(row.IV) in broker.get(key, set()))
    return frame.loc[accepted].copy()


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
        from expiry_forecast import completed_cutoff
        # Crossing the actual session close changes the available daily candles.
        alignments={}
        for i,row in frame.iterrows():
            stamp=pd.Timestamp(row['As of'])
            cutoff=completed_cutoff(stamp.tz_convert('Asia/Kolkata')) if stamp.tzinfo else str(stamp)
            key=(str(cutoff),row.get('Source',''))
            if key not in alignments:alignments[key]=aligned_spread(0.,row['As of'],key[1],prices)
            values=dict(alignments[key])
            iv=pd.to_numeric(row.get('IV'),errors='coerce')
            values['IV-RV Spread']=iv-values['HV30'] if pd.notna(iv) else float('nan')
            for column,value in values.items():frame.loc[i,column]=value
        frame['As of']=pd.to_datetime(frame['As of'],format='ISO8601',utc=True).dt.tz_convert('Asia/Kolkata')
        frame=frame.sort_values('As of',kind='stable').reset_index(drop=True)
    return frame
