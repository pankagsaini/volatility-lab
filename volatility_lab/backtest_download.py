"""Resumable raw backtest archive. No bid/ask or IV inferred from candles."""
from pathlib import Path
from datetime import date,timedelta,datetime
from urllib.parse import quote
import gzip,json,os,sqlite3,hashlib,time,shutil
try:
    import msvcrt
except ImportError:
    msvcrt = None
from concurrent.futures import ThreadPoolExecutor
import threading
try:
    from upstox_archive import Collector,INDICES
except ImportError:
    Collector = object
    INDICES = {'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank'}
from collector_support import atomic_json as _atomic_json
ROOT=Path(__file__).resolve().parent/'data'/'backtest_1minute'
ROOT.mkdir(parents=True,exist_ok=True)
STATUS=ROOT/'status.json'
_thread=threading.local()

def fetch_job(job):
 if not hasattr(_thread,'client'):_thread.client=Collector()
 return _thread.client.call(job[1],json.loads(job[2]))

def retry_file_write(operation):
 # Windows readers/antivirus can briefly deny replacement of an open file.
 for attempt in range(8):
  try:return operation()
  except PermissionError:
   if attempt==7:raise
   time.sleep(min(.1*2**attempt,2.))

def atomic_json(path,value):
 return retry_file_write(lambda:_atomic_json(path,value))


def db():
 c=sqlite3.connect(ROOT/'queue.sqlite',timeout=30)
 c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA synchronous=NORMAL')
 c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,path TEXT,params TEXT,kind TEXT,meta TEXT,state TEXT DEFAULT "pending",rows INTEGER,file TEXT,attempts INTEGER DEFAULT 0,error TEXT)')
 c.execute("CREATE INDEX IF NOT EXISTS jobs_state_kind ON jobs(state,kind)")
 c.execute("CREATE INDEX IF NOT EXISTS jobs_key_kind ON jobs(json_extract(meta,'$.key'),kind)")
 return c

def enqueue(c,path,params,kind,meta):
 ident=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
 c.execute('INSERT OR IGNORE INTO jobs(id,path,params,kind,meta) VALUES(?,?,?,?,?)',(ident,path,json.dumps(params),kind,json.dumps(meta)))

def candle_jobs(c,key,start,end,expired,interval,meta):
 # Expired daily endpoint accepts the complete requested period. V3 daily allows up to a decade.
 step=28 if interval=='1minute' else 3600
 if interval=='1minute':
  covered=[]
  for text, in c.execute("SELECT meta FROM jobs WHERE json_extract(meta,'$.key')=? AND kind='minute' AND state IN ('done','pending','failed')",(key,)):
   old=json.loads(text);covered.append((date.fromisoformat(old['start']),date.fromisoformat(old['end'])))
  spans=[(start,end)]
  for a,b in sorted(covered):
   spans=[piece for left,right in spans for piece in ([(left,right)] if b<left or a>right else ([(left,a-timedelta(days=1))] if left<a else [])+([(b+timedelta(days=1),right)] if b<right else []))]
  for left,right in spans:_enqueue_candles(c,key,left,right,expired,interval,meta,step)
  return
 _enqueue_candles(c,key,start,end,expired,interval,meta,step)

def _enqueue_candles(c,key,start,end,expired,interval,meta,step):
 while start<=end:
  stop=min(start+timedelta(days=step),end)
  unit=interval if expired else 'minutes/1' if interval=='1minute' else 'days/1'
  path=('/v2/expired-instruments/historical-candle/' if expired else '/v3/historical-candle/')+quote(key,safe='')+'/'+unit+'/'+str(stop)+'/'+str(start)
  enqueue(c,path,{},'minute' if interval=='1minute' else 'discovery',dict(meta,key=key,expired=expired,start=str(start),end=str(stop)))
  start=stop+timedelta(days=1)

