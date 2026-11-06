"""Expiry-specific option snapshots and durable, source-labelled IV history."""

from datetime import datetime, time

import json

import sqlite3

from copy import deepcopy
from functools import lru_cache

import numpy as np

import pandas as pd

import market_table as base

from collector_support import serialized,atomic_csv

from market_data import IST, MarketDataError

from option_math import reversal_values

from trade_review import archive_quotes

from history_policy import compatible,register

DATA=base.DATA

SCHEMA=4
COMPATIBLE_SCHEMAS=(3,SCHEMA)

def calculation_key(rate,dividend,max_spread):

    from history_policy import key
    return key(rate,dividend,max_spread)

def listed_expiries(master,symbol,now):

    sub=master[master.name==symbol]

    options={d for d in sub.loc[sub.instrument_type.isin(['CE','PE']),'expiry'] if pd.notna(d) and datetime.combine(d,time(15,40),tzinfo=IST)>now}

    monthly=sorted(options.intersection(set(sub.loc[sub.instrument_type=='FUT','expiry'])))[:3]

    if not monthly:return []

    dates=sorted(d for d in options if d<=monthly[-1]) if symbol=='NIFTY' else monthly

    return [{'date':str(d),'kind':'Monthly' if d in monthly else 'Weekly'} for d in dates]

def month_future_contract(futures,expiry):
    """Return the actual monthly future for this expiry's calendar month.

    Index futures are monthly while NIFTY options can be weekly. A weekly
    synthetic therefore gets its own month's future as a reference, never the
    nearest future from another calendar month.
    """
    exact=futures[futures.expiry==expiry]
    if len(exact)==1:return exact.iloc[0].to_dict(),'Same-expiry monthly future'
    month=futures[(pd.to_datetime(futures.expiry).dt.year==expiry.year)&
                  (pd.to_datetime(futures.expiry).dt.month==expiry.month)]
    if len(month)==1:return month.iloc[0].to_dict(),'Monthly future for weekly expiry month'
    return None,'No listed monthly future for expiry month'

def db():

    DATA.mkdir(parents=True,exist_ok=True)

    con=sqlite3.connect(DATA/'monitor.sqlite',timeout=20)

    con.execute('''CREATE TABLE IF NOT EXISTS expiry_observations (

        date TEXT, symbol TEXT, expiry TEXT, config TEXT, iv REAL, spread REAL,

        source TEXT, PRIMARY KEY(date,symbol,expiry,config))''')

    return con

def save_observation(symbol,expiry,config,date,iv,spread,source,replace=True):

    # Only verified Google Cloud imports enter trusted IV history.
    if source!='Upstox supplied IV (Google Cloud)' or not np.isfinite(iv) or iv<=0:return

    con=db()

    try:

        with con:

            suffix='DO UPDATE SET iv=excluded.iv,spread=excluded.spread,source=excluded.source' if replace else 'DO NOTHING'

            con.execute('INSERT INTO expiry_observations VALUES (?,?,?,?,?,?,?) ON CONFLICT(date,symbol,expiry,config) '+suffix,

                        [str(date),symbol,str(expiry),config,float(iv),float(spread) if np.isfinite(spread) else None,source])

    finally:con.close()

def history(symbol,expiry,config):

    con=db()

    try:

        keys=compatible(DATA/'monitor.sqlite',config)

        marks=','.join('?' for _ in keys)

        frame=pd.read_sql_query(f"SELECT date AS Date,iv AS IV,spread AS Spread,source AS Source,config AS Config FROM expiry_observations WHERE symbol=? AND expiry=? AND source='Upstox supplied IV (Google Cloud)' AND config IN ({marks}) ORDER BY date",con,params=[symbol,str(expiry),*keys],parse_dates=['Date'])

        frame['_preferred']=frame.Config.eq(config)

        return frame.sort_values(['Date','_preferred','Config']).drop_duplicates(['Date','Source'],keep='last').drop(columns='_preferred')

    finally:con.close()

