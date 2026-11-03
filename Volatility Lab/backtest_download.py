"""Resumable raw backtest archive. No bid/ask or IV inferred from candles."""
from pathlib import Path
from datetime import date,timedelta,datetime
from urllib.parse import quote
import gzip,json,sqlite3,hashlib,time,shutil,msvcrt,tempfile,multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from upstox_archive import Collector,INDICES
from collector_support import atomic_json as _atomic_json
ROOT=Path(__file__).resolve().parent/'data'/'backtest_1minute'
ROOT.mkdir(parents=True,exist_ok=True)
STATUS=ROOT/'status.json'
REQUEST_TIMEOUT_SECONDS=80

class RequestTimeoutError(RuntimeError):pass
class RequestWorkerError(RuntimeError):pass

def _fetch_job_child(job,result_file):
 """Isolate one broker request so an OS-level network stall cannot freeze the queue."""
 try:result=('ok',Collector().call(job[1],json.loads(job[2])))
 except BaseException as exc:result=('error',type(exc).__name__)
 Path(result_file).write_text(json.dumps(result),encoding='utf-8')

def fetch_job(job):
 """Hard-bounded request; replies remain raw and are written only by the parent."""
 # Wait for process completion, not the first bytes in a pipe: a partial pipe
 # reply could otherwise leave recv() blocked beyond the request deadline.
 with tempfile.TemporaryDirectory(prefix='request-',dir=ROOT) as scratch:
  result_file=Path(scratch)/'response.json'
  process=mp.get_context('spawn').Process(target=_fetch_job_child,args=(job,str(result_file)),daemon=True)
  process.start()
  try:
   process.join(REQUEST_TIMEOUT_SECONDS)
   if process.is_alive():raise RequestTimeoutError()
   if process.exitcode!=0 or not result_file.exists():raise RequestWorkerError()
   kind,payload=json.loads(result_file.read_text(encoding='utf-8'))
   if kind!='ok':raise RequestWorkerError(str(payload))
   return payload
  finally:
   if process.is_alive():process.terminate()
   process.join(10)
   if process.is_alive():
    process.kill();process.join(5)
   process.close()

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
 c.execute('PRAGMA journal_mode=WAL')
 c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,path TEXT,params TEXT,kind TEXT,meta TEXT,state TEXT DEFAULT "pending",rows INTEGER,file TEXT,attempts INTEGER DEFAULT 0,error TEXT)')
 c.execute("CREATE INDEX IF NOT EXISTS jobs_state_kind ON jobs(state,kind)")
 c.execute("CREATE INDEX IF NOT EXISTS jobs_key_kind ON jobs(json_extract(meta,'$.key'),kind)")
 return c

def excluded_contract(meta):
 return meta.get('symbol')=='BANKNIFTY' and (meta.get('contract') or {}).get('weekly') is True

def enqueue(c,path,params,kind,meta,*,revive_superseded=False):
 if kind in ('minute','discovery') and excluded_contract(meta):return
 ident=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
 c.execute('INSERT OR IGNORE INTO jobs(id,path,params,kind,meta) VALUES(?,?,?,?,?)',(ident,path,json.dumps(params),kind,json.dumps(meta)))
 if revive_superseded:
  c.execute("UPDATE jobs SET state='pending',attempts=0,error=NULL WHERE id=? AND state='superseded'",(ident,))

def candle_jobs(c,key,start,end,expired,interval,meta,*,revive_superseded=False):
 if excluded_contract(meta):return
 # Retain existing request boundaries so resuming does not repeat completed work.
 step=28 if interval=='1minute' else 299 if expired else 3600
 if interval=='1minute':
  covered=[]
  for text, in c.execute("SELECT meta FROM jobs WHERE json_extract(meta,'$.key')=? AND kind='minute' AND state IN ('done','pending','retry','failed')",(key,)):
   old=json.loads(text);covered.append((date.fromisoformat(old['start']),date.fromisoformat(old['end'])))
  spans=[(start,end)]
  for a,b in sorted(covered):
   spans=[piece for left,right in spans for piece in ([(left,right)] if b<left or a>right else ([(left,a-timedelta(days=1))] if left<a else [])+([(b+timedelta(days=1),right)] if b<right else []))]
  for left,right in spans:_enqueue_candles(c,key,left,right,expired,interval,meta,step)
  return
 _enqueue_candles(c,key,start,end,expired,interval,meta,step,revive_superseded=revive_superseded)