def optimize_queue(c):
 marker=ROOT/'queue_optimization_v3.json'
 if marker.exists():return
 before=c.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
 grouped={}
 for ident,text in c.execute("SELECT id,meta FROM jobs WHERE kind='discovery' AND state='pending'").fetchall():
  m=json.loads(text)
  key=m['key']
  if key not in grouped:grouped[key]=[[],m,m['start'],m['end']]
  item=grouped[key];item[0].append(ident);item[2]=min(item[2],m['start']);item[3]=max(item[3],m['end'])
 with c:
  for ids,m,start,end in grouped.values():
   c.executemany("UPDATE jobs SET state='superseded' WHERE id=?",[(i,) for i in ids])
   candle_jobs(c,m['key'],date.fromisoformat(start),date.fromisoformat(end),m['expired'],'day',m)
   # Single pending windows retain the same hash and must remain executable.
   unit=('/day/' if m['expired'] else '/days/1/')+end+'/'+start
   path=('/v2/expired-instruments/historical-candle/' if m['expired'] else '/v3/historical-candle/')+quote(m['key'],safe='')+unit
   ident=hashlib.sha256(json.dumps([path,{}],sort_keys=True).encode()).hexdigest()
   c.execute("UPDATE jobs SET state='pending' WHERE id=? AND state='superseded'",(ident,))
 after=c.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
 atomic_json(marker,{'before_pending':before,'after_pending':after,'contracts_consolidated':len(grouped),'completed_files_removed':0,'method':'Combined daily discovery; full date scope retained'})