def preserve_failed_rows(result,previous):
    """Retain whole old rows on transport/session failure, never blend quote times."""
    if previous.get('config')!=result.get('config') or previous.get('provider')!=result.get('provider'):return result
    saved={(r.get('Instrument'),r.get('Expiry')):r for r in previous.get('rows',[])}
    rows=[]
    for row in result.get('rows',[]):
        old=saved.get((row.get('Instrument'),row.get('Expiry')), {})
        if row.get('Source')=='Unavailable' and not row.get('As of') and old.get('As of') and pd.notna(old.get('IV')):
            row={**old,'Refresh status':'Refresh failed; retaining saved snapshot'}
        rows.append(row)
    return {**result,'rows':rows}

def write_snapshot(result):

    path=DATA/'expiry_latest.json'
    try:result=preserve_failed_rows(result,json.loads(path.read_text(encoding='utf-8')))
    except (OSError,ValueError):pass

    clean=json.loads(pd.Series(result).to_json())

    path=DATA/'expiry_latest.json';tmp=DATA/'expiry_latest.tmp'

    tmp.write_text(json.dumps(clean,indent=2),encoding='utf-8');tmp.replace(path)

    return clean

def load_snapshot(config,allow_saved=False):

    try:

        raw=(DATA/'expiry_latest.json').read_bytes()
        result=json.loads(raw)

        # Keep an existing verified snapshot visible until the next refresh adds
        # the synthetic-future diagnostic.  Old rows simply show it unavailable.
        if result.get('schema') in COMPATIBLE_SCHEMAS and (allow_saved or result.get('config')==config):
            revision=[]
            for symbol in sorted({r['Instrument'] for r in result.get('rows',[])}):
                path=DATA/f'{symbol.lower()}_one_year.csv'
                try:
                    stat=path.stat();revision.append((str(path),stat.st_mtime_ns,stat.st_size))
                except OSError:revision.append((str(path),None,None))
            return deepcopy(_aligned_snapshot(raw,tuple(revision)))

    except (OSError,ValueError):pass

    return None


@lru_cache(maxsize=8)
def _aligned_snapshot(raw,history_revision):
    """Invalidate on either quote content or saved OHLC; callers receive a copy."""
    return align_snapshot(json.loads(raw))


