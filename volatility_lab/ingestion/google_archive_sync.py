"""Private Drive mirror -> durable local archive -> existing lab stores (Accelerated v4).
No network credentials; Google Drive desktop supplies the authenticated filesystem.
"""
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import gzip,hashlib,json,os,sqlite3,threading,time,zlib
import pandas as pd
import numpy as np
from collector_support import atomic_json,read_json,data_lock,STATE
from market_calendar import HOLIDAYS
ROOT=PACKAGE_ROOT
DATA=ROOT/'data'
STORE=DATA/'google_archive'
CONFIG=ROOT/'cloud'/'apps_script'/'sync_config.json'
STREAM_SETTLE_SECONDS=3.0
LIVE_QUOTE_MAX_AGE_SECONDS=120.0
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='drive-import')
_guard=threading.Lock()
_future=None
_started=0
_ONE_YEAR_CSV_CACHE: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}


def _cached_one_year_csv(symbol: str):
    path = DATA / f'{symbol.lower()}_one_year.csv'
    try:
        st_info = path.stat()
        rev = (st_info.st_mtime_ns, st_info.st_size)
    except OSError:
        return None
    cached = _ONE_YEAR_CSV_CACHE.get(symbol)
    if cached is not None and cached[0] == rev:
        return cached[1]
    try:
        df = pd.read_csv(path)
        _ONE_YEAR_CSV_CACHE[symbol] = (rev, df)
        return df
    except (OSError, ValueError):
        return None


def source_folder():
    configured=(read_json(CONFIG, default={}) or {}).get('source_folder')
    candidates=[Path(configured)] if configured else []
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        candidates.append(Path(letter+':/')/'My Drive'/'Volatility Lab - private Upstox archive')
        candidates.append(Path(letter+':/')/'My Drive'/'Historical Data'/'Volatility Lab - private Upstox archive')
    for p in candidates:
        if p.is_dir() and p.resolve()!=STORE.resolve():return p
    return None

def _aware_timestamp(value, label):
    try:
        stamp=pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'Invalid {label}') from exc
    if stamp.tzinfo is None:
        raise ValueError(f'Undated {label}')
    return stamp.tz_convert('UTC')


def validate_report(obj):
    """Validate a complete cloud report before it can enter research stores."""
    if not isinstance(obj,dict):raise ValueError('Invalid capture payload')
    if obj.get('schema')!=1 or obj.get('provider')!='Upstox' or obj.get('mode') not in ('snapshot','eod'):
        raise ValueError('Unknown capture format')
    if obj.get('status')!='complete' or obj.get('errors') or obj.get('pending'):
        raise ValueError('Incomplete capture is not eligible for import')
    stamp=_aware_timestamp(obj.get('retrieved_at'),'capture timestamp')
    if not isinstance(obj.get('records'),list) or not obj['records']:
        raise ValueError('Missing records')
    labels=set()
    option_labels=0
    for rec in obj['records']:
        if not isinstance(rec,dict) or not isinstance(rec.get('label'),str) or not rec['label']:
            raise ValueError('Invalid capture record')
        if rec['label'] in labels:raise ValueError('Duplicate capture record label')
        labels.add(rec['label'])
        record_stamp=_aware_timestamp(rec.get('retrieved_at'),'record timestamp')
        age=(stamp-record_stamp).total_seconds()
        if age < -30.0 or age > 600.0:raise ValueError('Record timestamp outside capture window')
        data=rec.get('data')
        if rec['label']=='quotes':
            if not isinstance(data,dict) or not all(isinstance(q,dict) for q in data.values()):
                raise ValueError('Invalid quote record')
        elif rec['label'].split('|',1)[0] in {'NIFTY','BANKNIFTY'} and '|' in rec['label']:
            parts=rec['label'].split('|')
            if len(parts)!=2 or not parts[0] or not parts[1] or not isinstance(data,list) or not data:
                raise ValueError('Invalid option-chain record')
            expiry=parts[1]
            for row in data:
                if not isinstance(row,dict) or str(row.get('expiry') or '')[:10]!=expiry:
                    raise ValueError('Option-chain expiry does not match its record label')
                spot=pd.to_numeric(row.get('underlying_spot_price'),errors='coerce')
                if not np.isfinite(spot) or float(spot)<=0:
                    raise ValueError('Option-chain row has no valid underlying spot')
            option_labels+=1
        elif not isinstance(data,(list,dict)):
            raise ValueError('Invalid instrument record')
    if obj.get('mode')=='snapshot':
        if obj.get('nfo_session_validated') is not True:raise ValueError('NFO session was not validated')
        day=str(obj.get('day') or '')[:10]
        if day != stamp.tz_convert('Asia/Kolkata').strftime('%Y-%m-%d'):
            raise ValueError('Snapshot day does not match capture timestamp')
        if 'quotes' not in labels or not option_labels:
            raise ValueError('Snapshot is missing quotes or option-chain evidence')
    return obj


