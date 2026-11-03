"""Read-only short-volatility diagnostics. No order routing or trade recommendations."""
from datetime import time
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from option_math import price_delta
from quote_quality import assess_quote, timestamp, number
from vol_lib import validate_ohlc

VERSION='trade-review-v1'


def relative_spread_history(data, session_dates):
    """Relative IV/HV difference, five complete sessions; never bridge missing data."""
    if data.empty:return data.assign(**{'Relative spread · 5-session %':np.nan})
    dates=pd.DatetimeIndex(session_dates).union(pd.DatetimeIndex(data.Date)).sort_values()
    out=data.set_index('Date').reindex(dates).rename_axis('Date').reset_index()
    hv=out.get('Historical vol 30D',pd.Series(np.nan,index=out.index))
    ratio=100*(out.IV-hv)/hv.where(hv>0)
    out['Relative spread · 5-session %']=ratio.rolling(5,min_periods=5).mean()
    if 'Source' in out:
        same_source=out.Source.notna()
        for offset in range(1,5):same_source&=out.Source.eq(out.Source.shift(offset))
        out.loc[~same_source,'Relative spread · 5-session %']=np.nan
    return out


def archive_quotes(root, symbol, expiry, payload):
    """Content-addressed originals remain available after the latest file changes."""
    raw=json.dumps(payload,default=str,sort_keys=True).encode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    folder=Path(root)/'quote_archive'/symbol/str(expiry)
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/(digest+'.json')
    if not target.exists():target.write_bytes(raw)
    return digest


def option_book(payload, expiry, rate=.065, dividend=0., max_spread=3.):
    """Recompute strictly at the stored snapshot time, never call saved quotes live."""
    stamp=timestamp(payload['valuation']);spot=float(payload['spot'])
    end=timestamp(str(expiry)+'T15:40:00+05:30')
    years=(end-stamp).total_seconds()/(365*86400)
    if not np.isfinite([spot,rate,dividend,max_spread]).all() or spot<=0 or years<=0:
        raise ValueError('Invalid or expired option snapshot.')
    contracts=pd.DataFrame(payload['contracts'])
    required={'strike','instrument_type','tradingsymbol','expiry','lot_size'}
    if not required.issubset(contracts):raise ValueError('Contract metadata is incomplete.')
    if contracts.empty:raise ValueError('No contracts in this snapshot.')
    if not (pd.to_datetime(contracts.expiry).dt.date==end.date()).all():
        raise ValueError('Mixed expiry contracts in snapshot.')
    if contracts.duplicated(['strike','instrument_type']).any():
        raise ValueError('Ambiguous contracts at a strike.')
    if payload.get('provider')!='Upstox':
        raise ValueError('Trade review requires captured Upstox broker IV; price-based IV reconstruction is disabled.')
    closing=bool(payload.get('closing',stamp.time()>=time(15,40)))
    broker={}
    for item in payload.get('broker_option_chain',[]):
        for side,kind in [('call_options','CE'),('put_options','PE')]:
            broker[(float(item['strike_price']),kind)]=(item.get(side) or {}).get('option_greeks',{})
    rows=[]
    for contract in contracts.to_dict('records'):
        quote=payload.get('quotes',{}).get('NFO:'+contract['tradingsymbol'],{})
        quality=assess_quote(quote,stamp,max_spread,closing=closing)
        # LTP-only prices cannot support a bid-entry / ask-exit cost calculation.
        if not quality['accepted'] or quality['source']!='Bid/ask midpoint':continue
        strike=float(contract['strike']);kind=contract['instrument_type']
        if kind not in ('CE','PE'):continue
        greeks=broker.get((strike,kind),{})
        supplied=number(greeks.get('iv'))
        if supplied is None or supplied<=0:continue
        vols=[np.nan,supplied/100,np.nan]
        lot=number(contract['lot_size'])
        if lot is None or lot<=0 or lot!=int(lot):continue
        depth=quote['depth']
        rows.append({'Strike':strike,'Kind':kind,'Bid':quality['bid'],'Mid':quality['mid'],
                     'Ask':quality['ask'],**{k:number(greeks.get(k.lower())) for k in ['Delta','Gamma','Theta','Vega']},'Bid IV':100*vols[0],'IV':100*vols[1],'Ask IV':100*vols[2],
                     'Lot':int(lot),'Bid quantity':float(depth['buy'][0]['quantity']),
                     'Ask quantity':float(depth['sell'][0]['quantity']),
                     'Quote age · sec':quality['quote_age_seconds'],
                     'Quote time':str(quality['quote_timestamp'])})
    columns=['Strike','Kind','Bid','Mid','Ask','Bid IV','IV','Ask IV','Delta','Gamma','Theta','Vega','Lot',
             'Bid quantity','Ask quantity','Quote age · sec','Quote time']
    book=pd.DataFrame(rows,columns=columns)
    strike=min(contracts.strike.astype(float).unique(),key=lambda k:(abs(k-spot),k))
    return {'book':book,'spot':spot,'years':years,'stamp':stamp,'closing':closing,
            'strike':strike,'rate':rate,'dividend':dividend,'broker_greeks':True}


