"""Append exact Upstox dated futures to the retained historical rolling series."""
from datetime import datetime,timedelta
from contextlib import closing
import pandas as pd
from collector_support import atomic_json
from market_data import IST,history_cutoff,MarketDataError

def append_dated(engine,client,root,symbol,now=None):
    from futures_rolling import setup,contracts,VERSION,rolling_frame
    from market_calendar import last_trading_day_on_or_before
    now=now or datetime.now(IST);engine.masters(client,now)
    listed=contracts(engine.nfo,symbol,now)
    expected=last_trading_day_on_or_before(history_cutoff(now))
    with closing(setup(root)) as con:
        con.execute('CREATE TABLE IF NOT EXISTS continuous_provenance(symbol TEXT,rank INTEGER,day TEXT,expiry TEXT,provider TEXT,PRIMARY KEY(symbol,rank,day))')
        dated=pd.read_sql_query('SELECT * FROM candles WHERE symbol=? ORDER BY day,expiry',con,params=(symbol,))
        existing=set(con.execute('SELECT rank,day FROM continuous WHERE symbol=?',(symbol,)).fetchall())
        inserts=[];provenance=[]
        for day,group in dated.groupby('day'):
            if day>str(expected):continue
            active=group[group.expiry>=day].sort_values('expiry').head(3)
            if len(active)!=3:continue
            for rank,row in enumerate(active.itertuples(),1):
                if (rank,day) in existing:continue
                inserts.append((symbol,rank,day,row.close,row.volume,row.oi,0,row.expiry,now.isoformat()))
                provenance.append((symbol,rank,day,row.expiry,'Upstox dated daily close'))
        with con:
            con.executemany('INSERT INTO continuous VALUES (?,?,?,?,?,?,?,?,?)',inserts)
            con.executemany('INSERT OR REPLACE INTO continuous_provenance VALUES (?,?,?,?,?)',provenance)
        tails=dict(con.execute('SELECT rank,MAX(day) FROM continuous WHERE symbol=? GROUP BY rank',(symbol,)))
    pending=any(tails.get(r,'')<str(expected) for r in (1,2,3))
    previous=last_trading_day_on_or_before(expected-timedelta(days=1))
    if pending and not (expected==now.date() and all(tails.get(r,'')>=str(previous) for r in (1,2,3))):
        raise MarketDataError('Upstox daily futures not complete through '+str(expected)+'; prior history retained.')
    frame=rolling_frame(root,symbol)
    manifest={'version':VERSION,'symbol':symbol,'status':'verified','requested_end':str(expected),'fetched':now.isoformat(),'provider':'Upstox + retained historical data','coverage':{label:{'sessions':len(f),'first':str(f.Date.min().date()),'last':str(f.Date.max().date())} for label,f in frame.groupby('Pair')},'note':'New observations use exact dated Upstox contracts; existing historical observations retained.'}
    if pending:manifest['pending_daily_candle']=str(expected)
    atomic_json(root/f'{symbol}_rolling_audit.json',manifest)
    return manifest