def validated(raw):
    if len(raw)>20_000_000:raise ValueError('Oversized archive')
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:body=f.read(40_000_001)
    if len(body)>40_000_000:raise ValueError('Oversized payload')
    return validate_report(json.loads(body))

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
    validate_report(obj)
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
        if valuation:
            valuation_stamp=pd.Timestamp(valuation)
            if valuation_stamp.tzinfo is None:
                valuation=None
            else:
                age_seconds=(stamp-valuation_stamp.tz_convert('Asia/Kolkata')).total_seconds()
                if obj.get('mode')=='snapshot':
                    if age_seconds < -30.0 or age_seconds>LIVE_QUOTE_MAX_AGE_SECONDS:valuation=None
                elif age_seconds < -30.0 or age_seconds>7*86400:valuation=None
        if not valuation:continue
        asof=pd.Timestamp(valuation)
        frame=_cached_one_year_csv(symbol)
        base_alignment=aligned_spread(0.,asof,'Upstox supplied IV',frame)
        for e in entry['option_expiries']:
            rec=records.get(symbol+'|'+e)
            if not rec or not rec['data']:continue
            raw=rec['data'];spots={float(r['underlying_spot_price']) for r in raw if r.get('underlying_spot_price') is not None}
            if len(spots)!=1:continue
            spot=spots.pop()
            if not np.isfinite(spot) or spot<=0:continue
            chain=chain_from_response(raw,e)
            chain.attrs.clear()
            strikes=sorted({float(r['strike_price']) for r in raw})
            # The archived chain does not contain independently verifiable
            # per-option event timestamps. Never re-solve Black-76 IV from an
            # LTP in this path; preserve the broker's stated Greeks with a
            # provenance label instead.
            call,put,iv=atm_values(chain,strikes,spot,use_black76=False)
            align={**base_alignment,'IV-RV Spread':iv-base_alignment['HV30'] if np.isfinite(iv) and np.isfinite(base_alignment['HV30']) else np.nan}
            prev=float(q.get('last_price',0))-float(q.get('net_change',0))
            row={'Provider':'Upstox','Instrument':symbol,'Expiry':e,'Type':'Monthly' if e in monthly else 'Weekly','Price':spot,'% change':100*(spot/prev-1) if prev>0 else None,'Call IV':call,'Put IV':put,'IV':iv,**reversal_values(chain,symbol),**align,'As of':asof.isoformat(),'Retrieved at':rec['retrieved_at'],'Source':'Upstox supplied Greeks','Timestamp note':'Underlying quote timestamp verified against capture; broker Greeks have no per-option event timestamp','IV method':'Arithmetic mean of broker-supplied ATM call and put IV; not independently re-solved from LTP','Capture source':'Google Drive','Greeks source':'Upstox supplied Greeks','Research status':'Broker option IV is usable for hypothesis generation only; per-option event-time validation is unavailable'}
            with closing(sqlite3.connect(DATA/'monitor.sqlite',timeout=30)) as existing_con:
                existing_con.execute('PRAGMA journal_mode=WAL')
                existing_con.execute('PRAGMA busy_timeout=30000')
                exists=existing_con.execute("SELECT 1 FROM sqlite_master WHERE name='intraday_observations'").fetchone()
                saved=existing_con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=?', (symbol,e,config,row['As of'])).fetchone() if exists else None
                if saved:
                    previous=json.loads(saved[0])
                    if (previous.get('Capture source')=='Google Drive' and previous.get('IV') is not None and np.isfinite(previous['IV']) and previous['IV']>0):
                        from expiry_data import db
                        with closing(db()) as con:
                            prior=con.execute('SELECT MAX(as_of) FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND substr(as_of,1,10)=?', (symbol,e,config,str(asof.date()))).fetchone()[0]
                            if prior==row['As of']:
                                save_observation(symbol,e,config,asof.date(),previous['IV'], previous.get('IV-RV Spread') if previous.get('IV-RV Spread') is not None else np.nan, 'Upstox supplied IV (Google Cloud)')
                                save_smile(DATA/'monitor.sqlite',symbol,e,config,row['As of'], smile_points(chain,strikes,spot),capture_source='Google Drive')
                        rows.append(previous)
                        details.append({'Instrument':symbol,'Expiry':e,'Status':'Existing timestamp retained','As of':previous['As of']})
                        continue
            save_intraday(DATA/'monitor.sqlite',config,row)
            save_smile(DATA/'monitor.sqlite',symbol,e,config,row['As of'],smile_points(chain,strikes,spot),capture_source='Google Drive')
            try:
                from trade_review import archive_quotes
                archive_quotes(DATA, symbol, e, {'provider': 'Upstox', 'valuation': asof.isoformat(), 'spot': spot, 'broker_option_chain': raw}, config)
            except Exception:
                pass
            from expiry_data import db
            with closing(db()) as con:
                prior=con.execute('SELECT MAX(as_of) FROM intraday_observations WHERE symbol=? AND expiry=? AND config=? AND substr(as_of,1,10)=?', (symbol,e,config,str(asof.date()))).fetchone()[0]
                if prior==row['As of']:save_observation(symbol,e,config,asof.date(),iv,align['IV-RV Spread'],'Upstox supplied IV (Google Cloud)')
            rows.append(row);details.append({'Instrument':symbol,'Expiry':e,'Status':'Cloud capture','As of':row['As of'],**align})
    if rows:
        old=load_snapshot(config,allow_saved=True) or {}
        if pd.Timestamp(old.get('timestamp','1970-01-01T00:00:00Z'))<stamp:
            combined={(r['Instrument'],r['Expiry']):r for r in old.get('rows',[])}
            for r in rows:
                key=(r['Instrument'],r['Expiry']);previous=combined.get(key,{})
                if not previous.get('As of') or pd.Timestamp(previous['As of'])<=pd.Timestamp(r['As of']):combined[key]=r
            rows=list(combined.values())
            catalog={**old.get('catalog',{}),**catalog}
            cloud_snapshot={'provider':'Upstox','schema':SCHEMA,'config':config, 'parameters':{'rate':rate,'dividend':dividend,'max_spread':spread}, 'timestamp':stamp.isoformat(),'rows':rows,'details':details, 'catalog':catalog,'india_vix':old.get('india_vix',{})}
            write_snapshot(cloud_snapshot)
        else:
            cloud_snapshot={'provider':'Upstox','schema':SCHEMA,'config':config, 'parameters':{'rate':rate,'dividend':dividend,'max_spread':spread}, 'timestamp':stamp.isoformat(),'rows':rows,'details':details, 'catalog':catalog,'india_vix':old.get('india_vix',{})}
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
                with closing(connection(root)) as con,con:con.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)', (symbol,stamp.isoformat(),json.dumps(result)))
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
                con.execute('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)',(key,'day',t.isoformat(),*vals,oi))
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
                    con.execute('INSERT OR IGNORE INTO continuous VALUES(?,?,?,?,?,?,?,?)', (symbol,rank,day,r.close,r.volume,r.oi,0,r.expiry,stamp.isoformat()))
    from collector_support import atomic_csv
    with closing(research()) as con:
        for symbol,key in INDICES.items():
            f=pd.read_sql_query("SELECT stamp Date,open Open,high High,low Low,close Close,volume Volume FROM candles WHERE instrument=? AND interval='day' ORDER BY stamp",con,params=(key,))
            if f.empty:continue
            f['Date']=pd.to_datetime(f.Date,utc=True).dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d')
            path=DATA/f'{symbol.lower()}_one_year.csv'
            old=pd.read_csv(path) if path.exists() else pd.DataFrame()
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
    state.update(status='recovered',original_status=state['status'],original_errors=errors,errors=[], recovered_through=str(target),recovered_by='Complete Google Drive EOD archive', recovery_file=candidates[0].name,recovered_at=pd.Timestamp.now(tz='Asia/Kolkata').isoformat())
    atomic_json(STATE/'status.json',state)
    return True

