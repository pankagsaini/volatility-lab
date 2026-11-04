"""Multi-leg minute-close research engine. All exits use subsequent open fills."""
import json,math
from datetime import datetime,timedelta
import pandas as pd
from backtest_common import record_skip
from broker_charges import charge
from backtest_signals import decide
from backtest_index import ContractIndex,SignalIndex

TEMPLATES={
 'Short straddle':[('Sell','CE',0,1),('Sell','PE',0,1)],
 'Short strangle':[('Sell','CE',200,1),('Sell','PE',-200,1)],
 'Iron condor':[('Sell','CE',200,1),('Sell','PE',-200,1),('Buy','CE',700,1),('Buy','PE',-700,1)],
 'Iron butterfly':[('Sell','CE',0,1),('Sell','PE',0,1),('Buy','CE',500,1),('Buy','PE',-500,1)],
 'Long straddle':[('Buy','CE',0,1),('Buy','PE',0,1)],
 'Long strangle':[('Buy','CE',200,1),('Buy','PE',-200,1)]}

def template(name):
 target=str(name).strip().lower()
 for k,v in TEMPLATES.items():
  if k.lower()==target:return [dict(Side=a,Type=b,Offset=c,Ratio=d) for a,b,c,d in v]
 return [dict(Side=a,Type=b,Offset=c,Ratio=d) for a,b,c,d in TEMPLATES['Iron condor']]

def valid_price(bar,index=0,fill=False):
 try:return math.isfinite(float(bar[index])) and float(bar[index])>0 and (not fill or float(bar[4])>0)
 except (TypeError,ValueError,IndexError):return False

def _positive_int(value,label):
 try:number=float(value)
 except (TypeError,ValueError):raise ValueError(label+' must be a positive whole number.')
 if not math.isfinite(number) or number<1 or number!=int(number):raise ValueError(label+' must be a positive whole number.')
 return int(number)

def sizing(s):
 """New runs use broker lots; saved fixed-unit runs remain readable."""
 if 'lots' in s:return 'broker_lot_size',_positive_int(s['lots'],'Strategy lots')
 return 'legacy_fixed_units',_positive_int(s.get('quantity'),'Base units per leg')

def broker_lot_size(contract):
 try:return _positive_int(contract.get('lot_size'),'Broker lot size')
 except ValueError:return None

def capital_settings(s):
 out=[]
 for key,label in (('capital_allocated','Capital allocated'),('margin_per_lot','Broker margin allocation per lot')):
  try:value=float(s.get(key,0) or 0)
  except (TypeError,ValueError):raise ValueError(label+' must be a non-negative number.')
  if not math.isfinite(value) or value<0:raise ValueError(label+' must be a non-negative number.')
  out.append(value)
 return tuple(out)

def validate(s):
 if not s['start']<=s['end']:raise ValueError('Start date must precede end date.')
 if not '09:16'<=s['entry']<s['exit']<='15:29':raise ValueError('Use 09:16–15:29, with entry before exit.')
 if not s['legs'] or len(s['legs'])>8:raise ValueError('Choose one to eight legs.')
 if not 0<=s['slippage_bps']<=500:raise ValueError('Invalid slippage.')
 sizing(s);capital_settings(s)
 try:brokerage=float(s.get('brokerage_per_order',20) or 0)
 except (TypeError,ValueError):raise ValueError('Brokerage per order must be a non-negative number.')
 if not math.isfinite(brokerage) or brokerage<0:raise ValueError('Brokerage per order must be a non-negative number.')
 if s['min_dte']<1 or s['max_dte']<s['min_dte']:raise ValueError('Expiry-day settlement is not supported; minimum DTE is 1.')
 if s['stop']<0 or s['target']<0:raise ValueError('Risk thresholds cannot be negative.')
 for leg in s['legs']:
  if leg['Side'] not in ('Buy','Sell') or leg['Type'] not in ('CE','PE'):raise ValueError('Each leg needs a side and option type.')
  if not math.isfinite(float(leg['Offset'])) or not 1<=int(leg['Ratio'])<=20 or float(leg['Ratio'])!=int(leg['Ratio']):raise ValueError('Invalid leg offset or ratio.')

