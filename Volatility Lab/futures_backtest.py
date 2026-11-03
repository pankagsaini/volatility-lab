"""Dated-contract futures carry test. Close signals execute at next available session open."""
from pathlib import Path
from collections import OrderedDict
from threading import RLock
import gzip,json,math
import pandas as pd
from broker_charges import charge
from backtest_common import is_blacklisted_future
ROOT=Path(__file__).resolve().parent/'data/backtest_futures'

# The archive contains many minute responses; this daily engine must not keep
# their decoded payloads in memory or decompress them again on every rerun.
_FILE_CACHE=OrderedDict()
_FILE_CACHE_LOCK=RLock()
_FILE_CACHE_ROWS=0
_MAX_CACHE_FILES=2048
_MAX_CACHE_ROWS=75000


def _daily_archive(path):
 global _FILE_CACHE_ROWS
 stat=path.stat();revision=(stat.st_mtime_ns,stat.st_size,stat.st_ino)
 key=str(path)
 with _FILE_CACHE_LOCK:
  saved=_FILE_CACHE.get(key)
  if saved is not None and saved[0]==revision:
   _FILE_CACHE.move_to_end(key);return saved[1]
 with gzip.open(path,'rt',encoding='utf-8') as f:o=json.load(f)
 meta=o.get('meta',{});contract=meta.get('contract',{});route=o.get('path','')
 value=None;weight=0
 if o.get('provider')=='Upstox' and contract.get('instrument_type')=='FUT' and ('/day/' in route or '/days/1/' in route):
  candles=o['data'].get('candles',[])
  # Keep raw row values: only the requested symbol/date range is converted,
  # exactly as in the original loader. Conflicting duplicates remain excluded.
  value=(meta.get('symbol'),contract,candles)
  weight=len(candles) if isinstance(candles,list) else 0
 with _FILE_CACHE_LOCK:
  old=_FILE_CACHE.pop(key,None)
  if old is not None:_FILE_CACHE_ROWS-=old[2]
  if weight<=_MAX_CACHE_ROWS:
   while _FILE_CACHE and (len(_FILE_CACHE)>=_MAX_CACHE_FILES or _FILE_CACHE_ROWS+weight>_MAX_CACHE_ROWS):
    _,removed=_FILE_CACHE.popitem(last=False);_FILE_CACHE_ROWS-=removed[2]
   _FILE_CACHE[key]=(revision,value,weight);_FILE_CACHE_ROWS+=weight
 return value


def clear_load_cache():
 global _FILE_CACHE_ROWS
 with _FILE_CACHE_LOCK:_FILE_CACHE.clear();_FILE_CACHE_ROWS=0

def broker_lot_size(contract):
 if isinstance(contract,dict):
  v=contract.get('lot_size')
  if isinstance(v,(int,float)) and v>0:return int(v)
 elif isinstance(contract,(int,float)) and contract>0:return int(contract)
 return None


def contract_lot_sizes(symbol):
 sizes={}
 for p in sorted(ROOT.resolve().glob('*.json.gz')):
  saved=_daily_archive(p)
  if saved is None or saved[0]!=symbol:continue
  _,r,_=saved
  exp=r.get('expiry')
  if not exp:continue
  exp_str=str(pd.to_datetime(exp,unit='ms',utc=True).date()) if isinstance(exp,(int,float)) else str(exp)[:10]
  if is_blacklisted_future(symbol,exp_str):continue
  lot=broker_lot_size(r)
  if lot and exp_str not in sizes:sizes[exp_str]=lot
 return sizes


def load(symbol,start,end):
 bars={};conflicts=set()
 first,last=str(start),str(end)
 for p in sorted(ROOT.resolve().glob('*.json.gz')):
  saved=_daily_archive(p)
  if saved is None or saved[0]!=symbol:continue
  _,r,candles=saved
  exp=r['expiry'];exp=str(pd.to_datetime(exp,unit='ms',utc=True).date()) if isinstance(exp,(int,float)) else exp
  if is_blacklisted_future(symbol,exp):continue
  for row in candles:
   day=str(row[0])[:10]
   if not first<=day<=last:continue
   key=(day,exp);value=tuple(float(v) for v in row[1:6])
   if key in bars and bars[key]!=value:conflicts.add(key)
   bars[key]=value
 for key in conflicts:bars.pop(key,None)
 return bars,len(conflicts)

