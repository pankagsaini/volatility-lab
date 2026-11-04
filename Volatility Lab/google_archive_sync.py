"""Private Drive mirror -> durable local archive -> existing lab stores.
No network credentials; Google Drive desktop supplies the authenticated filesystem.
"""
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import gzip,hashlib,json,os,sqlite3,threading,time
import pandas as pd
import numpy as np
from collector_support import atomic_json,read_json,data_lock,STATE
from market_calendar import HOLIDAYS
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
STORE=DATA/'google_archive'
CONFIG=ROOT/'cloud'/'apps_script'/'sync_config.json'
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='drive-import')
_guard=threading.Lock()
_future=None
_started=0

def source_folder():
    configured=read_json(CONFIG).get('source_folder')
    candidates=[Path(configured)] if configured else []
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        candidates.append(Path(letter+':/')/'My Drive'/'Volatility Lab - private Upstox archive')
    for p in candidates:
        if p.is_dir() and p.resolve()!=STORE.resolve():return p
    return None

def validated(raw):
    if len(raw)>20_000_000:raise ValueError('Oversized archive')
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:body=f.read(40_000_001)
    if len(body)>40_000_000:raise ValueError('Oversized payload')
    obj=json.loads(body)
    if obj.get('schema')!=1 or obj.get('provider')!='Upstox' or obj.get('mode') not in ('snapshot','eod','test'):raise ValueError('Unknown capture format')
    stamp=pd.Timestamp(obj['retrieved_at'])
    if stamp.tzinfo is None:raise ValueError('Undated capture')
    if not isinstance(obj.get('records'),list):raise ValueError('Missing records')
    return obj