def status(c,state,**extra):
 counts={s:n for s,n in c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state')}
 rows=c.execute('SELECT COALESCE(SUM(rows),0) FROM jobs WHERE kind="minute" AND state="done"').fetchone()[0]
 try:atomic_json(STATUS,dict(status=state,updated=datetime.now().astimezone().isoformat(),jobs=counts,minute_rows=rows,**extra))
 except PermissionError:pass  # A status display must not stop the durable download queue.

def next_jobs(c,limit=2):
 """Choose the highest-priority work while sharing each batch across indices."""
 if limit<=0:return []
 columns='id,path,params,kind,meta,attempts'
 for kind in ('expiries','contracts','minute','discovery'):
  selected=[]
  for symbol in INDICES:
   row=c.execute(f"SELECT {columns} FROM jobs WHERE state='pending' AND kind=? AND json_extract(meta,'$.symbol')=? ORDER BY rowid LIMIT 1",(kind,symbol)).fetchone()
   if row:selected.append(row)
   if len(selected)>=limit:return selected
  if not selected:continue
  # If only one index has this priority available, keep both workers busy there.
  seen={row[0] for row in selected}
  for row in c.execute(f"SELECT {columns} FROM jobs WHERE state='pending' AND kind=? ORDER BY rowid LIMIT ?",(kind,limit)).fetchall():
   if row[0] not in seen:
    selected.append(row);seen.add(row[0])
    if len(selected)>=limit:break
  return selected
 return []

def run():
 c=db();pool=ThreadPoolExecutor(max_workers=2)
 with c:c.execute('UPDATE jobs SET state="pending",attempts=0 WHERE state IN ("failed","running")')
 configfile=ROOT/'scope.json'
 if configfile.exists():scope=json.loads(configfile.read_text(encoding='utf-8'))
 else:
  end=date.today()-timedelta(days=1);start=end.replace(year=end.year-5)
  scope={'start':str(start),'end':str(end),'symbols':list(INDICES),'interval':'1minute','note':'All returned option contracts, no spread filter. Daily candles discover traded dates. Missing minutes not synthesized.'}
  atomic_json(configfile,scope)
 start=date.fromisoformat(scope['start']);end=date.fromisoformat(scope['end'])
 optimize_queue(c)
 with c:
  for symbol,key in INDICES.items():
   enqueue(c,'/v2/expired-instruments/expiries',{'instrument_key':key},'expiries',{'symbol':symbol,'underlying':key})
   enqueue(c,'/v2/option/contract',{'instrument_key':key},'contracts',{'symbol':symbol,'expired':False})
   candle_jobs(c,key,max(start,date(2022,1,1)),end,False,'1minute',{'symbol':symbol})
 consecutive_errors = 0
 while True:
  if shutil.disk_usage(ROOT).free<10*1024**3:status(c,'paused_low_disk');pool.shutdown(wait=False);return
  jobs=next_jobs(c,2)
  if not jobs:
   failed=c.execute('SELECT COUNT(*) FROM jobs WHERE state="failed"').fetchone()[0]
   status(c,'finished_with_errors' if failed else 'finished_available_requests');pool.shutdown();return
  futures=[pool.submit(fetch_job,job) for job in jobs]
  for job,future in zip(jobs,futures):
   ident,path,params,kind,metastr,attempts=job;meta=json.loads(metastr)
   status(c,'running',current={'kind':kind,'symbol':meta['symbol'],'start':meta.get('start'),'end':meta.get('end')})
   try:
    data=future.result(timeout=90)
    # Preserve original response, including zero/illiquid candles. No spread filter.
    dest=ROOT/(ident+'.json.gz');temp=dest.with_suffix('.tmp')
    blob=gzip.compress(json.dumps({'schema':1,'provider':'Upstox','retrieved_at':datetime.now().astimezone().isoformat(),'path':path,'params':json.loads(params),'meta':meta,'data':data}).encode())
    retry_file_write(lambda:temp.write_bytes(blob));retry_file_write(lambda:temp.replace(dest))
    with c:
     if kind=='expiries':
      if not isinstance(data,list):raise ValueError('Invalid expiry list')
      atomic_json(ROOT/(meta['symbol']+'_availability.json'),{'requested_start':str(start),'requested_end':str(end),'returned_expiries':data})
      for expiry in data:
       if str(start)<=expiry<=str(end):enqueue(c,'/v2/expired-instruments/option/contract',{'instrument_key':meta['underlying'],'expiry_date':expiry},'contracts',dict(meta,expired=True))
     elif kind=='contracts':
      if not isinstance(data,list):raise ValueError('Invalid contracts')
      for r in data:
       if r.get('instrument_type') not in ('CE','PE'):continue
       expiry=date.fromisoformat(r['expiry']);last=min(end,expiry)
       if last>=start:candle_jobs(c,r['instrument_key'],start,last,meta['expired'],'day',{'symbol':meta['symbol'],'contract':r})
     else:
      if not isinstance(data,dict) or not isinstance(data.get('candles'),list):raise ValueError('Invalid candles')
      candles=data['candles']
      if kind=='discovery' and candles:
       days=[date.fromisoformat(str(r[0])[:10]) for r in candles]
       first=max(date.fromisoformat(meta['start']),min(days));last=min(date.fromisoformat(meta['end']),max(days))
       if first<=last:candle_jobs(c,meta['key'],first,last,meta['expired'],'1minute',meta)
     n=len(data.get('candles',[])) if isinstance(data,dict) else len(data)
     c.execute('UPDATE jobs SET state="done",rows=?,file=?,error=NULL WHERE id=?',(n,dest.name,ident))
     consecutive_errors = 0
     consecutive_errors = 0
   except Exception as exc:
    # Do not persist broker responses/tokens in error messages.
    is_fatal_auth = '401' in str(exc) or '403' in str(exc)
    next_state = 'failed' if (attempts>=2 or is_fatal_auth) else 'pending'
    with c:c.execute('UPDATE jobs SET state=?,attempts=attempts+1,error=? WHERE id=?',(next_state,type(exc).__name__,ident))
    status(c,'retrying',error_type=type(exc).__name__)
    consecutive_errors += 1
    if is_fatal_auth or consecutive_errors >= 10:
     status(c,'paused_auth_error' if is_fatal_auth else 'paused_network_error',error_type=type(exc).__name__);pool.shutdown(wait=False);return
    time.sleep(2)

if __name__=='__main__':
 ROOT.mkdir(parents=True, exist_ok=True)
 with os.fdopen(os.open(str(ROOT/'download.lock'), os.O_CREAT | os.O_RDWR, 0o666), 'r+b') as lock:
  lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
  try:
   if msvcrt is not None: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise SystemExit(0)
  try:run()
  except BaseException as exc:
   with db() as c:status(c,'stopped' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'paused_error',error_type=type(exc).__name__)
   if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
  finally:
   lock.seek(0)
   if msvcrt is not None:
    try: msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    except OSError: pass