def _sync_once(rate,dividend,spread,verify_receipt=False):
    STORE.mkdir(parents=True,exist_ok=True)
    source=source_folder()
    if source is None:
        web_url = (read_json(CONFIG, default={}) or {}).get('web_app_url') or 'https://script.google.com/macros/s/AKfycbxDbTwpiRAE9ZRoGGWTyTncZhwUPmTnimv0HyejOiJilfS7lPnxZsF4mj701Fd4jdz5qQ/exec'
        try:
            import requests
            resp = requests.get(web_url, timeout=12, allow_redirects=True)
            if resp.status_code == 200:
                try:
                    obj = validate_report(resp.json())
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    atomic_json(STORE/'status.json', {
                        'status': 'partial', 'mode': 'web_cloud',
                        'message': 'Cloud capture rejected by integrity validation.',
                        'error': type(exc).__name__, 'source': web_url,
                    })
                    return 0
                retrieved_at = str(obj.get('retrieved_at') or '')
                prev_status = read_json(STORE/'status.json', default={}) or {}
                if prev_status.get('mode') == 'web_cloud' and prev_status.get('updated') == retrieved_at and retrieved_at:
                    return 0
                with data_lock(timeout=120):
                    import_report(obj, rate, dividend, spread)
                atomic_json(STORE/'status.json',{'status':'ready','mode':'web_cloud','updated':retrieved_at or pd.Timestamp.now(tz='UTC').isoformat(),'total_files':int(prev_status.get('total_files', 0)) + 1,'source':web_url})
                return 1
        except Exception as exc:
            pass
        atomic_json(STORE/'status.json',{'status':'waiting','message':'Sign in to Google Drive desktop to connect the private archive.'});return 0
    manifest=read_json(STORE/'manifest.json', default={}) or {};count=0;errors=[]
    import re
    pattern=re.compile(r'^\d{4}\-\d{2}\-\d{2}_(snapshot|eod)_\d{6}_complete\.json\.gz$')
    for path in sorted(source.glob('*.json.gz'), reverse=True):
        if not pattern.fullmatch(path.name):continue
        try:
            info=path.stat()
            if (time.time() - info.st_mtime) < STREAM_SETTLE_SECONDS:
                continue
            known=manifest.get(path.name)
            if known and known.get('size')==info.st_size and known.get('mtime')==info.st_mtime_ns and (STORE/path.name).is_file() and (STORE/path.name).stat().st_size==known['size']:
                if not verify_receipt or hashlib.sha256((STORE/path.name).read_bytes()).hexdigest()==known['sha256']:continue
            raw=path.read_bytes();obj=validated(raw);digest=hashlib.sha256(raw).hexdigest()
            dest=STORE/path.name
            temp=dest.with_suffix('.tmp');temp.write_bytes(raw);os.replace(temp,dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=digest:raise ValueError('Local verification failed')
            with data_lock(timeout=120):import_report(obj,rate,dividend,spread)
            manifest[path.name]={'sha256':digest,'size':len(raw),'mtime':info.st_mtime_ns,'saved_at':pd.Timestamp.now(tz='UTC').isoformat()}
            atomic_json(STORE/'manifest.json',manifest);count+=1
            atomic_json(STORE/'status.json',{'status':'syncing','total_files':len(manifest),'updated':pd.Timestamp.now(tz='UTC').isoformat()})
        except (EOFError, gzip.BadGzipFile, zlib.error, json.JSONDecodeError):
            # Transient Google Drive stream still flushing; retry on next cycle without error penalty
            continue
        except Exception as exc:errors.append({'file':path.name,'error':type(exc).__name__})
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
    with os.fdopen(os.open(str(STORE/'sync.lock'), os.O_CREAT | os.O_RDWR, 0o666), 'r+b') as lock:
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
        if _future is None or (_future.done() and time.monotonic()-_started>=45):
            _started=time.monotonic();_future=_pool.submit(sync_once,rate,dividend,spread)
    return _future
