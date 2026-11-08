"""Upstox-backed, dated futures calendar spreads. No synthetic prices."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import sqlite3
import numpy as np
import pandas as pd
from market_data import IST, history_cutoff, MarketDataError
from market_table import Monitor
from market_calendar import HOLIDAYS,SPECIAL_SESSIONS,last_trading_day_on_or_before
from collector_support import serialized,atomic_csv


def contracts(master, symbol, now):
    rows=master[(master.name==symbol)&(master.instrument_type=='FUT')].copy()
    rows['expiry']=pd.to_datetime(rows.expiry).dt.date
    rows=rows[rows.expiry>=now.date()].sort_values('expiry')
    if rows.expiry.duplicated().any():raise ValueError('Ambiguous futures contract expiry.')
    return rows.head(3)


def timestamp(value):
    if not value:return None
    stamp=pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def quote_value(quote, now, max_bps=20):
    """Admit fresh positive depth, or explicitly dated closing LTP estimates."""
    result={'Price':None,'Bid':None,'Ask':None,'As of':None,'Source':'Unavailable','Status':'Missing quote'}
    if not quote:return result
    stamp=timestamp(quote.get('timestamp'));trade=timestamp(quote.get('last_trade_time'))
    result['As of']=stamp.isoformat() if stamp is not None else None
    age=(pd.Timestamp(now)-stamp).total_seconds() if stamp is not None else np.inf
    local=pd.Timestamp(now).tz_convert(IST)
    mins = local.hour * 60 + local.minute
    is_live = False
    if local.date() in SPECIAL_SESSIONS:
        info = SPECIAL_SESSIONS[local.date()]
        is_live = info.get('snap_start_min', 1080) <= mins <= info.get('snap_end_min', 1170)
    elif local.weekday() < 5 and local.date() not in HOLIDAYS and 9*60+15 <= mins <= 15*60+40:
        is_live = True
    if is_live:
        if not -5<=age<=90:return {**result,'Status':'Stale quote'}
        try:
            bid=quote['depth']['buy'][0];ask=quote['depth']['sell'][0]
            b=float(bid['price']);a=float(ask['price'])
            if not np.isfinite([a,b]).all() or not 0<b<a or min(bid['quantity'],ask['quantity'])<=0:raise ValueError()
            if (a-b)/((a+b)/2)*10000>max_bps:return {**result,'Status':'Bid/ask too wide'}
        except (KeyError,IndexError,TypeError,ValueError):return {**result,'Status':'No valid two-sided book'}
        return {**result,'Price':(a+b)/2,'Bid':b,'Ask':a,'Source':'Live midpoint','Status':'Accepted'}
    price=quote.get('last_price')
    # A provider may return a sentinel pre-market timestamp. The last-trade
    # timestamp dates LTP independently; never admit this as live depth.
    if (trade is not None and 0<=(local-trade).total_seconds()<=7*86400
            and 15*60<=trade.hour*60+trade.minute<=15*60+40
            and price is not None and np.isfinite(float(price)) and float(price)>0):
        return {**result,'Price':float(price),'As of':trade.isoformat(),'Source':'Closing LTP estimate','Status':'Dated close; not executable'}
    return {**result,'Status':'No eligible closing trade'}


def pair_values(front, back, front_expiry, back_expiry):
    gap=(pd.Timestamp(back_expiry)-pd.Timestamp(front_expiry)).days
    if gap<=0:raise ValueError('Spread requires increasing expiry dates.')
    if not np.isfinite([front,back]).all() or min(front,back)<=0:raise ValueError('Prices must be positive and finite.')
    return {'Spread':float(front-back),'Annualized carry %':float(np.log(back/front)*365/gap*100),'Expiry gap days':gap}


def quote_pair(first, second):
    a,b=first['quote'],second['quote']
    out={'Front expiry':first['expiry'],'Back expiry':second['expiry'],'Spread':None,'Annualized carry %':None,
         'Buy spread':None,'Sell spread':None,'Source':a['Source'],'Status':'Missing eligible leg'}
    if a['Price'] is None or b['Price'] is None:return out
    if a['Source']!=b['Source']:return {**out,'Status':'Different pricing sources'}
    ta,tb=timestamp(a['As of']),timestamp(b['As of'])
    limit=30 if a['Source']=='Live midpoint' else 300
    if ta.date()!=tb.date() or abs((ta-tb).total_seconds())>limit:return {**out,'Status':'Leg timestamps are not aligned'}
    out.update(pair_values(a['Price'],b['Price'],first['expiry'],second['expiry']))
    out.update({'As of':max(ta,tb).isoformat(),'Status':'Accepted' if limit==30 else 'Closing estimate; not executable'})
    if a['Bid'] is not None and b['Bid'] is not None:
        out.update({'Buy spread':a['Ask']-b['Bid'],'Sell spread':a['Bid']-b['Ask']})
    return out


def connection(root):
    root.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(root/'futures.sqlite',timeout=20)
    con.execute('CREATE TABLE IF NOT EXISTS candles(symbol TEXT,expiry TEXT,day TEXT,close REAL,volume REAL,oi REAL,PRIMARY KEY(symbol,expiry,day))')
    con.execute('CREATE TABLE IF NOT EXISTS snapshots(symbol TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(symbol,stamp))')
    con.execute('CREATE TABLE IF NOT EXISTS history_sync(symbol TEXT,expiry TEXT,cutoff TEXT,PRIMARY KEY(symbol,expiry))')
    return con


def load_latest(root,symbol):
    with closing(connection(root)) as con:
        row=con.execute('SELECT payload FROM snapshots WHERE symbol=? ORDER BY stamp DESC LIMIT 1',(symbol,)).fetchone()
    return json.loads(row[0]) if row else None


def daily_pair(root,symbol,front,back):
    with closing(connection(root)) as con:
        frame=pd.read_sql_query('SELECT a.day Date,a.close Front,b.close Back FROM candles a JOIN candles b ON a.symbol=b.symbol AND a.day=b.day WHERE a.symbol=? AND a.expiry=? AND b.expiry=? ORDER BY a.day',con,params=(symbol,front,back))
    if frame.empty:return pd.DataFrame(columns=['Date','Spread','Annualized carry %'])
    frame['Date']=pd.to_datetime(frame.Date)
    frame=frame[frame.Date<=pd.Timestamp(front)].copy()
    frame['Spread']=frame.Front-frame.Back
    frame['Annualized carry %']=np.log(frame.Back/frame.Front)*365/(pd.Timestamp(back)-pd.Timestamp(front)).days*100
    return frame


def saved_pairs(root,symbol):
    with closing(connection(root)) as con:
        rows=con.execute('SELECT payload FROM snapshots WHERE symbol=?',(symbol,)).fetchall()
    return sorted({(p['Front expiry'],p['Back expiry']) for row in rows for p in json.loads(row[0])['pairs']},reverse=True)


def intraday(root,symbol,front,back):
    with closing(connection(root)) as con:
        rows=con.execute('SELECT payload FROM snapshots WHERE symbol=? ORDER BY stamp',(symbol,)).fetchall()
    items=[p for row in rows for p in json.loads(row[0])['pairs'] if p['Front expiry']==front and p['Back expiry']==back and p.get('As of') and p['Spread'] is not None]
    if not items:return pd.DataFrame()
    frame=pd.DataFrame(items);frame['Date']=pd.to_datetime(frame['As of'],format='ISO8601',utc=True).dt.tz_convert(IST)
    return frame.sort_values('Date').drop_duplicates(['Date','Source'])


class FuturesMonitor(Monitor):
    def __init__(self):
        super().__init__();self.loaded=set()

    @serialized
    def refresh(self,client,root,symbol,now=None,force_history=False,history_only=False):
        now=now or datetime.now(IST);self.masters(client,now)
        listed=contracts(self.nfo,symbol,now)
        if len(listed)<3:raise MarketDataError('Upstox lists fewer than three futures expiries for this index.')
        root.mkdir(parents=True,exist_ok=True)
        atomic_csv(listed,root/f'{symbol}_contracts.csv',index=False)
        quotes={} if history_only else self.request(client,'/quote',params=[('i','NFO:'+s) for s in listed.tradingsymbol])
        legs=[];errors=[]
        for row in listed.itertuples():
            expiry=str(row.expiry)
            legs.append({'expiry':expiry,'contract':row.tradingsymbol,'lot_size':int(row.lot_size),
                         'quote':quote_value(quotes.get('NFO:'+row.tradingsymbol,{}),now)})
        pairs=[{'Pair':name,**quote_pair(legs[i],legs[i+1])} for i,name in enumerate(['Current - Next','Next - Far'])]
        result={'provider':getattr(client,'provider','Upstox'),'symbol':symbol,'collected':now.isoformat(),'legs':legs,'pairs':pairs,'history_errors':errors,'history_pending':[]}
        # Save raw provider observations for audit; never any credentials.
        result['raw_quotes']=quotes
        if not history_only:
            with closing(connection(root)) as con,con:
                con.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?)',(symbol,now.isoformat(),json.dumps(result,default=str)))
        for row in listed.itertuples():
            end=min(history_cutoff(now),row.expiry);key=(symbol,str(row.expiry),end)
            if key in self.loaded and not force_history:continue
            try:
                with closing(connection(root)) as con:
                    last=con.execute('SELECT MAX(day) FROM candles WHERE symbol=? AND expiry=?',(symbol,str(row.expiry))).fetchone()[0]
                    synced=con.execute('SELECT cutoff FROM history_sync WHERE symbol=? AND expiry=?',(symbol,str(row.expiry))).fetchone()
                if last and synced and synced[0]==str(end) and not force_history:
                    self.loaded.add(key);continue
                start=end-timedelta(days=365) if not last or force_history else max(end-timedelta(days=365),pd.Timestamp(last).date()-timedelta(days=3))
                while start<=end:
                    stop=min(start+timedelta(days=299),end)
                    response=self.request(client,f'/instruments/historical/{int(row.instrument_token)}/day',params={'from':f'{start} 00:00:00','to':f'{stop} 23:59:59','continuous':0,'oi':1})
                    records=[]
                    for candle in response.get('candles',[]):
                        day=timestamp(candle[0]).date();close=float(candle[4]);volume=float(candle[5])
                        if start<=day<=stop and np.isfinite(close) and close>0 and np.isfinite(volume) and volume>0:
                            records.append((symbol,str(row.expiry),str(day),close,volume,float(candle[6]) if len(candle)>6 else None))
                    with closing(connection(root)) as con,con:con.executemany('INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?)',records)
                    start=stop+timedelta(days=1)
                expected=last_trading_day_on_or_before(end)
                with closing(connection(root)) as con,con:
                    latest=con.execute('SELECT MAX(day) FROM candles WHERE symbol=? AND expiry=?',(symbol,str(row.expiry))).fetchone()[0]
                    if not latest or latest<str(expected):
                        previous=last_trading_day_on_or_before(expected-timedelta(days=1))
                        if expected==now.date() and latest and latest>=str(previous):
                            result['history_pending'].append(f'{row.expiry}: saved through {latest}; broker daily candle for {expected} is pending.')
                            continue
                        raise MarketDataError(f'Daily history saved through {latest or "no date"}; awaiting broker candle for {expected}.')
                    con.execute('INSERT OR REPLACE INTO history_sync VALUES (?,?,?)',(symbol,str(row.expiry),str(end)))
                self.loaded.add(key)
            except (MarketDataError,ValueError,TypeError,IndexError) as exc:
                if isinstance(exc,MarketDataError) and str(exc).startswith('Daily history saved through '):
                    errors.append(f'{row.expiry}: {exc}')
                else:errors.append(f'{row.expiry}: history update failed; saved candles retained.')
        if not history_only:
            with closing(connection(root)) as con,con:
                con.execute('UPDATE snapshots SET payload=? WHERE symbol=? AND stamp=?',(json.dumps(result,default=str),symbol,now.isoformat()))
        return result
