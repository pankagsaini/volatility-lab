"""Upstox market-data adapter for the existing read-only calculation engines."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from datetime import datetime,timedelta
from pathlib import Path
import hashlib,re
import pandas as pd
from collector_support import read_json
from market_data import IST,MarketDataError,history_cutoff,candles_to_frame
from upstox_data import UpstoxDataClient,saved_client,INDICES

ROOT=PACKAGE_ROOT
CACHE=runtime_data_dir()/'upstox'
NAMES={'NIFTY':'NIFTY 50','BANKNIFTY':'NIFTY BANK'}


def catalogue_due(data,now):
    """Recheck listed contracts hourly, including new listings within the same day."""
    if data.get('date')!=str(now.astimezone(IST).date()):return True
    try:
        stamp=datetime.fromisoformat(data['fetched_at'])
        if stamp.tzinfo is None:return True
        age=(now-stamp).total_seconds()
        return not 0<=age<3600
    except (KeyError,ValueError,TypeError):return True

def token_id(key):
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:6],'big')

def provider_timestamp(value):
    """Upstox feed time is ISO-8601; last trade is epoch milliseconds."""
    try:
        if value is None or value=='':return None
        stamp=(pd.to_datetime(int(value),unit='ms',utc=True) if isinstance(value,(int,float)) or str(value).isdigit() else pd.Timestamp(value))
        if pd.isna(stamp) or stamp.tzinfo is None:return None
        return stamp.tz_convert(IST).isoformat()
    except (ValueError,TypeError,OverflowError):return None

def market_client():
    base=saved_client()
    return UpstoxAdapter(base._token)

class UpstoxAdapter(UpstoxDataClient):
    provider='Upstox'
    quote_batch_size=500
    @property
    def authenticated(self):return bool(self._token)

    def master(self):
        now=datetime.now(IST);path=CACHE/'master.json';data=read_json(path)
        if catalogue_due(data,now):
            from catalogue_fetch import refresh
            data=refresh(self._opener,path,str(now.date()),INDICES)
        records=[]
        for r in data['instruments']:
            key=r['instrument_key'];index=key.startswith('NSE_INDEX|')
            name=next((s for s,k in INDICES.items() if k==key),None)
            trading=NAMES.get(name,'INDIA VIX') if index else r['trading_symbol']
            expiry=pd.to_datetime(r['expiry'],unit='ms',utc=True).tz_convert(IST).date() if r.get('expiry') else None
            records.append(dict(instrument_token=token_id(key),instrument_key=key,tradingsymbol=trading,name=name if index else r['underlying_symbol'],expiry=expiry,strike=r.get('strike_price',0),lot_size=r.get('lot_size',1),instrument_type=r['instrument_type'],segment='INDICES' if index else 'NFO',exchange='NSE' if index else 'NFO',provider='Upstox'))
        self._master=pd.DataFrame(records)
        self._bytoken={int(r.instrument_token):r.instrument_key for r in self._master.itertuples()}
        self._byname={r.exchange+':'+r.tradingsymbol:r.instrument_key for r in self._master.itertuples()}
        return self._master

    def _request(self,path,params=None,form=None,csv_response=False):
        if form is not None:raise MarketDataError('Trading and authentication mutations are not supported.')
        if not hasattr(self,'_master'):self.master()
        if path in ('/instruments/NFO','/instruments/NSE'):
            self.master()
            return self._master[self._master.exchange==path.rsplit('/',1)[1]].to_csv(index=False)
        if path=='/quote':
            names=[v for k,v in params if k=='i'];keys=[self._byname[n] for n in names if n in self._byname]
            raw=self.quotes(keys);bykey={r['instrument_token']:r for r in raw.values()};out={}
            for name in names:
                q=bykey.get(self._byname.get(name));
                if q is None:continue
                q=dict(q);q['provider']='Upstox';q['retrieved_at']=datetime.now(IST).isoformat()
                # Feed freshness applies to depth; trade freshness applies only to LTP.
                q['timestamp']=provider_timestamp(q.get('timestamp'))
                q['last_trade_time']=provider_timestamp(q.get('last_trade_time'))
                q['timestamp_basis']='Upstox feed-update time; last-trade time retained separately'
                if q.get('net_change') is not None and q.get('last_price') is not None:
                    try:q['ohlc']={**(q.get('ohlc') or {}),'close':float(q['last_price'])-float(q['net_change'])}
                    except (ValueError,TypeError):pass
                out[name]=q
            return out
        match=re.fullmatch(r'/instruments/historical/(\d+)/day',path)
        if match:
            if params.get('continuous'):raise MarketDataError('Upstox supports dated contracts; continuous history is not available from this adapter.')
            key=self._bytoken[int(match[1])]
            return self.daily(key,str(params['from'])[:10],str(params['to'])[:10])
        raise MarketDataError('Unsupported read-only adapter endpoint.')

    def daily_history(self,instrument_token,now=None,start_date=None):
        now=now or datetime.now(IST);end=history_cutoff(now)
        start=pd.Timestamp(start_date).date() if start_date else end-timedelta(days=729)
        requested_start=start
        if not hasattr(self,'_bytoken'):self.master()
        key=self._bytoken[int(instrument_token)]
        records=[]
        while start<=end:
            stop=min(start+timedelta(days=299),end)
            records.extend(self.daily(key,start,stop).get('candles',[]));start=stop+timedelta(days=1)
        frame=candles_to_frame(records,end) if records else pd.DataFrame(columns=['Date','Open','High','Low','Close'])
        # Historical candles can lag the completed trading session. The current-day
        # endpoint supplies that day's OHLC; never admit its still-forming bar.
        if requested_start<=end==now.astimezone(IST).date() and (frame.empty or frame.Date.iloc[-1].date()<end):
            try:
                current=candles_to_frame(self.intraday_daily(key).get('candles',[]),end)
                current=current[current.Date.dt.date==end]
                if not current.empty:
                    frame=pd.concat([frame,current],ignore_index=True).sort_values('Date').reset_index(drop=True)
                    frame.attrs['current_day_source']='Upstox intraday days/1 after 16:30 IST'
            except (MarketDataError,ValueError,TypeError):
                # Retain usable history; Monitor retries an absent close next time.
                pass
        if frame.empty:raise MarketDataError('Provider returned no completed daily candles.')
        return frame
