"""Presentation-only data health. Never turns historical richness into a trade signal."""
import numpy as np
import pandas as pd
import streamlit as st


def quote_state(as_of, now=None):
    if not as_of:return 'Unavailable'
    try:
        stamp=pd.Timestamp(as_of)
        if stamp.tzinfo is None:return 'Timestamp unverified'
        now=pd.Timestamp.now(tz='Asia/Kolkata') if now is None else pd.Timestamp(now)
        age=(now-stamp).total_seconds()
        if age < -60:return 'Timestamp unverified'
        if stamp.tz_convert('Asia/Kolkata').date()!=now.tz_convert('Asia/Kolkata').date():return 'Earlier session'
        return 'Recent snapshot' if age<=300 else 'Saved today'
    except (ValueError,TypeError):return 'Timestamp unverified'


def history_summary(data,period,now=None):
    end=(pd.Timestamp.now(tz='Asia/Kolkata') if now is None else pd.Timestamp(now)).tz_localize(None).normalize()
    start=end-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12}[period])
    view=data[(data.Date>=start)&(data.Date<=end)] if len(data) else data
    return {name:int(pd.to_numeric(view.get(name,pd.Series(dtype=float)),errors='coerce').notna().sum()) for name in ('IV','IV-RV Spread')}


def show_history_health(data,period):
    counts=history_summary(data,period)
    a,b=st.columns(2)
    a.caption(f"Selected range · {counts['IV']} IV sessions")
    b.caption(f"{counts['IV-RV Spread']} matched IV–RV sessions · gaps are not filled")
    if counts['IV']==0:st.info('No IV observations in this range. Try a longer range or another chart expiry.')
    elif counts['IV']<2:st.caption('One observation is shown as a point; a trend needs multiple dated observations.')


def show_snapshot_health(rows):
    rows=list(rows)
    valid=sum(pd.notna(r.get('IV')) for r in rows)
    spreads=sum(pd.notna(r.get('IV-RV Spread')) for r in rows)
    stamps=[pd.Timestamp(r['As of']) for r in rows if r.get('As of')]
    states={quote_state(r.get('As of')) for r in rows}
    state=next((v for v in ['Timestamp unverified','Earlier session','Unavailable','Saved today','Recent snapshot'] if v in states),'Unavailable')
    a,b,c=st.columns(3)
    a.metric('Snapshot',state)
    b.metric('IV coverage',f'{valid} / {len(rows)}')
    c.metric('IV–RV coverage',f'{spreads} / {len(rows)}')
    st.caption('IV–RV = IV − trailing HV30, in percentage points. Historical richness is not an expected trading return.')
