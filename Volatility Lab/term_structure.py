"""Spot-ATM term structure from one instrument's dated expiry snapshots."""
from datetime import datetime, time
import numpy as np
import pandas as pd
from market_data import IST


def term_structure(rows, symbol):
    selected=[row for row in rows if row.get('Instrument')==symbol]
    records=[]
    seen=set()
    for row in sorted(selected,key=lambda r:r['Expiry']):
        expiry=pd.Timestamp(row['Expiry']).date()
        if expiry in seen:raise ValueError('Duplicate expiry in snapshot.')
        seen.add(expiry)
        stamp=pd.Timestamp(row.get('As of'))
        valid_time=not pd.isna(stamp) and stamp.tzinfo is not None
        if valid_time:stamp=stamp.tz_convert(IST)
        end=pd.Timestamp(datetime.combine(expiry,time(15,40),tzinfo=IST))
        dte=(end-stamp).total_seconds()/86400 if valid_time else np.nan
        try:iv=float(row.get('IV'))
        except (TypeError,ValueError):iv=np.nan
        status='Ready'
        if not valid_time:status='Missing valuation timestamp'
        elif dte<=0:status='Expired at valuation'
        elif not np.isfinite(iv) or iv<=0:status='IV unavailable'
        if status!='Ready':iv=np.nan
        records.append({'Expiry':str(expiry),'Type':row.get('Type',''), 'DTE':dte,
            'IV':iv,'Next expiry':None,'Next IV - IV':np.nan,
            'Source':row.get('Source','Unknown'),'As of':str(stamp) if valid_time else None,
            'Status':status,'Comparison':'No next expiry'})
    for current,following in zip(records,records[1:]):
        current['Next expiry']=following['Expiry']
        if not np.isfinite([current['IV'],following['IV']]).all():
            current['Comparison']='Unavailable leg'
        elif current['As of']!=following['As of']:
            current['Comparison']='Different valuation times'
        elif current['Source']!=following['Source']:
            current['Comparison']='Different price sources'
        else:
            current['Next IV - IV']=following['IV']-current['IV']
            current['Comparison']='Comparable snapshots'
    return pd.DataFrame(records,columns=['Expiry','Type','DTE','IV','Next expiry',
        'Next IV - IV','Source','As of','Status','Comparison'])