def import_report(obj,rate,dividend,spread):
    from upstox_adapter import provider_timestamp
    from upstox_data import INDICES
    from expiry_data import calculation_key,save_observation,load_snapshot,write_snapshot,SCHEMA
    from history_policy import register
    from broker_options import chain_from_response
    from market_table import atm_values,aligned_spread
    from option_math import reversal_values
    from snapshot_history import save as save_intraday
    from smile import smile_points,save_smile
    stamp=pd.Timestamp(obj['retrieved_at']).tz_convert('Asia/Kolkata')
    config=calculation_key(rate,dividend,spread)
    register(DATA/'monitor.sqlite',rate,dividend,spread)
    cat=obj.get('catalogue',{}).get('indices',{})
    records={r['label']:r for r in obj['records']}
    quotes={q.get('instrument_token',k):q for k,q in records.get('quotes',{}).get('data',{}).items()}
    rows=[];catalog={};details=[]
    for symbol,entry in cat.items():
        if symbol not in INDICES:continue
        monthly={f['expiry'] for f in entry['futures']}
        catalog[symbol]=[{'date':e,'kind':'Monthly' if e in monthly else 'Weekly'} for e in entry['option_expiries']]
        q=quotes.get(INDICES[symbol],{})
        valuation=provider_timestamp(q.get('timestamp')) or provider_timestamp(q.get('last_trade_time'))
        trade=provider_timestamp(q.get('last_trade_time'))
        if stamp.date() in HOLIDAYS or stamp.weekday()>4 or not 555<=stamp.hour*60+stamp.minute<=940:
            valuation=trade
        if valuation and (stamp-pd.Timestamp(valuation)).total_seconds()>7*86400:valuation=None
        # A retrieval on a holiday must never create a fictional trading session.
        if not valuation:continue
        asof=pd.Timestamp(valuation)
        try:frame=pd.read_csv(DATA/f'{symbol.lower()}_one_year.csv')
        except (OSError,ValueError):frame=None
        base_alignment=aligned_spread(0.,asof,'Upstox supplied IV',frame)
        for e in entry['option_expiries']:
            rec=records.get(symbol+'|'+e)
            if not rec or not rec['data']:continue
            raw=rec['data'];spots={float(r['underlying_spot_price']) for r in raw if r.get('underlying_spot_price') is not None}
            if len(spots)!=1:continue
            spot=spots.pop()
            if not np.isfinite(spot) or spot<=0:continue
            chain=chain_from_response(raw,e)
            chain.attrs.clear()  # Raw broker payload is archived; avoid copying its audit on every pandas slice.
            strikes=sorted({float(r['strike_price']) for r in raw})
            call,put,iv=atm_values(chain,strikes,spot)
            align={**base_alignment,'IV-RV Spread':iv-base_alignment['HV30']}
            prev=float(q.get('last_price',0))-float(q.get('net_change',0))
            row={'Provider':'Upstox','Instrument':symbol,'Expiry':e,'Type':'Monthly' if e in monthly else 'Weekly','Price':spot,'% change':100*(spot/prev-1) if prev>0 else None,'Call IV':call,'Put IV':put,'IV':iv,**reversal_values(chain,symbol),**align,'As of':asof.isoformat(),'Retrieved at':rec['retrieved_at'],'Source':'Upstox supplied IV','Capture source':'Google Drive','Timestamp note':'Underlying feed timestamp; broker Greeks have no separate exchange timestamp','IV method':'Lab arithmetic mean of broker ATM call and put IV','Greeks source':'Upstox supplied Greeks'}
            # A repeated feed timestamp cannot silently replace the smile while the
            # intraday table retains its first observation. Preserve one coherent row.
            with closing(sqlite3.connect(DATA/'monitor.sqlite',timeout=20)) as existing_con:
                exists=existing_con.execute("SELECT 1 FROM sqlite_master WHERE name='intraday_observations'").fetchone()
                saved=existing_con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=?',(symbol,e,config,row['As of'])).fetchone() if exists else None
            if saved:
                previous=json.loads(saved[0])
                if (previous.get('Capture source')=='Google Drive' and previous.get('IV') is not None
                        and np.isfinite(previous['IV']) and previous['IV']>0):
                    from expiry_data import db
                    with closing(db()) as con:
                        prior=con.execute('SELECT MAX(as_of) FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND substr(as_of,1,10)=?',(symbol,e,config,str(asof.date()))).fetchone()[0]
                    if prior==row['As of']:
                        save_observation(symbol,e,config,asof.date(),previous['IV'],
                            previous.get('IV-RV Spread') if previous.get('IV-RV Spread') is not None else np.nan,
                            'Upstox supplied IV (Google Cloud)')
                        # Repair a missing smile when an already-saved Cloud
                        # observation is encountered again. This only consumes
                        # the broker chain in the validated Drive archive.
                        save_smile(DATA/'monitor.sqlite',symbol,e,config,row['As of'],
                                   smile_points(chain,strikes,spot),capture_source='Google Drive')
                    rows.append(previous)
                    details.append({'Instrument':symbol,'Expiry':e,'Status':'Existing timestamp retained','As of':previous['As of']})
                    continue
            save_intraday(DATA/'monitor.sqlite',config,row)
            save_smile(DATA/'monitor.sqlite',symbol,e,config,row['As of'],smile_points(chain,strikes,spot),capture_source='Google Drive')
            # Save a daily history point only when this is the latest Cloud capture.
            from expiry_data import db
            with closing(db()) as con:
                prior=con.execute('SELECT MAX(as_of) FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND substr(as_of,1,10)=?',(symbol,e,config,str(asof.date()))).fetchone()[0]
            if prior==row['As of']:save_observation(symbol,e,config,asof.date(),iv,align['IV-RV Spread'],'Upstox supplied IV (Google Cloud)')
            rows.append(row);details.append({'Instrument':symbol,'Expiry':e,'Status':'Cloud capture','As of':row['As of'],**align})
    if rows:
        old=load_snapshot(config,allow_saved=True) or {}
        if pd.Timestamp(old.get('timestamp','1970-01-01T00:00:00Z'))<stamp:
            if old.get('config')==config:
                combined={(r['Instrument'],r['Expiry']):r for r in old.get('rows',[])}
                for r in rows:
                    key=(r['Instrument'],r['Expiry']);previous=combined.get(key,{})
                    if not previous.get('As of') or pd.Timestamp(previous['As of'])<=pd.Timestamp(r['As of']):combined[key]=r
                rows=list(combined.values())
                catalog={**old.get('catalog',{}),**catalog}
            cloud_snapshot={'provider':'Upstox','schema':SCHEMA,'config':config,
                'parameters':{'rate':rate,'dividend':dividend,'max_spread':spread},
                'timestamp':stamp.isoformat(),'rows':rows,'details':details,
                'catalog':catalog,'india_vix':old.get('india_vix',{})}
            write_snapshot(cloud_snapshot)
        else:
            cloud_snapshot={'provider':'Upstox','schema':SCHEMA,'config':config,
                'parameters':{'rate':rate,'dividend':dividend,'max_spread':spread},
                'timestamp':stamp.isoformat(),'rows':rows,'details':details,
                'catalog':catalog,'india_vix':old.get('india_vix',{})}
        from relative_value import capture
        capture(DATA,cloud_snapshot)
    import_futures(obj,quotes,stamp)
    return len(rows)

