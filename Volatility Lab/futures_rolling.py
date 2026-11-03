"""Three-year Upstox-backed futures history and strictly prior carry context."""
from contextlib import closing
from pathlib import Path
import json
import numpy as np
import pandas as pd
from futures_data import connection,contracts,timestamp,pair_values
from collector_support import serialized
from market_calendar import last_trading_day_on_or_before

VERSION='rolling-carry-v1'
LISTED_CALENDAR=Path(__file__).resolve().parent/'data'/'listed_monthly_expiries.json'
def monthly_expiry(symbol,month,as_of):
    month=pd.Timestamp(month).to_period('M');as_of=pd.Timestamp(as_of).normalize()
    if month>pd.Period('2026-12'):
        path=LISTED_CALENDAR
        known=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        item=known.get(symbol,{}).get(str(month))
        if item:return pd.Timestamp(item['expiry'])
        raise ValueError('Future monthly expiry is not yet in the verified contract calendar. Refresh contracts.')
    if month<pd.Period('2023-10'):raise ValueError('Historical expiry calendar starts October 2023.')
    weekday=3
    if symbol=='BANKNIFTY' and pd.Timestamp('2024-03-01')<=as_of<pd.Timestamp('2025-01-02'):weekday=2
    if month>=pd.Period('2025-09'):weekday=1
    day=month.end_time.normalize()
    while day.weekday()!=weekday:day-=pd.Timedelta(days=1)
    return pd.Timestamp(last_trading_day_on_or_before(day.date()))


def rank_expiries(symbol,day):
    day=pd.Timestamp(day).normalize();month=day.to_period('M')
    if day>monthly_expiry(symbol,month.start_time,day):month+=1
    return [monthly_expiry(symbol,(month+i).start_time,day) for i in range(3)]


def setup(root):
    con=connection(root)
    con.execute('CREATE TABLE IF NOT EXISTS continuous(symbol TEXT,rank INTEGER,day TEXT,close REAL,volume REAL,oi REAL,seed_token INTEGER,seed_expiry TEXT,fetched TEXT,PRIMARY KEY(symbol,rank,day))')
    return con


def load_roles(root,symbol):
    with closing(setup(root)) as con:
        return pd.read_sql_query('SELECT * FROM continuous WHERE symbol=? ORDER BY day,rank',con,params=(symbol,))


def rolling_frame(root,symbol):
    raw=load_roles(root,symbol);records=[];days={};expiry_cache={}
    with closing(setup(root)) as con:
        exists=con.execute("SELECT 1 FROM sqlite_master WHERE name='continuous_provenance'").fetchone()
        provenance={(r[0],r[1]):(r[2],r[3]) for r in con.execute('SELECT rank,day,expiry,provider FROM continuous_provenance WHERE symbol=?',(symbol,))} if exists else {}
    # Build each dated rank lookup once instead of repeatedly filtering pandas frames.
    for row in raw.itertuples(index=False):
        days.setdefault(row.day,{})[row.rank]=(float(row.close),float(row.volume))
    for day,legs in days.items():
        stamp=pd.Timestamp(day);month=stamp.to_period('M')
        # Expiry conventions change at these regime boundaries.
        regime=symbol=='BANKNIFTY' and pd.Timestamp('2024-03-01')<=stamp<pd.Timestamp('2025-01-02')
        def expiry(m):
            key=(m,regime)
            if key not in expiry_cache:expiry_cache[key]=monthly_expiry(symbol,m.start_time,stamp)
            return expiry_cache[key]
        try:
            if stamp>expiry(month):month+=1
            exps=[expiry(month+i) for i in range(3)]
        except ValueError:continue
        for rank,label in [(1,'Current - Next'),(2,'Next - Far')]:
            if rank not in legs or rank+1 not in legs:continue
            front,volume=legs[rank];back,back_volume=legs[rank+1]
            front_meta=provenance.get((rank,day));back_meta=provenance.get((rank+1,day))
            if front_meta and back_meta:exps[rank-1]=pd.Timestamp(front_meta[0]);exps[rank]=pd.Timestamp(back_meta[0])
            if min(volume,back_volume)<=0:continue
            records.append({'Date':stamp,'Pair':label,'Front expiry':str(exps[rank-1].date()),
                'Back expiry':str(exps[rank].date()),'Front':front,'Back':back,
                'DTE':(exps[rank-1]-stamp).days,
                **pair_values(front,back,exps[rank-1],exps[rank]),'Source':front_meta[1] if front_meta and back_meta else 'Saved rolling futures close'})
    return pd.DataFrame(records,columns=['Date','Pair','Front expiry','Back expiry','Front','Back','DTE','Spread','Annualized carry %','Expiry gap days','Source'])