def estimate_exchange_margin(legs, spot_price, symbol='NIFTY'):
 if not legs or spot_price <= 0: return 0.
 has_short = any(leg['side'] == -1 for leg in legs)
 if not has_short:
  return max(0., sum(leg['side'] * leg['entry'] * leg['quantity'] for leg in legs))
 psr = 0.10 if symbol == 'BANKNIFTY' else 0.08
 exposure_pct = 0.02
 net_calls = sum(leg['side'] * leg['quantity'] for leg in legs if leg['contract'].get('instrument_type') == 'CE')
 net_puts = sum(leg['side'] * leg['quantity'] for leg in legs if leg['contract'].get('instrument_type') == 'PE')
 is_defined_risk = (net_calls >= 0 and net_puts >= 0)
 strikes = [float(leg['contract'].get('strike_price', spot_price)) for leg in legs]
 test_points = set(strikes) | {spot_price * (1 + r) for r in (-0.15, -psr, -psr/2, 0, psr/2, psr, 0.15)}
 def portfolio_loss(s_star):
  pnl = 0.
  for leg in legs:
   k = float(leg['contract'].get('strike_price', spot_price))
   opt_type = leg['contract'].get('instrument_type')
   intr = max(0., s_star - k) if opt_type == 'CE' else max(0., k - s_star)
   pnl += leg['side'] * (intr - leg['entry']) * leg['quantity']
  return -pnl
 max_loss = max(0., max(portfolio_loss(pt) for pt in test_points))
 if is_defined_risk:
  exposure_buffer = sum(0.01 * spot_price * leg['quantity'] for leg in legs if leg['side'] == -1)
  return round(max_loss + exposure_buffer, 2)
 else:
  short_exposure = sum(exposure_pct * spot_price * leg['quantity'] for leg in legs if leg['side'] == -1)
  scan_loss = max(portfolio_loss(spot_price * (1 + r)) for r in (-psr, 0, psr))
  return round(max(max_loss, scan_loss) + short_exposure, 2)