def import_futures(obj,quotes,stamp):
    from futures_data import connection,quote_value,quote_pair
    from upstox_adapter import provider_timestamp
    from upstox_archive import connect as research
    from upstox_data import INDICES
    root=DATA/'futures'
    quote_now=stamp.replace(hour=18,minute=0) if stamp.date() in HOLIDAYS else stamp
    cat=obj.get('catalogue',{}).get('indices',{})
    for symbol,entry in cat.items():
        if symbol not in INDICES:continue
        legs=[]
        for f in entry['futures']:
            q=dict(quotes.get(f['instrument_key'],{}))
            for k in ('timestamp','last_trade_time'):q[k]=provider_timestamp(q.get(k))
            legs.append({'expiry':f['expiry'],'contract':f.get('trading_symbol',f['instrument_key']),'lot_size':None,'quote':quote_value(q,quote_now)})
        if len(legs)==3:
            pairs=[{'Pair':n,**quote_pair(legs[i],legs[i+1])} for i,n in enumerate(['Current - Next','Next - Far'])]
            if any(p['Spread'] is not None for p in pairs):
                result={'provider':'Upstox','symbol':symbol,'collected':stamp.isoformat(),'legs':legs,'pairs':pairs,'history_errors':[],'history_pending':[],'capture_source':'Google Drive'}
                with closing(connection(root)) as con,con:con.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)',(symbol,stamp.isoformat(),json.dumps(result)))
    if obj['mode']!='eod':return
    mapping={f['instrument_key']:(s,f['expiry']) for s,e in cat.items() for f in e['futures'] if s in INDICES}
    with closing(research()) as con,con,closing(connection(root)) as fut,fut:
        for rec in obj['records']:
            key=rec['label']
            if key not in mapping and key not in INDICES.values() and key!='NSE_INDEX|India VIX':continue
            for c in rec['data'].get('candles',[]):
                t=pd.Timestamp(c[0]);vals=[float(v) for v in c[1:6]]
                if t.tzinfo is None or not np.isfinite(vals).all() or min(vals[:4])<=0 or vals[1]<max(vals[:4]) or vals[2]>min(vals[:4]):continue
                t=t.tz_convert('Asia/Kolkata');oi=float(c[6] or 0) if len(c)>6 else None
                con.execute('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?)',(key,'day',t.isoformat(),*vals,oi))
                if key in mapping:
                    symbol,expiry=mapping[key]
                    fut.execute('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?)',(symbol,expiry,str(t.date()),vals[3],vals[4],oi))
    from futures_rolling import setup
    with closing(setup(root)) as con,con:
        for symbol in INDICES:
            dated=pd.read_sql_query('SELECT * FROM candles WHERE symbol=? ORDER BY day,expiry',con,params=(symbol,))
            for day,group in dated.groupby('day'):
                if day>=str(stamp.date()) and stamp.hour<18:continue
                active=group[group.expiry>=day].sort_values('expiry').head(3)
                if len(active)!=3:continue
                for rank,r in enumerate(active.itertuples(),1):
                    con.execute('INSERT OR IGNORE INTO continuous VALUES(?,?,?,?,?,?,?,?,?)',(symbol,rank,day,r.close,r.volume,r.oi,0,r.expiry,stamp.isoformat()))
    # Refresh local index CSVs used by HV and preserve older rows.
    from collector_support import atomic_csv
    with closing(research()) as con:
        for symbol,key in INDICES.items():
            f=pd.read_sql_query("SELECT stamp Date,open Open,high High,low Low,close Close,volume Volume FROM candles WHERE instrument=? AND interval='day' ORDER BY stamp",con,params=(key,))
            if f.empty:continue
            f['Date']=pd.to_datetime(f.Date,utc=True).dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d')
            path=DATA/f'{symbol.lower()}_one_year.csv'
            old=pd.read_csv(path) if path.exists() else pd.DataFrame()
            # Existing file schema governs column names; don't replace unknown formats.
            if not old.empty and set(old.columns)-set(f.columns):continue
            atomic_csv(pd.concat([old,f[list(old.columns)] if not old.empty else f]).drop_duplicates('Date',keep='last').sort_values('Date'),path,index=False)

