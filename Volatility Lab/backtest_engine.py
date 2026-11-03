"""Draft intraday backtest: exact-minute open fills, explicit missing-data skips."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,gzip,sqlite3,math
from contextlib import closing
import pandas as pd
from backtest_common import record_skip
ROOT=Path(__file__).resolve().parent/'data/backtest_1minute'

def load(symbol,start,end):
 first,last=str(start),str(end)
 # Apply the same metadata predicates in SQLite, rather than deserializing every
 # completed request from the five-year archive for each selected date range.
 with closing(sqlite3.connect((ROOT/'queue.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=30)) as con:
  jobs=con.execute('''SELECT kind,meta,file FROM jobs WHERE state="done" AND kind IN ("contracts","minute")
   AND json_extract(meta,'$.symbol')=? AND (kind='contracts' OR
   (COALESCE(json_extract(meta,'$.end'),'9999')>=? AND COALESCE(json_extract(meta,'$.start'),'0000')<=?))''',(symbol,first,last)).fetchall()
 contracts={};series={};conflicts=set()
 for kind,metastr,file in jobs:
  meta=json.loads(metastr)
  with gzip.open(ROOT/file,'rt',encoding='utf-8') as f:o=json.load(f)
  if kind=='contracts':
   for r in o['data']:
    if r.get('instrument_type') in ('CE','PE'):contracts[r['instrument_key']]=r
  else:
   key=meta['key'];dest=series.setdefault(key,{})
   for row in o['data']['candles']:
    dt=datetime.fromisoformat(row[0])
    if dt.tzinfo is None:continue
    local=dt.astimezone(_IST)
    # Chunks commonly span weeks. Avoid strftime for rows outside the requested
    # sessions, while still parsing/validating every timestamp as before.
    if not first<=local.date().isoformat()<=last:continue
    stamp=local.strftime('%Y-%m-%d %H:%M')
    if stamp in dest and dest[stamp]!=row[1:]:conflicts.add((key,stamp))
    dest[stamp]=row[1:]
 for key,stamp in conflicts:series[key].pop(stamp,None)
 return list(contracts.values()),series,len(conflicts)

_IST=timezone(timedelta(hours=5,minutes=30))

def simulate(contracts,series,spotkey,start,end,entry='09:20',exit='15:15',offset=0,wings=0,quantity=1,slip_bps=5,order_fee=25,turnover_bps=0,min_dte=1,max_dte=35,use_upstox_fees=False,signal_rows=None,filters=()):
 if entry>=exit or not 0<=slip_bps<10000 or quantity<=0 or offset<0 or wings<0:raise ValueError('Invalid strategy settings')
 trades=[];skips=[];spot=series.get(spotkey,{})
 days=sorted({s[:10] for s in spot if str(start)<=s[:10]<=str(end)})
 for day in days:
  decision=(datetime.fromisoformat(day+' '+entry)-timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M')
  bar=spot.get(decision)
  if not bar:record_skip(skips,day,'Missing preceding spot minute');continue
  price=float(bar[3])
  eligible=[r for r in contracts if min_dte<=(pd.Timestamp(r['expiry'])-pd.Timestamp(day)).days<=max_dte]
  if not eligible:record_skip(skips,day,'No eligible expiry in catalogue');continue
  exp=min(r['expiry'] for r in eligible)
  if filters:
   from backtest_signals import decide
   allowed,reason=decide(signal_rows or [],exp,day,entry,filters)
   if not allowed:record_skip(skips,day,reason);continue
  chain=[r for r in eligible if r['expiry']==exp]
  def nearest(kind,target):
   choices=[r for r in chain if r['instrument_type']==kind]
   return min(choices,key=lambda r:(abs(float(r['strike_price'])-target),float(r['strike_price']))) if choices else None
  call=nearest('CE',price+offset);put=nearest('PE',price-offset)
  if not call or not put:record_skip(skips,day,'Missing contract definitions');continue
  legs=[(call,-1),(put,-1)]
  if wings:
   c=nearest('CE',float(call['strike_price'])+wings);p=nearest('PE',float(put['strike_price'])-wings)
   if not c or not p or float(c['strike_price'])<=float(call['strike_price']) or float(p['strike_price'])>=float(put['strike_price']):record_skip(skips,day,'Protective strikes unavailable');continue
   legs.extend([(c,1),(p,1)])
  fills=[];gross=slippage=turnover=0.;reason=None;cost_parts={}
  for r,side in legs:
   data=series.get(r['instrument_key'],{});a=data.get(day+' '+entry);b=data.get(day+' '+exit)
   if a is None or b is None:reason='Missing exact entry/exit minute';break
   try:
    raw_a=float(a[0]);raw_b=float(b[0])
    if not all(math.isfinite(x) and x>0 for x in (raw_a,raw_b,float(a[4]),float(b[4]))):raise ValueError()
   except (ValueError,TypeError,IndexError):reason='Invalid price or no traded volume';break
   buy_a=raw_a*(1+side*slip_bps/10000);sell_b=raw_b*(1-side*slip_bps/10000)
   gross+=side*(raw_b-raw_a)*quantity
   slippage+=(raw_a+raw_b)*slip_bps/10000*quantity
   turnover+=(buy_a+sell_b)*quantity
   if use_upstox_fees:
    from broker_charges import charge
    for px,action in [(buy_a,'buy' if side==1 else 'sell'),(sell_b,'sell' if side==1 else 'buy')]:
     for label,value in charge(day,'Options',px,quantity,action).items():cost_parts[label]=cost_parts.get(label,0)+value
   fills.append({'contract':r['trading_symbol'],'key':r['instrument_key'],'side':side,'entry':buy_a,'exit':sell_b,'quantity':quantity})
  if reason:record_skip(skips,day,reason);continue
  charges=sum(cost_parts.values()) if use_upstox_fees else 2*len(legs)*order_fee+turnover*turnover_bps/10000
  trades.append({'Date':day,'Expiry':exp,'Spot signal':price,'Gross':gross,'Slippage':slippage,'Charges':charges,'Net':gross-slippage-charges,'Legs':json.dumps(fills),**cost_parts})
 return pd.DataFrame(trades),pd.DataFrame(skips)