def simulate(contracts,series,spotkey,s,signals=()):
 validate(s);trades=[];skips=[];spot=series.get(spotkey,{})
 sizing_mode,size=sizing(s);capital_allocated,margin_per_lot=capital_settings(s)
 catalogue=ContractIndex(contracts)
 if s.get('filters') and not isinstance(signals,SignalIndex):signals=SignalIndex(signals)
 days=sorted({x[:10] for x in spot if s['start']<=x[:10]<=s['end']} | {d for d in s.get('confirmed_sessions',[]) if s['start']<=d<=s['end']})
 for day in days:
  if pd.Timestamp(day).weekday() not in s['weekdays']:record_skip(skips,day,'Weekday excluded');continue
  entry=datetime.fromisoformat(day+' '+s['entry']);scheduled=datetime.fromisoformat(day+' '+s['exit'])
  stamp=lambda dt:dt.strftime('%Y-%m-%d %H:%M')
  prior=spot.get(stamp(entry-timedelta(minutes=1)))
  if not valid_price(prior,3):record_skip(skips,day,'Missing preceding spot close');continue
  day_date=entry.date()
  expiries=catalogue.eligible(day_date,s['min_dte'],s['max_dte']);rank=s.get('expiry_rank',0)
  if len(expiries)<=rank:record_skip(skips,day,'Selected expiry unavailable');continue
  expiry=expiries[rank]
  if s.get('filters'):
   ok,reason=decide(signals,expiry,day,s['entry'],s['filters'])
   if not ok:record_skip(skips,day,reason);continue
  strikes=catalogue.strikes(expiry)
  atm=min(strikes,key=lambda k:(abs(k-float(prior[3])),k))
  legs=[];bad=None
  for spec in s['legs']:
   target=atm+float(spec['Offset'])
   choices=catalogue.exact(expiry,spec['Type'],target)
   if len(choices)!=1:bad='Exact requested strike unavailable';break
   r=choices[0];bars=series.get(r['instrument_key'],{});bar=bars.get(stamp(entry))
   if not valid_price(bar,fill=True):bad='Missing or untraded entry candle';break
   ratio=int(spec['Ratio']);lot_size=broker_lot_size(r) if sizing_mode=='broker_lot_size' else None
   if sizing_mode=='broker_lot_size' and lot_size is None:bad='Missing or invalid broker lot size';break
   quantity=(size*ratio*lot_size) if lot_size is not None else size*ratio
   legs.append(dict(contract=r,bars=bars,entry=float(bar[0]),side=1 if spec['Side']=='Buy' else -1,quantity=quantity,ratio=ratio,lot_size=lot_size,strategy_lots=size if lot_size is not None else None))
  if bad:record_skip(skips,day,bad);continue
  if len({x['contract']['instrument_key'] for x in legs})!=len(legs):record_skip(skips,day,'Duplicate contract legs');continue
  entry_debit=sum(leg['side']*leg['entry']*(1+leg['side']*s['slippage_bps']/10000)*leg['quantity'] for leg in legs)
  has_short=any(leg['side']==-1 for leg in legs)
  if margin_per_lot>0:
   capital_required=margin_per_lot*size
   capital_source='Manual broker allocation'
  elif has_short and (s.get('estimate_margin') or s.get('margin_model')=='exchange_span_proxy'):
   capital_required=estimate_exchange_margin(legs,float(prior[3]),s.get('symbol','NIFTY'))
   capital_source='Exchange SPAN + Exposure proxy'
  elif has_short:
   capital_required=None
   capital_source='Broker margin not supplied'
  else:
   capital_required=max(0.,entry_debit)
   capital_source='Entry premium debit'
  if capital_allocated>0 and capital_required is not None and capital_required>capital_allocated+0.005:
   record_skip(skips,day,f'Capital requirement ₹{capital_required:,.0f} exceeds allocation ₹{capital_allocated:,.0f}');continue
  brokerage=s.get('brokerage_per_order',20)
  for leg in legs:
   leg['filled_entry']=leg['entry']*(1+leg['side']*s['slippage_bps']/10000)
  def pnl(prices,include_details=True):
   gross=slip=fees=0.;details=[]
   for leg,px in zip(legs,prices):
    q,side,a=leg['quantity'],leg['side'],leg['entry'];b=float(px)
    fa=leg['filled_entry'];fb=b*(1-side*s['slippage_bps']/10000)
    gross+=side*(b-a)*q;slip+=(a+b)*s['slippage_bps']/10000*q
    if 'entry_fee' not in leg:
     leg['entry_fee']=sum(charge(day,'Options',fa,q,'buy' if side==1 else 'sell',brokerage).values())
    fees+=leg['entry_fee']+sum(charge(day,'Options',fb,q,'sell' if side==1 else 'buy',brokerage).values())
    if include_details:details.append({'Contract':leg['contract']['trading_symbol'],'Side':'Buy' if side==1 else 'Sell','Lot size':leg['lot_size'],'Strategy lots':leg['strategy_lots'],'Ratio':leg['ratio'],'Quantity':q,'Entry':fa,'Exit':fb})
   return gross,slip,fees,details
  exit_at=scheduled;reason='Scheduled exit';signal_at=None
  if s['stop'] or s['target']:
   cursor=entry
   while cursor<scheduled:
    bars=[x['bars'].get(stamp(cursor)) for x in legs]
    if not all(valid_price(b,3) for b in bars):bad='Missing monitoring minute; risk path unknown';break
    gross,slip,fees,_=pnl([b[3] for b in bars],include_details=False);net=gross-slip-fees
    if s['stop'] and net<=-s['stop']:reason='Portfolio stop'
    elif s['target'] and net>=s['target']:reason='Portfolio target'
    else:cursor+=timedelta(minutes=1);continue
    signal_at=stamp(cursor);exit_at=cursor+timedelta(minutes=1);break
  if bad:record_skip(skips,day,bad);continue
  bars=[x['bars'].get(stamp(exit_at)) for x in legs]
  if not all(valid_price(b,fill=True) for b in bars):record_skip(skips,day,'Missing or untraded exit candle');continue
  gross,slip,fees,details=pnl([b[0] for b in bars]);net=gross-slip-fees
  trades.append({'Date':day,'Expiry':expiry,'Entry time':s['entry'],'Exit time':exit_at.strftime('%H:%M'),'Exit reason':reason,'Signal minute':signal_at,'ATM':atm,'Sizing':sizing_mode,'Strategy lots':size if sizing_mode=='broker_lot_size' else None,'Capital required':capital_required,'Capital source':capital_source,'Entry premium debit':entry_debit,'Net / capital %':(net/capital_required*100) if capital_required and capital_required>0 else None,'Gross':gross,'Slippage':slip,'Charges':fees,'Net':net,'Legs':json.dumps(details)})
 return pd.DataFrame(trades),pd.DataFrame(skips)