def reconcile_local_capture_status():
    """Clear stale local warnings only when a complete cloud EOD filled the same futures dates."""
    candidates=sorted(STORE.glob('????-??-??_eod_*_complete.json.gz'),reverse=True)
    if not candidates:return False
    try:obj=validated(candidates[0].read_bytes())
    except (OSError,ValueError,KeyError,TypeError):return False
    if obj.get('mode')!='eod' or obj.get('status')!='complete' or obj.get('errors') or obj.get('pending'):return False
    target=obj.get('target_day',obj.get('day'))
    state=read_json(STATE/'status.json')
    if state.get('status') not in ('partial','failed') or not state.get('attempt','')[:10] or state['attempt'][:10]>str(target):return False
    recoverable={
        'NIFTY: dated history / quotes: Upstox connection failed or returned an invalid response.',
        'BANKNIFTY: dated history / quotes: Upstox connection failed or returned an invalid response.',
        'NIFTY: completed futures candles missing',
        'BANKNIFTY: completed futures candles missing',
        'Upstox connection failed or returned an invalid response.',
        'Upstox connection failed or returned an invalid response after 3 attempts.',
    }
    errors=state.get('errors',[])
    if not errors or any(error not in recoverable for error in errors):return False
    from futures_rolling import setup
    with closing(setup(DATA/'futures')) as con:
        for symbol in ('NIFTY','BANKNIFTY'):
            tails=dict(con.execute('SELECT rank,MAX(day) FROM continuous WHERE symbol=? GROUP BY rank',(symbol,)))
            if any(tails.get(rank,'')<str(target) for rank in (1,2,3)):return False
    state.update(status='recovered',original_status=state['status'],original_errors=errors,errors=[],
                 recovered_through=str(target),recovered_by='Complete Google Drive EOD archive',
                 recovery_file=candidates[0].name,recovered_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat())
    atomic_json(STATE/'status.json',state)
    return True

