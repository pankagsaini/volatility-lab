"""One-off futures archive; original provider responses, no dashboard provider changes."""
from pathlib import Path
from datetime import date,timedelta,datetime
from urllib.parse import quote
import json,gzip,hashlib,shutil,msvcrt
from collector_support import atomic_json
from upstox_archive import Collector,INDICES
ROOT=Path(__file__).resolve().parent/'data'/'backtest_futures'
ROOT.mkdir(parents=True,exist_ok=True)

def fetch(provider,call,path,params,meta):
 ident=hashlib.sha256(json.dumps([provider,path,params],sort_keys=True).encode()).hexdigest()
 file=ROOT/(ident+'.json.gz')
 if file.exists():
  with gzip.open(file,'rt',encoding='utf-8') as f:return json.load(f)['data']
 if shutil.disk_usage(ROOT).free<10*1024**3:raise RuntimeError('Low disk')
 data=call(path,params)
 body={'provider':provider,'path':path,'params':params,'meta':meta,'retrieved_at':datetime.now().astimezone().isoformat(),'data':data}
 tmp=file.with_suffix('.tmp');tmp.write_bytes(gzip.compress(json.dumps(body).encode()));tmp.replace(file)
 return data

def ranges(first,last,days):
 while first<=last:
  stop=min(last,first+timedelta(days=days-1));yield first,stop;first=stop+timedelta(days=1)

def status(provider,state,**kwargs):
 atomic_json(ROOT/(provider+'_status.json'),dict(status=state,updated=datetime.now().astimezone().isoformat(),**kwargs))

def upstox(start,end):
 c=Collector();contracts={}
 for symbol,key in INDICES.items():
  exps=fetch('Upstox',c.call,'/v2/expired-instruments/expiries',{'instrument_key':key},{'symbol':symbol})
  # Current instruments supplement expired catalogue.
  from collector_support import read_json
  for r in read_json(Path(__file__).resolve().parent/'data/upstox/master.json').get('instruments',[]):
   if r.get('underlying_key')==key and r.get('instrument_type')=='FUT':
    contracts[r['instrument_key']]=(symbol,r,False)
  for exp in exps:
   if not str(start)<=exp<=str(end):continue
   status('Upstox','cataloguing',symbol=symbol,expiry=exp)
   rows=fetch('Upstox',c.call,'/v2/expired-instruments/future/contract',{'instrument_key':key,'expiry_date':exp},{'symbol':symbol})
   for r in rows:contracts[r['instrument_key']]=(symbol,r,True)
 for i,(key,(symbol,r,expired)) in enumerate(contracts.items(),1):
  import pandas as pd
  exp=r['expiry'];exp=pd.to_datetime(exp,unit='ms',utc=True).date() if isinstance(exp,(float,int)) else date.fromisoformat(exp)
  stop=min(end,exp);meta={'symbol':symbol,'contract':r,'expired':expired}
  prefix='/v2/expired-instruments/historical-candle/' if expired else '/v3/historical-candle/'
  # Discover every returned trading period across target five years; don't assume listing date.
  for a,b in ranges(start,stop,300):
   status('Upstox','running',symbol=symbol,contract=i,total_contracts=len(contracts),expiry=str(exp),interval='day_discovery')
   daily=fetch('Upstox',c.call,prefix+quote(key,safe='')+'/'+('day' if expired else 'days/1')+'/'+str(b)+'/'+str(a),{},meta)
   candles=daily.get('candles',[])
   if not candles:continue
   dates=[date.fromisoformat(str(x[0])[:10]) for x in candles]
   for first,last in ranges(max(a,min(dates)),min(b,max(dates)),28):
    status('Upstox','running',symbol=symbol,contract=i,total_contracts=len(contracts),expiry=str(exp),interval='1minute')
    fetch('Upstox',c.call,prefix+quote(key,safe='')+'/'+('1minute' if expired else 'minutes/1')+'/'+str(last)+'/'+str(first),{},meta)
 status('Upstox','finished_available_requests',contracts=len(contracts))

if __name__=='__main__':
 with (ROOT/'download.lock').open('a+b') as lock:
  lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
  try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise SystemExit(0)
  try:
   scope=ROOT/'scope.json'
   if scope.exists():o=json.loads(scope.read_text());start=date.fromisoformat(o['start']);end=date.fromisoformat(o['end'])
   else:
    end=date.today()-timedelta(days=1);start=end.replace(year=end.year-5);atomic_json(scope,{'start':str(start),'end':str(end)})
   try:upstox(start,end)
   except Exception as exc:status('Upstox','paused_error',error_type=type(exc).__name__)
  finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