class ExpiryMonitor(base.Monitor):

    @serialized

    def refresh_all(self,client,rate,dividend,max_spread,progress=None):

        register(DATA/'monitor.sqlite',rate,dividend,max_spread)

        now=datetime.now(IST);self.masters(client,now)

        config=calculation_key(rate,dividend,max_spread)

        master=self.nfo[self.nfo.name.isin(base.INDICES)].copy()

        DATA.mkdir(parents=True,exist_ok=True)

        atomic_csv(master,DATA/'index_contracts.csv',index=False)

        rows=[];details=[];catalog={};vix={}
        dividend_inputs=dividend

        for index,(symbol,spot_name) in enumerate(base.INDICES.items()):
            from dividend_yield import for_symbol
            dividend=(dividend_inputs.get(symbol) if isinstance(dividend_inputs,dict) else dividend_inputs) if getattr(client,'provider','')=='Upstox' else for_symbol(dividend_inputs,symbol)

            if progress:progress(index/len(base.INDICES),f'Loading {symbol} expiries')

            entries=listed_expiries(master,symbol,now);catalog[symbol]=entries

            frame=None;spot=np.nan;change=np.nan

            try:

                frame=self.history(client,symbol,now)

            except (MarketDataError,ValueError,KeyError):pass

            # Fetch each underlying immediately before its chains, avoiding old spot timestamps.

            try:

                request_names=['NSE:'+spot_name]+(['NSE:INDIA VIX'] if symbol=='NIFTY' else [])

                spots=self.request(client,'/quote',params=[('i',s) for s in request_names])

                if symbol=='NIFTY':

                    v=spots.get('NSE:INDIA VIX',{});vix={'value':v.get('last_price'),'timestamp':str(v.get('timestamp','Unavailable'))}

                spotq=spots['NSE:'+spot_name];spot=float(spotq['last_price']);previous=float(spotq.get('ohlc',{}).get('close',0))

                if not np.isfinite(spot) or spot<=0:raise MarketDataError('Invalid underlying price.')

                change=(spot/previous-1)*100 if previous>0 else np.nan

                valuation,closing=base.valuation_time(spotq,datetime.now(IST))

                dates=[pd.Timestamp(e['date']).date() for e in entries]

                contracts=master[(master.name==symbol)&master.expiry.isin(dates)&master.instrument_type.isin(['CE','PE'])]
                futures=master[(master.name==symbol)&master.instrument_type.eq('FUT')].copy()
                future_by_expiry={expiry:month_future_contract(futures,expiry) for expiry in dates}
                selected_futures=[contract for contract,_ in future_by_expiry.values() if contract is not None]

                wanted=['NFO:'+s for s in pd.concat([contracts.tradingsymbol,pd.Series([item['tradingsymbol'] for item in selected_futures])]).drop_duplicates()];quotes={}

                batch_size=getattr(client,'quote_batch_size',250)
                for start in range(0,len(wanted),batch_size):

                    quotes.update(self.request(client,'/quote',params=[('i',s) for s in wanted[start:start+batch_size]]))

                if not closing:

                    if not base.fresh(spotq,datetime.now(IST)):raise MarketDataError('Spot quote became stale; refresh again.')

                    valuation=datetime.now(IST)

                for entry in entries:

                    expiry=pd.Timestamp(entry['date']).date()

                    sub=contracts[contracts.expiry==expiry]

                    if getattr(client,'provider','')!='Upstox':
                        raise MarketDataError('Trusted option IV requires Upstox broker-chain data.')
                    from broker_options import chain_from_response
                    from upstox_data import INDICES as broker_indices
                    broker_raw=client.chain(broker_indices[symbol],str(expiry))
                    chain=chain_from_response(broker_raw,expiry)
                    received=datetime.now(IST)
                    future_contract,future_reference=future_by_expiry.get(expiry,(None,'No listed monthly future for expiry month'))
                    future_quote=quotes.get('NFO:'+future_contract['tradingsymbol'],{}) if future_contract else None
                    from synthetic_future import calculate as synthetic_future
                    parity=synthetic_future(broker_raw,spot,future_quote,future_contract)
                    parity.update({'Month future LTP':parity.get('Listed future'),'Future reference':future_reference})

                    audit=chain.attrs.get('quote_quality',[])

                    quality_dir=DATA/'quote_quality';quality_dir.mkdir(exist_ok=True)

                    pd.DataFrame(audit).to_csv(quality_dir/f'{symbol}_{expiry}_{datetime.now(IST).strftime("%Y%m%dT%H%M%S%f")}.csv',index=False)

                    call,put,iv=base.atm_values(chain,sub.strike.unique(),spot)

                    source='Upstox supplied IV'
                    alignment=base.aligned_spread(iv,valuation,source,frame)

                    spread=alignment['IV-RV Spread']

                    values={'Provider':getattr(client,'provider','Upstox'),'Instrument':symbol,'Expiry':str(expiry),'Type':entry['kind'],'Price':spot,'% change':change,'Call IV':call,'Put IV':put,'IV':iv,**reversal_values(chain,symbol),'IV-RV Spread':spread,'As of':valuation.isoformat(),'Source':source,**alignment,**parity}

                    values.update({'Retrieved at':received.isoformat(),'Timestamp note':'Option-chain API has no per-Greek exchange timestamp; As of is the underlying valuation reference', 'IV method':'Lab arithmetic mean of broker ATM call and put IV', 'Greeks source':'Upstox supplied Greeks'})
                    rows.append(values)

                    missing=[c for c in base.COLUMNS if not np.isfinite(values[c])]

                    reasons={}

                    for item in audit:

                        if not item['accepted']:reasons[item['reason']]=reasons.get(item['reason'],0)+1

                    details.append({'Instrument':symbol,'Expiry':str(expiry),'Status':'Ready' if not missing else 'Unavailable: '+', '.join(missing),'Accepted contracts':len(chain),'Rejected contracts':len(audit)-len(chain),'ATM source':source,**alignment,**parity,'Rejection reasons':json.dumps(reasons,sort_keys=True),'As of':valuation.isoformat()})

                    future_keys={'NFO:'+future_contract['tradingsymbol']} if future_contract else set()
                    keys={'NFO:'+s for s in sub.tradingsymbol}|future_keys

                    from input_provenance import snapshot as pricing_snapshot
                    payload={'provider':getattr(client,'provider','Upstox'),'pricing_inputs':pricing_snapshot(rate,dividend,symbol),'valuation':valuation.isoformat(),'spot':spot,'spot_quote':spotq,

                             'closing':closing,'config':config,

                              'quotes':{k:q for k,q in quotes.items() if k in keys},'contracts':sub.to_dict('records')+([future_contract] if future_contract else [])}

                    payload.update(broker_option_chain=broker_raw,broker_received_at=received.isoformat(),iv_source='Upstox supplied IV')
                    archive_quotes(DATA,symbol,expiry,payload)

                    target=DATA/f'{symbol.lower()}_option_quotes_{expiry}.json'

                    temporary=target.with_suffix('.tmp')

                    temporary.write_text(json.dumps(payload,default=str),encoding='utf-8')

                    temporary.replace(target)

            except (MarketDataError,ValueError,KeyError,TypeError) as exc:

                message=str(exc) if isinstance(exc,MarketDataError) else 'Market data unavailable; retry refresh.'

                for entry in entries:

                    if any(r['Instrument']==symbol and r['Expiry']==entry['date'] for r in rows):continue

                    rows.append({'Instrument':symbol,'Expiry':entry['date'],'Type':entry['kind'],**{c:np.nan for c in base.COLUMNS},'Price':spot,'% change':change,'As of':None,'Source':'Unavailable'})

                    details.append({'Instrument':symbol,'Expiry':entry['date'],'Status':message})

                if not entries:details.append({'Instrument':symbol,'Expiry':'','Status':message})

        result={'provider':getattr(client,'provider','Upstox'),'schema':SCHEMA,'config':config,'parameters':{'rate':rate,'dividend':dividend_inputs,'max_spread':max_spread},'timestamp':datetime.now(IST).isoformat(),'rows':rows,'details':details,'catalog':catalog,'india_vix':vix}

        # Publish broker quotes and raw capture provenance together.

        result=write_snapshot(result)

        if progress:progress(.95,'Saving option snapshot')

        if progress:progress(1.,'Updated')

        return write_snapshot(result)



