"""Shared index-price and realized-volatility helpers for the broker IV monitor."""
from datetime import datetime, time, timedelta
import io
from pathlib import Path
import time as clock
import numpy as np
import pandas as pd
from forecast import fit_garch
from market_data import IST, MarketDataError, history_cutoff
from vol_lib import validate_ohlc
from market_calendar import HOLIDAYS,last_trading_day_on_or_before

INDICES={'NIFTY':'NIFTY 50','BANKNIFTY':'NIFTY BANK'}
COLUMNS=['Price','% change','Call IV','Put IV','IV','25 Delta reversal','IV-RV Spread']
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'


def one_year(frame,as_of):
    df=validate_ohlc(frame).reset_index()
    end=pd.Timestamp(as_of).normalize()
    return df[(df.Date> end-pd.DateOffset(years=1)) & (df.Date<=end)].copy()


def historical_vol(frame):
    df=validate_ohlc(frame)
    returns=np.log(df.Close/df.Close.shift())
    return pd.DataFrame({'Date':df.index,'Price':df.Close.to_numpy(),
                         'Historical vol 10D':returns.rolling(10).std().to_numpy()*np.sqrt(245)*100,
                         'Historical vol 30D':returns.rolling(30).std().to_numpy()*np.sqrt(245)*100})












def select_expiry(master,symbol,now,policy='Monthly, roll 7 days early'):
    sub=master[master.name==symbol]
    option_dates=set(sub.loc[sub.instrument_type.isin(['CE','PE']),'expiry'])
    if policy.startswith('Monthly'):
        # Monthly futures identify monthly option dates, avoiding weekly guesses.
        dates=option_dates.intersection(set(sub.loc[sub.instrument_type=='FUT','expiry']))
        dates=[d for d in dates if (d-now.date()).days>7]
    else:dates=option_dates
    valid=sorted(d for d in dates if datetime.combine(d,time(15,40),tzinfo=IST)>now)
    if not valid:raise MarketDataError('No eligible listed expiry for the chosen policy.')
    return valid[0]


def fresh(quote,now):
    try:
        stamp=pd.Timestamp(quote.get('timestamp'))
        if pd.isna(stamp):return False
        if stamp.tzinfo is None:stamp=stamp.tz_localize(IST)
        return -5 <= (pd.Timestamp(now)-stamp).total_seconds() <= 60
    except (TypeError,ValueError):return False


def quote_time(quote):
    stamp=pd.Timestamp(quote.get('timestamp'))
    if pd.isna(stamp):raise MarketDataError('Quote timestamp unavailable.')
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def valuation_time(spotq,now):
    """Live during session; explicitly dated last-session snapshot outside hours."""
    stamp=quote_time(spotq)
    if now.date() in HOLIDAYS or now.weekday()>4:
        from upstox_adapter import provider_timestamp
        trade=provider_timestamp(spotq.get('last_trade_time'))
        if not trade:raise MarketDataError('No dated last-session trade on market holiday.')
        previous=pd.Timestamp(trade)
        if previous.date()>=now.date() or (pd.Timestamp(now)-previous).total_seconds()>7*86400:
            raise MarketDataError('No recent completed-session trade.')
        return previous.to_pydatetime(),True
    if now.weekday()<5 and time(9,15)<=now.time().replace(tzinfo=None)<=time(15,40):
        if not fresh(spotq,now):raise MarketDataError('Stale spot quote during market hours.')
        return now,False
    age=(pd.Timestamp(now)-stamp).total_seconds()
    if age < -60 or age>7*86400:raise MarketDataError('No recent completed-session quote.')
    close=datetime.combine(stamp.date(),time(15,40),tzinfo=IST)
    if stamp.to_pydatetime()<close-timedelta(minutes=5):
        raise MarketDataError('After-market spot quote is not from the closing session.')
    return close,True