def validate_overlap(root,symbol,listed,as_of):
    """Verify role interpretation against exact contracts in the current cycle."""
    expected=rank_expiries(symbol,pd.Timestamp(as_of).date())
    if [str(d.date()) for d in expected]!=[str(d) for d in listed.expiry]:
        raise ValueError('Listed expiry dates disagree with the historical calendar; rolling carry withheld.')
    raw=load_roles(root,symbol);checks=[]
    with closing(setup(root)) as con:
        for rank,row in enumerate(listed.itertuples(),1):
            dated=pd.read_sql_query('SELECT day,close FROM candles WHERE symbol=? AND expiry=?',con,params=(symbol,str(row.expiry)))
            joined=raw[raw['rank']==rank].merge(dated,on='day',suffixes=('_continuous','_dated'))
            # A given dated contract held a different rank before the latest roll.
            joined=joined[[str(rank_expiries(symbol,d)[rank-1].date())==str(row.expiry) for d in joined.day]]
            if joined.empty:raise ValueError('Need matching dated and rolling candles to verify all three futures ranks. Refresh futures first.')
            diff=(joined.close_continuous-joined.close_dated).abs()
            if (diff>max(float(getattr(row,'tick_size',.1)),.05)+1e-6).any():
                raise ValueError(f'Rolling rank {rank} differs from dated closes; comparison withheld pending review.')
            checks.append({'Rank':rank,'Matched dates':len(joined),'Max difference':float(diff.max())})
    return checks


@serialized
def fetch_rolling(engine,client,root,symbol,now=None,progress=None,start_date=None,force=False):
    if getattr(client,'provider',None)!='Upstox':
        raise ValueError('Upstox read-only market-data client required.')
    from upstox_rolling import append_dated
    return append_dated(engine,client,root,symbol,now)


def carry_context(frame,current,years=3):
    """Strictly earlier dates, same role, +/-3 front-DTE and +/-3 expiry-gap days."""
    result={'Current carry %':current.get('Annualized carry %'),'Prior sessions':0,'Expiry cycles':0,
            'Median carry %':None,'10th percentile %':None,'90th percentile %':None,'Carry percentile':None,
            'Z-score':None,'Status':'Insufficient comparable history'}
    value=current.get('Annualized carry %')
    if value is None or not np.isfinite(value) or not current.get('As of'):return result
    day=timestamp(current['As of']).tz_localize(None).normalize()
    dte=(pd.Timestamp(current['Front expiry'])-day).days;gap=(pd.Timestamp(current['Back expiry'])-pd.Timestamp(current['Front expiry'])).days
    if frame.empty:return result
    selected=frame[(frame.Pair==current['Pair'])&(frame.Date<day)&(frame.Date>=day-pd.DateOffset(years=years))&
        ((frame.DTE-dte).abs()<=3)&((frame['Expiry gap days']-gap).abs()<=3)].copy()
    selected=selected[np.isfinite(selected['Annualized carry %'])].drop_duplicates('Date')
    values=selected['Annualized carry %'];cycles=selected['Front expiry'].nunique()
    result.update({'Prior sessions':len(selected),'Expiry cycles':cycles})
    if len(values)<60 or cycles<12:return result
    median=float(values.median());sd=float(values.std(ddof=1))
    result.update({'Median carry %':median,'10th percentile %':float(values.quantile(.1)),
        '90th percentile %':float(values.quantile(.9)),'Carry percentile':float(100*((values<value).sum()+.5*(values==value).sum())/len(values)),
        'Z-score':float((value-values.mean())/sd) if sd>1e-12 else None,'Status':'Descriptive comparison; not a trade signal'})
    return result


def remember_listed_expiries(master,as_of):
    """Only FUT metadata defines monthly expiry; retain provenance, never infer holidays."""
    from collector_support import atomic_json
    path=LISTED_CALENDAR
    known=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for symbol in ('NIFTY','BANKNIFTY'):
        rows=master[(master.name==symbol)&(master.instrument_type=='FUT')]
        for month,group in rows.groupby(pd.to_datetime(rows.expiry).dt.to_period('M')):
            dates=pd.to_datetime(group.expiry).dt.strftime('%Y-%m-%d').unique()
            if len(dates)!=1:continue
            known.setdefault(symbol,{})[str(month)]={'expiry':dates[0],'observed':str(as_of),'source':'Upstox NFO FUT master'}
    atomic_json(path,known)