def align_snapshot(result):

    """Re-evaluate derived spreads on load; preserve original quotes and timestamp."""

    frames={};alignments={}

    for row in result.get('rows',[]):

        symbol=row['Instrument']

        if symbol not in frames:

            try:frames[symbol]=pd.read_csv(DATA/f'{symbol.lower()}_one_year.csv')

            except (OSError,ValueError):frames[symbol]=None

        if not row.get('As of'):continue

        # All expiries at the same session/source use the same completed HV.
        # Only IV differs; avoid rebuilding identical rolling windows per expiry.
        key=(symbol,str(pd.Timestamp(row['As of']).tz_localize(None).normalize()),row.get('Source',''))
        if key not in alignments:
            alignments[key]=base.aligned_spread(0.,row['As of'],key[2],frames[symbol])
        alignment=dict(alignments[key])
        alignment['IV-RV Spread']=pd.to_numeric(row.get('IV'),errors='coerce')-alignment['HV30']

        row.update(alignment)

        for detail in result.get('details',[]):

            if detail.get('Instrument')==symbol and detail.get('Expiry')==row['Expiry']:

                detail.update(alignment)

    from synthetic_future import calculate as calc_synthetic

    for row in result.get('rows', []):
        sym = row.get('Instrument')
        exp = row.get('Expiry')
        step = 50.0 if sym == 'NIFTY' else 100.0
        row_price = row.get('Price')

        # Guarantee mathematically accurate ATM strike for the row's underlying spot
        if row_price and (row.get('ATM strike') is None or abs(float(row.get('ATM strike') or 0) - float(row_price)) > step * 1.5):
            row['ATM strike'] = round(float(row_price) / step) * step

        # Ensure DTE is always populated for consistent display across radar & term structure
        if row.get('DTE') is None and exp and row.get('As of'):
            try:
                exp_date = pd.Timestamp(exp).date()
                asof_date = pd.Timestamp(row['As of']).date()
                row['DTE'] = max(0.2, float((exp_date - asof_date).days))
            except Exception:
                pass

        if row.get('Synthetic future') is None and sym and exp:
            q_file = DATA / f'{sym.lower()}_option_quotes_{exp}.json'
            if q_file.exists():
                try:
                    c = json.loads(q_file.read_text(encoding='utf-8'))
                    chain = c.get('broker_option_chain')
                    c_spot = c.get('spot')
                    spot = row_price or c_spot
                    # Only accept quote file if quotes belong to the same spot regime (within 2 strike steps)
                    if chain and spot and (c_spot is None or row_price is None or abs(float(row_price) - float(c_spot)) <= step * 2.0):
                        p = calc_synthetic(chain, spot)
                        row['Synthetic future'] = p.get('Synthetic future')
                        syn_atm = p.get('ATM strike')
                        if syn_atm and abs(float(syn_atm) - float(spot)) <= step * 1.5:
                            row['ATM strike'] = syn_atm
                except Exception:
                    pass

    # Fill an older saved snapshot immediately from its own monthly quote rows.
    # This avoids a blank weekly reference until the next broker refresh while
    # keeping the reference inside the same calendar month.
    monthly={(row.get('Instrument'),str(row.get('Expiry'))[:7]):row
             for row in result.get('rows',[]) if row.get('Type')=='Monthly'}
    for row in result.get('rows',[]):
        reference=monthly.get((row.get('Instrument'),str(row.get('Expiry'))[:7]))
        if row.get('Type')=='Weekly' and reference:
            row['Month future LTP']=reference.get('Month future LTP',reference.get('Listed future'))
            row['Future contract']=reference.get('Future contract')
            row['Future quote time']=reference.get('Future quote time')
            row['Future reference']='Monthly future for weekly expiry month'
            try:
                if np.isfinite(float(row.get('Synthetic future'))):row['Synthetic status']='Ready · monthly future reference'
            except (TypeError,ValueError):pass
        else:
            row['Month future LTP']=row.get('Month future LTP',row.get('Listed future'))
            row['Future reference']=row.get('Future reference','Same-expiry monthly future' if row.get('Type')=='Monthly' else 'Unavailable')
            try:
                if np.isfinite(float(row.get('Synthetic future'))):row['Synthetic status']='Ready · same-expiry monthly future'
            except (TypeError,ValueError):pass

    # Fallback: if Month future LTP is still unpopulated, check latest futures snapshots
    fut_db = DATA / 'futures/futures.sqlite'
    if fut_db.exists() and any(r.get('Month future LTP') is None for r in result.get('rows', [])):
        try:
            import sqlite3
            latest_futs = {}
            with sqlite3.connect(fut_db, timeout=5) as con:
                for sym in ('NIFTY', 'BANKNIFTY'):
                    r_fut = con.execute('SELECT payload FROM snapshots WHERE symbol=? ORDER BY rowid DESC LIMIT 1', (sym,)).fetchone()
                    if r_fut:
                        d_fut = json.loads(r_fut[0])
                        for leg in d_fut.get('legs', []):
                            p = (leg.get('quote') or {}).get('Price')
                            if p and np.isfinite(float(p)):
                                latest_futs[sym] = float(p)
                                break
            for row in result.get('rows', []):
                sym = row.get('Instrument')
                if row.get('Month future LTP') is None and sym in latest_futs:
                    row['Month future LTP'] = latest_futs[sym]
        except Exception:
            pass

    result['spread_version']='session-aligned-v2'

    return result

