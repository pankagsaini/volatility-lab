"""Versioned research observations and strictly prior, DTE-matched comparisons."""

from contextlib import closing

from datetime import datetime, timezone

import hashlib

import json

import sqlite3

import numpy as np

import pandas as pd

from expiry_forecast import expiry_forecast,eligible_history

from smile import has_usable_points,load_smile

from vol_lib import rv_snapshot

from term_structure import term_structure

VERSION='relative-value-v2-session-aligned'

def connection(path):

    con=sqlite3.connect(path,timeout=20)

    con.execute('CREATE TABLE IF NOT EXISTS relative_value_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, version TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of,version))')

    return con

def save(path,record):

    # JSON null, never a zero substitute for unavailable measurements.

    clean=json.loads(pd.Series(record).to_json())

    with closing(connection(path)) as con,con:

        con.execute('INSERT OR IGNORE INTO relative_value_observations VALUES (?,?,?,?,?,?)',

            (record['Instrument'],record['Expiry'],record['Config'],record['As of'],VERSION,json.dumps(clean,allow_nan=False)))

def history(path,symbol,config,comparable=False):

    if not path.exists():return pd.DataFrame()

    with closing(connection(path)) as con:

        from history_policy import compatible

        keys=compatible(path,config) if comparable else [config]

        marks=','.join('?' for _ in keys)

        rows=con.execute(f'SELECT payload FROM relative_value_observations WHERE symbol=? AND config IN ({marks}) AND version=? ORDER BY as_of,expiry',

                         (symbol,*keys,VERSION)).fetchall()

    trusted=[]
    for (raw,) in rows:
        item=json.loads(raw)
        if item.get('Capture source')=='Google Drive' and item.get('Source')=='Upstox supplied IV':trusted.append(item)
    return eligible_history(pd.DataFrame(trusted))

def capture(root,snapshot):

    """Fit forecasts only for trusted Google Cloud broker-IV captures."""

    path=root/'monitor.sqlite'

    for row in snapshot['rows']:

        if not row.get('As of') or row.get('Capture source')!='Google Drive' or row.get('Source')!='Upstox supplied IV':continue

        record={**row,'Config':snapshot['config'],'Version':VERSION,

                'Recorded at':datetime.now(timezone.utc).isoformat(),'Parameters':snapshot.get('parameters')}

        detail=next((d for d in snapshot['details'] if d.get('Instrument')==row['Instrument'] and d.get('Expiry')==row['Expiry']),{})

        record['Quality']=detail

        if row['Instrument']=='NIFTY':record['India VIX']=snapshot.get('india_vix',{})

        smile=load_smile(path,row['Instrument'],row['Expiry'],snapshot['config'],row['As of'])

        if smile and has_usable_points(smile):record['Smile']=smile

        curve=term_structure(snapshot['rows'],row['Instrument'])

        term=curve[curve.Expiry==row['Expiry']]

        if not term.empty:
            record['Term structure']=term.iloc[0].to_dict()
            record['DTE']=float(term.iloc[0]['DTE'])

        try:

            raw=(root/(row['Instrument'].lower()+'_one_year.csv')).read_bytes()

            frame=pd.read_csv(__import__('io').BytesIO(raw),parse_dates=['Date'])

            record['Input SHA256']=hashlib.sha256(raw).hexdigest()

            archive=root/'research_inputs';archive.mkdir(exist_ok=True)

            target=archive/(record['Input SHA256']+'.csv')

            if not target.exists():target.write_bytes(raw)

            matched=expiry_forecast(frame,row['Expiry'],row['As of'],row.get('IV'))

            record.update(matched)

            record['Expiry model version']=matched['Version']

            # Use only the completed candles admitted by the expiry model.

            frame=frame[frame.Date<=pd.Timestamp(matched['Training through'])]

            record['HV10']=rv_snapshot(frame,10)['close_to_close_rv']*100

            record['HV30']=rv_snapshot(frame,30)['close_to_close_rv']*100

            for window in (5,10,20,30,60):

                record[f'YZ{window}']=rv_snapshot(frame,window)['yang_zhang_rv']*100

            record['Forecast status']=matched['GARCH status']

            record['Version']=VERSION

        except (OSError,ValueError,KeyError,TypeError) as error:

            record['Forecast status']=str(error)

        save(path,record)