def atm_values(chain,strikes,spot):
    if not len(strikes):return np.nan,np.nan,np.nan
    strike=min(strikes,key=lambda k:(abs(k-spot),k))
    vals=[]
    for kind in ['CE','PE']:
        side=chain[(chain.strike==strike)&(chain.option_type==kind)]
        v = float(side.iv.iloc[0])*100 if len(side) and np.isfinite(side.iv.iloc[0]) and side.iv.iloc[0]>0 else np.nan
        vals.append(v)
    valid_vals = [v for v in vals if np.isfinite(v) and v > 0]
    return vals[0],vals[1],float(np.mean(valid_vals)) if valid_vals else np.nan


class Monitor:
    def __init__(self):
        self.master_date=None;self.nfo=None;self.nse=None;self.histories={};self.paths={}
        self.last_request=0.

    def request(self,client,*args,**kwargs):
        clock.sleep(max(0,1.05-(clock.monotonic()-self.last_request)))
        self.last_request=clock.monotonic()
        return client._request(*args,**kwargs)

    def masters(self,client,now):
        last=getattr(self,'master_checked_at',None)
        if getattr(self,'master_provider',None)!=getattr(client,'provider','Upstox') or self.master_date!=now.date() or last is None or (now-last).total_seconds()>=3600:
            nfo=pd.read_csv(io.StringIO(self.request(client,'/instruments/NFO',csv_response=True)))
            nse=pd.read_csv(io.StringIO(self.request(client,'/instruments/NSE',csv_response=True)))
            nfo['expiry']=pd.to_datetime(nfo.expiry,errors='coerce').dt.date
            from futures_rolling import remember_listed_expiries
            remember_listed_expiries(nfo,now)
            self.master_provider=getattr(client,'provider','Upstox');self.nfo=nfo;self.nse=nse;self.master_date=now.date();self.master_checked_at=now

    def history(self,client,symbol,now):
        from collector_support import atomic_csv
        key=(symbol,history_cutoff(now))
        expected=last_trading_day_on_or_before(key[1])
        # A missing close must be retried on the next refresh, not cached all day.
        if key in self.histories and self.histories[key].Date.iloc[-1].date()>=expected:return self.histories[key]
        archive=DATA/'underlying_history'
        files=sorted(archive.glob(symbol.lower()+'_*.csv'))
        saved=None
        if files:
            try:
                saved=validate_ohlc(pd.read_csv(files[-1],parse_dates=['Date'])).reset_index()
                saved=saved[saved.Date<=pd.Timestamp(key[1])]
                view=one_year(saved,key[1])
                if len(view)>=201 and view.Date.iloc[-1].date()>=expected:
                    atomic_csv(view,DATA/f'{symbol.lower()}_one_year.csv',index=False,date_format='%Y-%m-%d')
                    self.histories[key]=view
                    return view
            except (OSError,ValueError,KeyError):saved=None
        matches=self.nse[(self.nse.tradingsymbol==INDICES[symbol])&(self.nse.segment=='INDICES')]
        if len(matches)!=1:raise MarketDataError('Index history instrument could not be resolved.')
        token=int(matches.instrument_token.iloc[0])
        if saved is not None and len(saved)>=201:
            recent=client.daily_history(token,now,start_date=saved.Date.iloc[-1].date()-timedelta(days=5))
            frame=pd.concat([saved,recent]).drop_duplicates('Date',keep='last').sort_values('Date').reset_index(drop=True)
            frame=frame[frame.Date>=pd.Timestamp(key[1])-pd.Timedelta(days=729)]
        else:frame=client.daily_history(token,now)
        full_history=frame.copy()
        frame=one_year(frame,history_cutoff(now))
        if len(frame)<201:raise MarketDataError('Fewer than 201 daily candles in the one-year window.')
        if (now.date()-frame.Date.iloc[-1].date()).days>7:raise MarketDataError('Historical candles are stale.')
        DATA.mkdir(exist_ok=True)
        # Retain the acquisition separately; existing one-year UI stays unchanged.
        archive=DATA/'underlying_history'
        archive.mkdir(exist_ok=True)
        full_history.to_csv(archive/f'{symbol.lower()}_{now.strftime("%Y%m%dT%H%M%S%f")}.csv',
                            index=False,date_format='%Y-%m-%d')
        atomic_csv(frame,DATA/f'{symbol.lower()}_one_year.csv',index=False,date_format='%Y-%m-%d')
        self.histories[key]=frame
        return frame

    def rv(self,frame,symbol,now,expiry,method):
        # Full future weekdays from the last completed candle. Skip elapsed dates;
        # remaining current-session variance is prorated by regular session time.
        dates=pd.bdate_range(frame.Date.iloc[-1]+pd.Timedelta(days=1),pd.Timestamp(expiry))
        if not len(dates) or len(dates)>245:raise MarketDataError('Unsupported forecast tenor.')
        key=(symbol,frame.Date.iloc[-1].isoformat(),len(frame),method,len(dates))
        if key not in self.paths:
            if method=='GARCH':
                _,_,path,_=fit_garch(frame,(len(dates),),min_observations=200)
                variances=(path['Daily conditional vol %'].to_numpy()/100)**2/245
            else:
                returns=np.log(frame.Close/frame.Close.shift()).dropna()
                variances=np.full(len(dates),returns.tail(30).var())
            self.paths[key]=variances
        weights=np.array([0. if d.date()<now.date() else 1. for d in dates])
        for i,d in enumerate(dates):
            if d.date()==now.date():
                opening=datetime.combine(now.date(),time(9,15),tzinfo=IST)
                closing=datetime.combine(now.date(),time(15,40),tzinfo=IST)
                weights[i]=np.clip((closing-now).total_seconds()/(closing-opening).total_seconds(),0,1)
        years=(datetime.combine(expiry,time(15,40),tzinfo=IST)-now).total_seconds()/(365*86400)
        return float(np.sqrt(np.sum(self.paths[key]*weights)/years)*100)