def simulate(bars,pair=(0,2),entry=6.,target=6.66,quantity=75,near_side=-1,slip_bps=1,max_days=30,brokerage_per_order=20,lots=None,contracts=None):
 if entry==target or near_side not in (-1,1):raise ValueError('Choose distinct thresholds and a direction')
 by_day={}
 for day,expiry in bars:
  by_day.setdefault(day,[])
  if expiry>day:by_day[day].append(expiry)
 for expiries in by_day.values():expiries.sort()
 days=sorted(by_day);position=None;pending=None;trades=[];skips=[]
 stamps={}
 def stamp(day):
  if day not in stamps:stamps[day]=pd.Timestamp(day)
  return stamps[day]
 def eligible(day):return by_day[day]
 def prices(day,exps,index):
  values=[bars.get((day,e)) for e in exps]
  if any(v is None or v[4]<=0 or not all(math.isfinite(x) and x>0 for x in v[:4]) for v in values):return None
  return [v[index] for v in values]
 def carry(px,exps):return math.log(px[1]/px[0])*365/(stamp(exps[1])-stamp(exps[0])).days*100
 def leg_quantities(exps):
  if lots is not None and lots>0:
   c_near=(contracts or {}).get(exps[0])
   c_far=(contracts or {}).get(exps[1])
   lot_near=broker_lot_size(c_near) or quantity
   lot_far=broker_lot_size(c_far) or quantity
   return int(lots)*lot_near,int(lots)*lot_far
  return quantity,quantity
 def costs(day,px,opening,qn,qf):
  out={}
  for value,side,q in zip(px,[near_side,-near_side],[qn,qf]):
   action='buy' if side*(1 if opening else -1)==1 else 'sell'
   for k,v in charge(day,'Futures',value,q,action,brokerage_per_order).items():out[k]=out.get(k,0)+v
  return out
 def mark_position(day):
  if not position:return False
  px=prices(day,position['exps'],3)
  if not px:
   position['last_mark_issue']='Missing exact-contract close'
   return False
  sides=[near_side,-near_side]
  qs=[position['q_near'],position['q_far']]
  gross=sum(s*(b-a)*q for s,a,b,q in zip(sides,position['raw'],px,qs))
  after_entry=sum(s*(b-a)*q for s,a,b,q in zip(sides,position['fills'],px,qs))-sum(position['costs'].values())
  position['last_mark']={'date':day,'prices':px,'carry':carry(px,position['exps']),'gross':gross,'after_entry_costs':after_entry}
  position['last_mark_issue']=None
  return True
 def unfinished(reason):
  if not position:return None
  last=position.get('last_mark') or {}
  qn,qf=position['q_near'],position['q_far']
  rec={
   'Status':reason,
   'Entry':position['day'],
   'Near expiry':position['exps'][0],
   'Far expiry':position['exps'][1],
   'Direction':'Buy near / sell far' if near_side==1 else 'Sell near / buy far',
   'Quantity per leg':qn if qn==qf else f"{qn} (near) / {qf} (far)",
   'Entry carry %':position['carry'],
   'Entry fill near':position['fills'][0],
   'Entry fill far':position['fills'][1],
   'Estimated entry costs':sum(position['costs'].values()),
   'Last mark date':last.get('date'),
   'Last mark near close':(last.get('prices') or [None,None])[0],
   'Last mark far close':(last.get('prices') or [None,None])[1],
   'Last mark carry %':last.get('carry'),
   'Unrealised gross':last.get('gross'),
   'Unrealised P&L before exit costs':last.get('after_entry_costs'),
   'Mark status':'Latest exact-contract close' if last else position.get('last_mark_issue','No exact-contract close after entry')
  }
  if lots is not None and lots>0:
   rec['Sizing']='broker_lot_size'
   rec['Strategy lots']=int(lots)
  return rec
 final_reason=None
 for day in days:
  if pending:
   kind,exps,signalday,signalcarry,reason=pending
   px=prices(day,exps,0)
   if kind=='entry' and (stamp(day)-stamp(signalday)).days>7:px=None
   if min(exps)<=day:
    if position:
     final_reason='Expiry boundary reached before an executable exit'
     skips.append({'Date':day,'Reason':final_reason});break
    pending=None
   elif px:
    sides=[near_side,-near_side]
    fills=[v*(1+s*(1 if kind=='entry' else -1)*slip_bps/10000) for v,s in zip(px,sides)]
    if kind=='entry':
     qn,qf=leg_quantities(exps)
     position={'day':day,'exps':exps,'raw':px,'fills':fills,'q_near':qn,'q_far':qf,'costs':costs(day,fills,True,qn,qf),'carry':signalcarry,'last_mark':None,'last_mark_issue':None}
    else:
     qn,qf=position['q_near'],position['q_far']
     gross=sum(s*(b-a)*q for s,a,b,q in zip(sides,position['raw'],px,[qn,qf]))
     net_before=sum(s*(b-a)*q for s,a,b,q in zip(sides,position['fills'],fills,[qn,qf]))
     components=costs(day,fills,False,qn,qf)
     for k,v in position['costs'].items():components[k]+=v
     trade={'Entry':position['day'],'Exit':day,'Near expiry':exps[0],'Far expiry':exps[1],'Entry carry %':position['carry'],'Exit signal carry %':signalcarry,'Gross':gross,'Slippage':gross-net_before,'Charges':sum(components.values()),'Net':net_before-sum(components.values()),'Reason':reason,**components}
     if lots is not None and lots>0:
      trade['Sizing']='broker_lot_size'
      trade['Strategy lots']=int(lots)
      trade['Near lot size']=qn//int(lots) if lots else None
      trade['Far lot size']=qf//int(lots) if lots else None
      trade['Near quantity']=qn
      trade['Far quantity']=qf
     trades.append(trade)
     position=None
    pending=None
   else:
    missing='Missing exact contract open; '+kind+' not filled'
    skips.append({'Date':day,'Reason':missing})
    if kind=='entry':pending=None
    elif position:position['last_exit_issue']=missing
  if pending:
   mark_position(day)
   continue
  if position:
   if min(position['exps'])<=day:
    final_reason='Expiry boundary reached before an executable exit'
    skips.append({'Date':day,'Reason':final_reason});break
   marked=mark_position(day)
   if not marked:
    skips.append({'Date':day,'Reason':'Missing held-contract close'})
    continue
   exps=position['exps'];c=position['last_mark']['carry']
   reason='Carry target' if (c>=target if target>entry else c<=target) else 'Max holding period' if (stamp(day)-stamp(position['day'])).days>=max_days else 'Pre-expiry exit' if (stamp(exps[0])-stamp(day)).days<=3 else None
   if reason:pending=('exit',exps,day,c,reason)
  else:
   exps=eligible(day)
   if len(exps)<=max(pair):continue
   exps=[exps[i] for i in pair];px=prices(day,exps,3)
   if not px or (stamp(exps[0])-stamp(day)).days<=4:continue
   c=carry(px,exps)
   if (c<=entry if target>entry else c>=entry):pending=('entry',exps,day,c,'Entry threshold')
 if position:
  if final_reason is None:
   if pending and pending[0]=='exit':
    final_reason='Pending exit: '+pending[4]
    if position.get('last_exit_issue'):final_reason+='; exact exit open unavailable'
   else:final_reason='End of selected period'
  return pd.DataFrame(trades),pd.DataFrame(skips),unfinished(final_reason)
 return pd.DataFrame(trades),pd.DataFrame(skips),None
