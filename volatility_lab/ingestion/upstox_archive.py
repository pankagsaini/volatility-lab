"""Resumable Upstox research archive; raw candles never masquerade as historical IV."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
from datetime import datetime,timedelta
import json,sqlite3
import pandas as pd
from collector_support import atomic_json,read_json
from market_data import IST,history_cutoff
from upstox_data import saved_client,INDICES,UpstoxDataError
from upstox_adapter import market_client
from upstox_rate_limit import wait_for_slot
from market_calendar import last_trading_day_on_or_before

ROOT=runtime_data_dir()/'upstox'
STATUS=ROOT/'collection_status.json'

def connect():
    ROOT.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(ROOT/'research.sqlite',timeout=30)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS candles(instrument TEXT,interval TEXT,stamp TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,oi REAL,PRIMARY KEY(instrument,interval,stamp))')
    con.execute('CREATE TABLE IF NOT EXISTS contracts(instrument TEXT PRIMARY KEY,symbol TEXT,kind TEXT,expiry TEXT,strike REAL,payload TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS downloads(instrument TEXT,interval TEXT,start TEXT,end TEXT,rows INTEGER,fetched TEXT,PRIMARY KEY(instrument,interval,start,end))')
    return con

def record_contracts(rows,symbol):
    with connect() as con:
        for r in rows:
            expiry=r.get('expiry')
            if isinstance(expiry,(int,float)):expiry=str(pd.to_datetime(expiry,unit='ms',utc=True).tz_convert(IST).date())
            con.execute('INSERT OR REPLACE INTO contracts VALUES (?,?,?,?,?,?)',(r['instrument_key'],symbol,r['instrument_type'],expiry,r.get('strike_price',0),json.dumps(r)))

class Collector:
    def __init__(self):self.client=saved_client();self.downloads=0;self.errors=[]
    def call(self,path,params=None):
        # Pace actual HTTP attempts, including retries, across archive workers.
        return self.client._get(path,params,before_attempt=wait_for_slot)
    def candles(self,key,interval,start,end,expired=False):
        start=pd.Timestamp(start).date();end=pd.Timestamp(end).date()
        chunk=28 if interval=='1minute' else 299
        while start<=end:
            stop=min(start+timedelta(days=chunk),end)
            with connect() as con:
                done=con.execute('SELECT rows FROM downloads WHERE instrument=? AND interval=? AND start=? AND end=?',(key,interval,str(start),str(stop))).fetchone()
            if done is not None:start=stop+timedelta(days=1);continue
            from urllib.parse import quote
            prefix='/v2/expired-instruments/historical-candle/' if expired else '/v3/historical-candle/'
            unit=interval if expired else 'minutes/1' if interval=='1minute' else 'days/1'
            data=self.call(prefix+quote(key,safe='')+'/'+unit+'/'+str(stop)+'/'+str(start))
            rows=[]
            for c in data.get('candles',[]):
                stamp=pd.Timestamp(c[0])
                if stamp.tzinfo is None:raise ValueError('Timestamp without timezone')
                stamp=stamp.tz_convert(IST)
                vals=[float(x) for x in c[1:6]]
                import math
                if not all(math.isfinite(x) for x in vals) or min(vals[:4])<=0 or vals[1]<max(vals[0],vals[2],vals[3]) or vals[2]>min(vals[0],vals[1],vals[3]):raise ValueError('Invalid OHLC')
                if start<=stamp.date()<=stop:rows.append((key,interval,stamp.isoformat(),*vals,float(c[6]) if len(c)>6 and c[6] is not None else None))
            with connect() as con:
                con.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)',rows)
                con.execute('INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?)',(key,interval,str(start),str(stop),len(rows),datetime.now(IST).isoformat()))
            self.downloads+=1;start=stop+timedelta(days=1)
    def status(self,stage):atomic_json(STATUS,{'status':'running','stage':stage,'downloads':self.downloads,'updated':datetime.now(IST).isoformat(),'errors':self.errors[-10:]})

    def run(self):
        end=history_cutoff();start=end-timedelta(days=365);self.status('Index daily and one-minute history')
        adapter=market_client();master=adapter.master()
        for symbol,key in INDICES.items():
            self.candles(key,'day',end-timedelta(days=1100),end)
            self.candles(key,'1minute',end-timedelta(days=730),end)
            contracts=self.client.contracts(key)
            record_contracts(contracts,symbol)
            for expiry in sorted(set(r['expiry'] for r in contracts)):
                if expiry<=str(end+timedelta(days=100)):
                    atomic_json(ROOT/'chains'/f'{symbol}_{expiry}_{end}.json',{'provider':'Upstox','retrieved_at':datetime.now(IST).isoformat(),'data':self.call('/v2/option/chain',{'instrument_key':key,'expiry_date':expiry})})
            listed=read_json(ROOT/'master.json')['instruments'];futures=[r for r in listed if r.get('underlying_key')==key and r['instrument_type']=='FUT'];record_contracts(futures,symbol)
            self.status(symbol+' expired-contract catalogue')
            expiries=self.call('/v2/expired-instruments/expiries',{'instrument_key':key})
            atomic_json(ROOT/f'{symbol}_expiries.json',expiries)
            for expiry in expiries:
                if expiry<str(end-timedelta(days=1100)):continue
                future_path=ROOT/'catalogues'/f'{symbol}_{expiry}_futures.json'
                if future_path.exists():rows=read_json(future_path,[])
                else:
                    rows=self.call('/v2/expired-instruments/future/contract',{'instrument_key':key,'expiry_date':expiry});atomic_json(future_path,rows)
                if rows:record_contracts(rows,symbol);futures.extend(rows)
                if expiry>=str(start):
                    path=ROOT/'catalogues'/f'{symbol}_{expiry}.json';saved=read_json(path)
                    if not saved:
                        saved=self.call('/v2/expired-instruments/option/contract',{'instrument_key':key,'expiry_date':expiry});atomic_json(path,saved)
                    record_contracts(saved,symbol)
            self.status(symbol+' dated futures daily history')
            for r in futures:
                exp=r['expiry'];exp=pd.to_datetime(exp,unit='ms',utc=True).tz_convert(IST).date() if isinstance(exp,(int,float)) else pd.Timestamp(exp).date()
                expired=exp<datetime.now(IST).date()
                self.candles(r['instrument_key'],'day',max(end-timedelta(days=1100),exp-timedelta(days=120)),min(end,exp),expired)
                if exp>=end-timedelta(days=93):self.candles(r['instrument_key'],'1minute',max(end-timedelta(days=93),exp-timedelta(days=100)),min(end,exp),expired)
            self.status(symbol+' historical ATM option candles')
            with connect() as con:
                spots=pd.read_sql_query("SELECT stamp,close FROM candles WHERE instrument=? AND interval='day' ORDER BY stamp",con,params=(key,))
                opts=pd.read_sql_query("SELECT instrument,expiry,strike,kind FROM contracts WHERE symbol=? AND kind IN ('CE','PE') ORDER BY expiry",con,params=(symbol,))
            spots['day']=pd.to_datetime(spots.stamp).dt.date
            options=opts[(opts.expiry>=str(start))&(opts.expiry<=str(end+timedelta(days=100)))].copy()
            options=options[(options.expiry>=str(datetime.now(IST).date())) | (options.instrument.str.count(r'\|')==2)]
            selected={}
            for r in spots[spots.day>=start].itertuples():
                candidates=options[options.expiry>str(r.day)]
                if candidates.empty:continue
                expiry=candidates.expiry.min();chain=candidates[candidates.expiry==expiry]
                strikes=sorted(chain.strike.unique(),key=lambda k:abs(k-r.close))[:2]
                for item in chain[chain.strike.isin(strikes)].itertuples():
                    bounds=selected.setdefault(item.instrument,[r.day,r.day,item.expiry]);bounds[1]=r.day
            for i,(key2,(first,last,expiry)) in enumerate(selected.items()):
                self.candles(key2,'day',first,last, pd.Timestamp(expiry).date()<datetime.now(IST).date())
                if i%10==0:self.status(symbol+f' historical ATM candles {i+1}/{len(selected)}')
        with connect() as con:
            coverage=[dict(zip(('interval','instruments','rows','first','last'),r)) for r in con.execute('SELECT interval,COUNT(DISTINCT instrument),COUNT(*),MIN(stamp),MAX(stamp) FROM candles GROUP BY interval')]
        atomic_json(STATUS,{'status':'complete','updated':datetime.now(IST).isoformat(),'downloads':self.downloads,'coverage':coverage,'note':'Raw OHLC/OI archive. Historical option candles are ATM research samples, not historical bid/ask or IV.'})

if __name__=='__main__':
    if read_json(STATUS).get('status')=='complete':raise SystemExit(0)
    collector=Collector()
    try:collector.run()
    except Exception as exc:
        atomic_json(STATUS,{'status':'partial','updated':datetime.now(IST).isoformat(),'downloads':collector.downloads,'error':str(exc) if isinstance(exc,UpstoxDataError) else type(exc).__name__})
        raise SystemExit(1)

def incremental_capture():
    """Small EOD update; never repeats the initial historical backfill."""
    c=Collector();end=history_cutoff();now=datetime.now(IST)
    adapter=market_client();adapter.master();rows=read_json(ROOT/'master.json')['instruments']
    for symbol,key in INDICES.items():
        for interval,days in [('day',1100),('1minute',365)]:
            with connect() as con:last=con.execute('SELECT MAX(stamp) FROM candles WHERE instrument=? AND interval=?',(key,interval)).fetchone()[0]
            start=pd.Timestamp(last).date()-timedelta(days=2) if last else end-timedelta(days=days)
            # Tail requests are rechecked so upstream corrections can be ingested.
            with connect() as con:con.execute('DELETE FROM downloads WHERE instrument=? AND interval=? AND start>=?',(key,interval,str(start)))
            c.candles(key,interval,start,end)
        active=[r for r in rows if r.get('underlying_key')==key]
        record_contracts(active,symbol)
        for r in active:
            if r['instrument_type']!='FUT':continue
            key2=r['instrument_key']
            for interval in ('day','1minute'):
                with connect() as con:last=con.execute('SELECT MAX(stamp) FROM candles WHERE instrument=? AND interval=?',(key2,interval)).fetchone()[0]
                start=pd.Timestamp(last).date()-timedelta(days=2) if last else end-timedelta(days=100 if interval=='day' else 93)
                with connect() as con:con.execute('DELETE FROM downloads WHERE instrument=? AND interval=? AND start>=?',(key2,interval,str(start)))
                c.candles(key2,interval,start,end)
        contracts=c.client.contracts(key)
        record_contracts(contracts,symbol)
        recent_expiries=c.call('/v2/expired-instruments/expiries',{'instrument_key':key})
        for expiry in recent_expiries:
            if str(end-timedelta(days=7))<=expiry<=str(end):
                path=ROOT/'catalogues'/f'{symbol}_{expiry}.json'
                expired_options=read_json(path)
                if not expired_options:
                    expired_options=c.call('/v2/expired-instruments/option/contract',{'instrument_key':key,'expiry_date':expiry});atomic_json(path,expired_options)
                record_contracts(expired_options,symbol)
        with connect() as con:
            spots=pd.read_sql_query("SELECT stamp,close FROM candles WHERE instrument=? AND interval='day' AND stamp>=? ORDER BY stamp",con,params=(key,str(end-timedelta(days=3))))
            opts=pd.read_sql_query("SELECT instrument,expiry,strike FROM contracts WHERE symbol=? AND kind IN ('CE','PE')",con,params=(symbol,))
        opts=opts[(opts.expiry>=str(now.date())) | (opts.instrument.str.count(r'\|')==2)]
        for spot in spots.itertuples():
            day=pd.Timestamp(spot.stamp).date();eligible=opts[opts.expiry>str(day)]
            if eligible.empty:continue
            chain=eligible[eligible.expiry==eligible.expiry.min()]
            strikes=sorted(chain.strike.unique(),key=lambda k:abs(k-spot.close))[:2]
            for item in chain[chain.strike.isin(strikes)].itertuples():
                c.candles(item.instrument,'day',day,day,pd.Timestamp(item.expiry).date()<now.date())
        for expiry in sorted(set(r['expiry'] for r in contracts)):
            if expiry<=str(end+timedelta(days=100)):
                atomic_json(ROOT/'chains'/f'{symbol}_{expiry}_{now.strftime("%Y%m%dT%H%M%S")}.json',{'provider':'Upstox','retrieved_at':now.isoformat(),'data':c.call('/v2/option/chain',{'instrument_key':key,'expiry_date':expiry})})
    expected=last_trading_day_on_or_before(end)
    missing=[]
    with connect() as con:
        for symbol,key in INDICES.items():
            for interval in ('day','1minute'):
                tail=con.execute('SELECT MAX(stamp) FROM candles WHERE instrument=? AND interval=?',(key,interval)).fetchone()[0]
                if not tail or str(tail)[:10]<str(expected):missing.append(symbol+' '+interval+' through '+str(tail)[:10])
    atomic_json(ROOT/'incremental_status.json',{'status':'partial' if missing else 'complete','through':str(expected),'updated':now.isoformat(),'downloads':c.downloads,'missing':missing})
    if missing:raise UpstoxDataError('Upstox history publication pending: '+ '; '.join(missing))