def comparable_stats(frame,current,metric,min_days=200):

    """One nearest-DTE observation per prior day, same source/type/config.

    Prior year, +/-3 calendar DTE, excludes the whole current day. Sample

    selection does not depend on the metric. At least 200 valid daily values

    and near-full-year coverage are required for percentile/z-score.

    """

    result={'Prior days':0,'Percentile':None,'Z-score':None}

    required=['As of','DTE','Source','Type','Expiry',metric]

    if frame.empty or any(c not in frame for c in required):return result

    value=current.get(metric);dte=current.get('DTE')

    if value is None or dte is None or not np.isfinite([value,dte]).all():return result

    date=pd.Timestamp(current['As of']).normalize()

    data=frame.copy();data['day']=pd.to_datetime(data['As of'],format='ISO8601',utc=True).dt.tz_convert('Asia/Kolkata').dt.normalize()

    data['distance']=(pd.to_numeric(data.DTE,errors='coerce')-dte).abs()

    data=data[(data.day<date)&(data.day>=date-pd.DateOffset(years=1))&

              (data.Source==current['Source'])&(data.Type==current['Type'])&(data.distance<=3)]

    # Caller history is config/instrument/version isolated; enforce again for reusable use.

    for key in ('Instrument','Config','Version'):

        if key in data and key in current:data=data[data[key]==current[key]]

    data=data.sort_values(['day','distance','Expiry','As of']).drop_duplicates('day',keep='first')

    values=pd.to_numeric(data[metric],errors='coerce')

    data=data[np.isfinite(values)];values=pd.to_numeric(data[metric])

    result['Prior days']=len(data)

    if len(data)<min_days or data.day.min()>date-pd.DateOffset(years=1)+pd.Timedelta(days=10):return result

    result['Percentile']=float(100*((values<value).sum()+.5*(values==value).sum())/len(values))

    std=values.std(ddof=1)

    if std>0:result['Z-score']=float((value-values.mean())/std)

    return result

def maturity_stats(frame,current,metric,min_days=60,min_cycles=12):

    """Descriptive v1: prior 3y, explicit maturity buckets, source/type isolated.

    One observation per day, nearest DTE then latest timestamp. Requires both

    60 distinct days and 12 distinct expiries. No strategy significance claim.

    Caller supplies only compatible conventions; original historical rates retained.

    """

    result={'Prior days':0,'Expiry cycles':0,'Percentile':None,'Status':'Collecting comparable history'}

    bounds=[0,1,7,14,30,60,93,370]

    def bucket(x):

        return next((i for i in range(len(bounds)-1) if bounds[i]<=x<bounds[i+1]),None)

    value=pd.to_numeric(current.get(metric),errors='coerce');dte=pd.to_numeric(current.get('DTE'),errors='coerce')

    if not np.isfinite([value,dte]).all() or bucket(dte) is None or frame.empty:return result

    if any(c not in frame for c in ['As of','DTE','Source','Type','Expiry',metric]):return result

    date=pd.Timestamp(current['As of']).tz_convert('Asia/Kolkata').normalize()

    data=frame.copy();data['day']=pd.to_datetime(data['As of'],format='ISO8601',utc=True).dt.tz_convert('Asia/Kolkata').dt.normalize()

    maturity=pd.to_numeric(data.DTE,errors='coerce')

    data=data[(data.day<date)&(data.day>=date-pd.DateOffset(years=3))&maturity.map(bucket).eq(bucket(dte))]

    for field in ('Instrument','Source','Type','Version'):

        if field in data and field in current:data=data[data[field].eq(current[field])]

    data['distance']=(pd.to_numeric(data.DTE)-dte).abs()

    data=data.sort_values(['day','distance','As of','Expiry'],ascending=[True,True,False,True]).drop_duplicates('day')

    data=data[np.isfinite(pd.to_numeric(data[metric],errors='coerce'))]

    values=pd.to_numeric(data[metric]);result.update({'Prior days':len(data),'Expiry cycles':data.Expiry.nunique()})

    if len(data)>=min_days and data.Expiry.nunique()>=min_cycles:

        result.update({'Percentile':float(100*((values<value).sum()+.5*(values==value).sum())/len(values)), 'Status':'Descriptive only'})

    result['Maturity bucket']=f'{bounds[bucket(dte)]}–<{bounds[bucket(dte)+1]} calendar days'

    return result