def atm_pair(context):
    pair=context['book'][context['book'].Strike==context['strike']]
    if len(pair)!=2 or set(pair.Kind)!= {'CE','PE'}:
        raise ValueError('Both options at the exact spot-ATM strike need valid two-sided quotes.')
    if pair.Lot.nunique()!=1:raise ValueError('Call and put lot sizes differ.')
    return pair.copy()


def freshness(context, now, spot_quote=None):
    age=(timestamp(now)-context['stamp']).total_seconds()
    if context['closing']:return 'Closing estimate'
    if not 0<=age<=60:return 'Saved snapshot · refresh required'
    if spot_quote is None:return 'Spot timestamp unavailable · refresh required'
    try:
        spot_age=(timestamp(now)-timestamp(spot_quote.get('timestamp'))).total_seconds()
        pair=atm_pair(context)
        ages=[(timestamp(now)-timestamp(x)).total_seconds() for x in pair['Quote time']]
        if not all(0<=x<=60 for x in [spot_age,*ages]):return 'Stale or unsynchronised quotes'
        if any(pair['Bid quantity']<pair.Lot) or any(pair['Ask quantity']<pair.Lot):
            return 'Top-of-book depth below one lot'
    except (ValueError,TypeError):return 'Quote quality incomplete'
    return 'Recent two-sided snapshot · fills not guaranteed'


def parity_forward(context):
    """Near-ATM put/call parity consistency, using synchronized quote pairs."""
    book=context['book'];spot=context['spot'];values=[]
    for strike,group in book.groupby('Strike'):
        if abs(strike/spot-1)>.03 or len(group)!=2 or set(group.Kind)!={'CE','PE'}:continue
        ages=group['Quote age · sec'].to_numpy(float)
        if not np.isfinite(ages).all() or np.max(ages)-np.min(ages)>30:continue
        sides=group.set_index('Kind')
        values.append(strike+np.exp(context['rate']*context['years'])*(sides.loc['CE','Mid']-sides.loc['PE','Mid']))
    if len(values)<3:return None
    forward=float(np.median(values))
    assumed=spot*np.exp((context['rate']-context['dividend'])*context['years'])
    return {'Paired strikes':len(values),'Parity forward':forward,
            'Assumed-carry forward':assumed,'Difference · points':forward-assumed,
            'Pair range · points':float(np.ptp(values))}


def gap_summary(frame, as_of):
    stamp=timestamp(as_of)
    data=validate_ohlc(frame)
    cutoff=stamp.normalize().tz_localize(None)
    # Always prior sessions: stable and conservative even immediately after close.
    data=data[data.index<cutoff]
    start=cutoff-pd.DateOffset(years=1)
    gap=(data.Open/data.Close.shift()-1)*100
    daily=(data.Close/data.Close.shift()-1)*100
    intraday=(data.High-data.Low)/data.Open*100
    records=[]
    for label,series in [('Overnight gap',gap),('Close-to-close move',daily),('Intraday range',intraday)]:
        series=series[series.index>=start].dropna()
        if len(series)<30:raise ValueError('At least 30 completed observations are required for gap review.')
        records.append({'Measure':label,'Observations':len(series),'Median absolute %':series.abs().median(),
                        '95th absolute %':series.abs().quantile(.95),'Worst absolute %':series.abs().max(),
                        'Worst date':str(series.abs().idxmax().date()),'Through':str(series.index.max().date())})
    return pd.DataFrame(records)


def make_position(context, put_wing=None, call_wing=None):
    """One short ATM pair; optional one lower put and one higher call, same expiry."""
    pair=atm_pair(context);pair['Position']=-1
    if (put_wing is None)!=(call_wing is None):raise ValueError('Both protective wings are required.')
    if put_wing is not None:
        if not put_wing<context['strike']<call_wing:raise ValueError('Wings must surround the ATM strike.')
        for kind,strike in [('PE',put_wing),('CE',call_wing)]:
            wing=context['book'][(context['book'].Kind==kind)&(context['book'].Strike==strike)].copy()
            if len(wing)!=1:raise ValueError('Protective wing lacks a valid two-sided quote.')
            wing['Position']=1
            pair=pd.concat([pair,wing],ignore_index=True)
    if pair.Lot.nunique()!=1:raise ValueError('All legs must have the same lot size.')
    pair['Entry']=np.where(pair.Position<0,pair.Bid,pair.Ask)
    return pair