def _enqueue_candles(c,key,start,end,expired,interval,meta,step,*,revive_superseded=False):
 while start<=end:
  stop=min(start+timedelta(days=step),end)
  unit=interval if expired else 'minutes/1' if interval=='1minute' else 'days/1'
  path=('/v2/expired-instruments/historical-candle/' if expired else '/v3/historical-candle/')+quote(key,safe='')+'/'+unit+'/'+str(stop)+'/'+str(start)
  enqueue(c,path,{},'minute' if interval=='1minute' else 'discovery',dict(meta,key=key,expired=expired,start=str(start),end=str(stop)),revive_superseded=revive_superseded)
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
   candle_jobs(c,m['key'],date.fromisoformat(start),date.fromisoformat(end),m['expired'],'day',m,revive_superseded=True)
 after=c.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
 atomic_json(marker,{'before_pending':before,'after_pending':after,'contracts_consolidated':len(grouped),'completed_files_removed':0,'method':'Combined daily discovery; full date scope retained'})


def status(c,state,**extra):
 counts={s:n for s,n in c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state')}
 rows=c.execute('SELECT COALESCE(SUM(rows),0) FROM jobs WHERE kind="minute" AND state="done"').fetchone()[0]
 try:atomic_json(STATUS,dict(status=state,updated=datetime.now().astimezone().isoformat(),jobs=counts,minute_rows=rows,**extra))
 except PermissionError:pass  # A status display must not stop the durable download queue.

def repair_pacing_queue(c):
 """Once only: restore requests affected by the old limiter and blanket skip.

 Keep the previous state in the queue itself. The recovery and its marker commit
 together; subsequent starts must not reset genuine repeated provider failures.
 Completed responses and their files are never changed by this migration.
 """
 repair='pacing_livelock_20261026'
 with c:
  c.execute('CREATE TABLE IF NOT EXISTS queue_repairs(name TEXT PRIMARY KEY,applied_at TEXT,restored INTEGER)')
  c.execute('CREATE TABLE IF NOT EXISTS queue_repair_jobs(repair TEXT,job_id TEXT,old_state TEXT,old_attempts INTEGER,old_error TEXT,PRIMARY KEY(repair,job_id))')
  if c.execute('SELECT 1 FROM queue_repairs WHERE name=?',(repair,)).fetchone():return 0
  restored=[]
  for ident,state,attempts,error,text in c.execute("SELECT id,state,attempts,error,meta FROM jobs WHERE (state='skipped' AND error='ActiveContractHistoryNotRequested') OR (state IN ('retry','failed') AND error='RequestTimeoutError')").fetchall():
   meta=json.loads(text)
   if meta.get('symbol') not in INDICES or excluded_contract(meta):continue
   restored.append((repair,ident,state,attempts,error))
  c.executemany('INSERT INTO queue_repair_jobs VALUES(?,?,?,?,?)',restored)
  c.executemany("UPDATE jobs SET state='pending',attempts=0,error=NULL WHERE id=?",[(row[1],) for row in restored])
  c.execute('INSERT INTO queue_repairs VALUES(?,?,?)',(repair,datetime.now().astimezone().isoformat(),len(restored)))
 return len(restored)

def skip_banknifty_weekly_history(c):
 """BANKNIFTY weekly contracts are excluded from this archive by strategy scope."""
 ids=[]
 for ident,text in c.execute("SELECT id,meta FROM jobs WHERE kind IN ('minute','discovery') AND state IN ('pending','retry','failed') AND json_extract(meta,'$.symbol')='BANKNIFTY'"):
  meta=json.loads(text)
  if (meta.get('contract') or {}).get('weekly') is True:ids.append((ident,))
 if ids:
  c.executemany("UPDATE jobs SET state='skipped',error='BankNiftyWeeklyExcluded' WHERE id=?",ids)
 return len(ids)

def rechunk_expired_discovery(c):
 """Replace legacy multi-year expired-contract discovery calls with 299-day windows."""
 work=[]
 for ident,text in c.execute("SELECT id,meta FROM jobs WHERE kind='discovery' AND state IN ('pending','retry','failed') AND json_extract(meta,'$.expired')=1").fetchall():
  meta=json.loads(text);start=date.fromisoformat(meta['start']);end=date.fromisoformat(meta['end'])
  if (end-start).days>299:work.append((ident,meta,start,end))
 for ident,meta,start,end in work:
  _enqueue_candles(c,meta['key'],start,end,True,'day',meta,299,revive_superseded=True)
  c.execute("UPDATE jobs SET state='superseded',error='SplitExpiredDailyRequest' WHERE id=?",(ident,))
 return len(work)

def next_jobs(c,limit=2):
 """Choose the highest-priority work while sharing each batch across indices."""
 if limit<=0:return []
 columns='id,path,params,kind,meta,attempts'
 for kind in ('expiries','contracts','minute','discovery'):
  selected=[]
  for symbol in INDICES:
   row=c.execute(f"SELECT {columns} FROM jobs WHERE state IN ('pending','retry') AND kind=? AND json_extract(meta,'$.symbol')=? ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END,attempts,rowid LIMIT 1",(kind,symbol)).fetchone()
   if row:selected.append(row)
   if len(selected)>=limit:return selected
  if not selected:continue
  # If only one index has this priority available, keep both workers busy there.
  seen={row[0] for row in selected}
  for row in c.execute(f"SELECT {columns} FROM jobs WHERE state IN ('pending','retry') AND kind=? ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END,attempts,rowid LIMIT ?",(kind,limit)).fetchall():
   if row[0] not in seen:
    selected.append(row);seen.add(row[0])
    if len(selected)>=limit:break
  return selected
 return []

def run():
 c=db();pool=ThreadPoolExecutor(max_workers=2)
 repair_pacing_queue(c)
 with c:
  weekly_skipped=skip_banknifty_weekly_history(c)
  rechunked=rechunk_expired_discovery(c)
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
 while True:
  if shutil.disk_usage(ROOT).free<10*1024**3:status(c,'paused_low_disk');return
  jobs=next_jobs(c,2)
  if not jobs:
   failed=c.execute('SELECT COUNT(*) FROM jobs WHERE state="failed"').fetchone()[0]
   status(c,'finished_with_errors' if failed else 'finished_available_requests');pool.shutdown();return
  futures=[pool.submit(fetch_job,job) for job in jobs]
  for job,future in zip(jobs,futures):
   ident,path,params,kind,metastr,attempts=job;meta=json.loads(metastr)
   status(c,'running',current={'kind':kind,'symbol':meta['symbol'],'start':meta.get('start'),'end':meta.get('end')})
   try:
    data=future.result()
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
       if meta['symbol']=='BANKNIFTY' and r.get('weekly') is True:continue
       expiry=date.fromisoformat(r['expiry']);last=min(end,expiry)
       if last>=start:candle_jobs(c,r['instrument_key'],start,last,bool(meta.get('expired')),'day',{'symbol':meta['symbol'],'contract':r})
     else:
      if not isinstance(data,dict) or not isinstance(data.get('candles'),list):raise ValueError('Invalid candles')
      candles=data['candles']
      if kind=='discovery' and candles:
       days=[date.fromisoformat(str(r[0])[:10]) for r in candles]
       first=max(date.fromisoformat(meta['start']),min(days));last=min(date.fromisoformat(meta['end']),max(days))
       if first<=last:candle_jobs(c,meta['key'],first,last,meta['expired'],'1minute',meta)
     n=len(data.get('candles',[])) if isinstance(data,dict) else len(data)
     c.execute('UPDATE jobs SET state="done",rows=?,file=?,error=NULL WHERE id=?',(n,dest.name,ident))
   except Exception as exc:
    # Do not persist broker responses/tokens in error messages.
    next_attempt=attempts+1
    next_state='failed' if next_attempt>=3 else 'retry'
    with c:c.execute('UPDATE jobs SET state=?,attempts=?,error=? WHERE id=?',(next_state,next_attempt,type(exc).__name__,ident))
    status(c,'continuing_after_error',error_type=type(exc).__name__,retry_state=next_state)
    time.sleep(2)

if __name__=='__main__':
 with (ROOT/'download.lock').open('a+b') as lock:
  lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
  try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise SystemExit(0)
  try:run()
  except Exception as exc:
   with db() as c:status(c,'paused_error',error_type=type(exc).__name__)
  finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