def chart_iv_spread(history,hv):
    """Closing observations require same-date HV; live observations use prior HV.

    Source-less observations retain the legacy prior-session convention.
    Unknown/imported observations also use prior data to prevent lookahead.
    """
    result=history.copy().sort_values('Date').reset_index(drop=True)
    result['Date']=pd.to_datetime(result.Date).astype('datetime64[ns]')
    result['Historical vol 30D']=np.nan
    result['RV through']=pd.NaT
    result['RV status']='Historical candles unavailable'
    if hv is None or hv.empty:
        result['IV-RV Spread']=np.nan
        return result
    reference=hv[['Date','Historical vol 30D']].dropna().copy().sort_values('Date')
    reference['Date']=pd.to_datetime(reference.Date).astype('datetime64[ns]')
    for i,row in result.iterrows():
        source=str(row.get('Source','')).lower()
        closing=any(label in source for label in ('closing','daily-close','after-market','mixed midpoint / ltp'))
        candidates=reference[reference.Date.eq(row.Date)] if closing else reference[reference.Date.lt(row.Date)]
        if candidates.empty:
            result.at[i,'RV status']='Closing candle missing' if closing else 'Prior completed candle missing'
            continue
        selected=candidates.iloc[-1]
        # Reject missing intervening sessions using the verified exchange calendar.
        try:
            from expiry_forecast import sessions
            end=row.Date.date()-timedelta(days=1)
            if not closing and sessions(selected.Date.date()+timedelta(days=1),end):
                result.at[i,'RV status']='Prior completed candle missing';continue
        except ValueError:
            result.at[i,'RV status']='Trading calendar unavailable';continue
        result.at[i,'Historical vol 30D']=selected['Historical vol 30D']
        result.at[i,'RV through']=selected.Date
        result.at[i,'RV status']='Same-session close' if closing else 'Prior completed session'
    result['IV-RV Spread']=result.IV-result['Historical vol 30D']
    return result


def aligned_spread(iv, valuation, source, frame):
    sample=pd.DataFrame({'Date':[pd.Timestamp(valuation).tz_localize(None).normalize()], 'IV':[iv], 'Source':[source]})
    out=chart_iv_spread(sample,historical_vol(frame) if frame is not None else None).iloc[0]
    return {'IV-RV Spread':out['IV-RV Spread'], 'HV30':out['Historical vol 30D'],
            'RV through':None if pd.isna(out['RV through']) else str(out['RV through'].date()),
            'RV status':out['RV status']}