def position_greeks(context, position):
    """Signed one-lot spot BS Greeks; theta is an instantaneous calendar-day rate."""
    if context.get('broker_greeks'):
        weights=position.Position*position.Lot
        def total(column):
            values=pd.to_numeric(position[column],errors='coerce')
            return float((values*weights).sum()) if values.notna().all() else np.nan
        return {'Delta · units':total('Delta'),'Gamma Δ change / 1% spot':total('Gamma')*context['spot']*.01,
                'Vega · ₹ / IV pp':total('Vega'),'Theta · ₹ / calendar day':total('Theta')}
    from scipy.stats import norm
    s=context['spot'];t=context['years'];r=context['rate'];q=context['dividend']
    delta=gamma=vega=theta=0.
    for leg in position.itertuples():
        v=leg.IV/100;k=leg.Strike;size=leg.Position*leg.Lot
        d1=(np.log(s/k)+(r-q+.5*v*v)*t)/(v*np.sqrt(t));d2=d1-v*np.sqrt(t)
        density=norm.pdf(d1);ds=np.exp(-q*t);dk=np.exp(-r*t)
        delta+=size*price_delta(s,k,t,r,v,leg.Kind,q)[1]
        gamma+=size*ds*density/(s*v*np.sqrt(t))
        vega+=size*s*ds*density*np.sqrt(t)/100
        decay=-s*ds*density*v/(2*np.sqrt(t))
        decay+=(q*s*ds*norm.cdf(d1)-r*k*dk*norm.cdf(d2)) if leg.Kind=='CE' else (r*k*dk*norm.cdf(-d2)-q*s*ds*norm.cdf(-d1))
        theta+=size*decay/365
    return {'Delta · units':delta,'Gamma Δ change / 1% spot':gamma*s*.01,
            'Vega · ₹ / IV pp':vega,'Theta · ₹ / calendar day':theta}


def stress_position(context, position, elapsed_days=1., costs=0.):
    """Sticky-strike IV shocks, current half-spread exit penalty, frozen spot hedge.

    P&L includes bid/ask entry and model-mid exit with today's half spread per leg.
    The hedge is continuous spot-equivalent units, NOT tradable futures lots.
    """
    if not np.isfinite([elapsed_days,costs]).all() or elapsed_days<0 or costs<0:
        raise ValueError('Time and total additional rupee costs must be nonnegative.')
    lot=int(position.Lot.iloc[0]);remaining=max(0.,context['years']-elapsed_days/365)
    spot=context['spot'];rate=context['rate'];dividend=context['dividend']
    initial_delta=sum(r.Position*price_delta(spot,r.Strike,context['years'],rate,r.IV/100,r.Kind,dividend)[1]
                      for r in position.itertuples())*lot
    if context.get('broker_greeks'):initial_delta=position_greeks(context,position)['Delta · units']
    credit=-float((position.Position*position.Entry*lot).sum())
    spread_cost=float(((position.Ask-position.Bid)*lot).sum())
    records=[]
    for move in (-5.,-3.,-2.,-1.,0.,1.,2.,3.,5.):
        shocked=spot*(1+move/100)
        for bump in (0.,3.,5.,10.):
            pnl=credit-costs
            for r in position.itertuples():
                if remaining==0:
                    value=max(0.,shocked-r.Strike) if r.Kind=='CE' else max(0.,r.Strike-shocked)
                else:
                    mid=price_delta(shocked,r.Strike,remaining,rate,(r.IV+bump)/100,r.Kind,dividend)[0]
                    # Closing a short pays ask; selling a long receives bid.
                    value=max(0.,mid-r.Position*(r.Ask-r.Bid)/2)
                pnl+=r.Position*value*lot
            records.append({'Spot move %':move,'IV shock · pp':bump,'Unhedged P&L · ₹':pnl,
                            'Frozen delta hedge P&L · ₹':pnl-initial_delta*(shocked-spot)})
    protected=(position.Position>0).any()
    max_loss=None
    if protected:
        low=position.loc[(position.Position>0)&(position.Kind=='PE'),'Strike'].iloc[0]
        high=position.loc[(position.Position>0)&(position.Kind=='CE'),'Strike'].iloc[0]
        max_loss=max(context['strike']-low,high-context['strike'])*lot-credit+costs
    return pd.DataFrame(records),{'Entry credit · ₹':credit,'Unchanged-book round trip · ₹':spread_cost+costs,
        'Initial option delta · units':initial_delta,'Frozen spot hedge · units':-initial_delta,
        'Expiry max loss · ₹':max_loss,'Lot size':lot}