def _sync_once(rate,dividend,spread,verify_receipt=False):
    STORE.mkdir(parents=True,exist_ok=True)
    source=source_folder()
    if source is None:
        atomic_json(STORE/'status.json',{'status':'waiting','message':'Sign in to Google Drive desktop to connect the private archive.'});return 0
    manifest=read_json(STORE/'manifest.json');count=0;errors=[]
    import re
    pattern=re.compile(r'^\d{4}-\d{2}-\d{2}_(snapshot|test|eod)_\d{6}_(complete|partial)\.json\.gz$')
    # No source files are deleted here. Cloud cleanup consumes verified receipts.
    for path in sorted(source.glob('*.json.gz')):
        if not pattern.fullmatch(path.name):continue
        try:
            info=path.stat();known=manifest.get(path.name)
            if known and known.get('size')==info.st_size and known.get('mtime')==info.st_mtime_ns and (STORE/path.name).is_file() and (STORE/path.name).stat().st_size==known['size']:
                if not verify_receipt or hashlib.sha256((STORE/path.name).read_bytes()).hexdigest()==known['sha256']:continue
            raw=path.read_bytes();obj=validated(raw);digest=hashlib.sha256(raw).hexdigest()
            dest=STORE/path.name
            temp=dest.with_suffix('.tmp');temp.write_bytes(raw);os.replace(temp,dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:raise ValueError('Local verification failed')
            with data_lock():import_report(obj,rate,dividend,spread)
            manifest[path.name]={'sha256':digest,'size':len(raw),'mtime':info.st_mtime_ns,'saved_at':pd.Timestamp.now(tz='UTC').isoformat()}
            atomic_json(STORE/'manifest.json',manifest);count+=1
            atomic_json(STORE/'status.json',{'status':'syncing','total_files':len(manifest),'updated':pd.Timestamp.now(tz='UTC').isoformat()})
        except Exception as exc:errors.append({'file':path.name,'error':type(exc).__name__})
    if read_json(CONFIG).get('cleanup_approved'):
        receipt=read_json(STORE/'receipt_status.json')
        checked=pd.Timestamp(receipt.get('verified_at','1970-01-01T00:00:00Z'))
        if verify_receipt or (pd.Timestamp.now(tz='UTC')-checked).total_seconds()>=7*86400:
            files={}
            for remote in source.glob('*.json.gz'):
                n=remote.name;v=manifest.get(n);local=STORE/n
                if v and local.exists() and hashlib.sha256(local.read_bytes()).hexdigest()==v['sha256']:
                    files[n]={'sha256':v['sha256'],'size':v['size']}
            receipt={'schema':1,'verified_at':pd.Timestamp.now(tz='UTC').isoformat(),'files':files}
            atomic_json(source/'local_verified_receipts.json',receipt)
            atomic_json(STORE/'receipt_status.json',{'verified_at':receipt['verified_at']})
    reconcile_local_capture_status()
    atomic_json(STORE/'status.json',{'status':'partial' if errors else 'ready','updated':pd.Timestamp.now(tz='UTC').isoformat(),'imported':count,'total_files':len(manifest),'errors':errors[:10],'source':str(source)})
    return count

def sync_once(rate,dividend,spread,verify_receipt=False):
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
    try:
        import fcntl
    except ImportError:
        fcntl = None

    STORE.mkdir(parents=True,exist_ok=True)
    with (STORE/'sync.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:
            if msvcrt:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            elif fcntl:
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            return 0
        try:
            return _sync_once(rate,dividend,spread,verify_receipt)
        except Exception as exc:
            atomic_json(STORE/'status.json',{'status':'partial','updated':pd.Timestamp.now(tz='UTC').isoformat(),'message':'Sync interrupted; retry scheduled','error':type(exc).__name__})
            raise
        finally:
            lock.seek(0)
            if msvcrt:
                try: msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                except OSError: pass
            elif fcntl:
                try: fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
                except OSError: pass

def ensure_sync(rate,dividend,spread):
    global _future,_started
    with _guard:
        if _future is None or (_future.done() and time.monotonic()-_started>=180):
            _started=time.monotonic();_future=_pool.submit(sync_once,rate,dividend,spread)
    return _future


if __name__=='__main__':
    from pricing_inputs import get_rate,get_yields
    import sys
    sync_once(get_rate().get('model_rate'),{s:v.get('model_yield') for s,v in get_yields().items()},3.,verify_receipt='--verify-receipt' in sys.argv)
