"""Display-time checks; never mutate archived observations."""
import pandas as pd
from market_data import IST


def snapshot_status(stamp, source, connected, now=None, max_age=60, retrieved_at=None):
    if not stamp:return 'Unavailable'
    try:
        when=pd.Timestamp(stamp)
        when=when.tz_localize(IST) if when.tzinfo is None else when.tz_convert(IST)
        current=pd.Timestamp.now(tz=IST) if now is None else pd.Timestamp(now)
        current=current.tz_localize(IST) if current.tzinfo is None else current.tz_convert(IST)
        if pd.isna(when) or pd.isna(current):return 'Unavailable'
        age=(current-when).total_seconds()
    except (ValueError,TypeError):return 'Unavailable'
    if age < -5:return 'Invalid future timestamp'
    if str(source).startswith('Upstox supplied IV'):
        if not retrieved_at:
            if when.date()<current.date():return f'Earlier session {when:%d %b %H:%M} · option timestamp unavailable'
            if age<=max_age:return 'Recent underlying snapshot · option timestamp unavailable'
            return f'Saved today {when:%H:%M} · option timestamp unavailable'
        try:
            captured=pd.Timestamp(retrieved_at)
            captured=captured.tz_localize(IST) if captured.tzinfo is None else captured.tz_convert(IST)
            if pd.isna(captured):return 'Broker IV saved · option timestamp unavailable'
            age=(current-captured).total_seconds()
        except (ValueError,TypeError):
            return 'Broker IV saved · option timestamp unavailable'
        if age < -5:return 'Invalid future capture time'
        if age<=max_age:return 'Recent API retrieval · option timestamp unavailable'
        if captured.date()==current.date():return f'Retrieved today {captured:%H:%M} · option timestamp unavailable'
        return f'Saved {captured:%d %b %H:%M} · option timestamp unavailable'
    if not connected:return 'Saved snapshot'
    if 'clos' in str(source).lower():return 'Closing estimate'
    if not (current.weekday()<5 and 555<=current.hour*60+current.minute<=940):return 'Saved snapshot'
    return 'Recent snapshot' if age<=max_age else 'Stale - refresh'


def futures_display_pairs(result,connected,now=None):
    rows=[]
    for original in result['pairs']:
        row=dict(original)
        legs=[leg for leg in result['legs'] if leg['expiry'] in (row['Front expiry'],row['Back expiry'])]
        recent=len(legs)==2 and all(snapshot_status(leg['quote'].get('As of'),leg['quote'].get('Source'),connected,now,90)=='Recent snapshot' for leg in legs)
        state=snapshot_status(row.get('As of'),row.get('Source'),connected,now,90)
        if not recent or row.get('Status')!='Accepted' or row.get('Source')!='Live midpoint':
            row['Buy spread']=row['Sell spread']=None
            if row.get('Spread') is not None:
                row['Status']=state if state!='Recent snapshot' else 'Leg quote stale - refresh'
                if row.get('Source')=='Live midpoint':row['Source']='Saved midpoint'
        else:row['Status']='Recent two-sided snapshot'
        rows.append(row)
    return rows
