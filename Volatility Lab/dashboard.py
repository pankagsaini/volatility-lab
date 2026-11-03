"""Standalone local options dashboard; no trading actions."""

from pathlib import Path

import importlib

import os

import gzip

import json

from datetime import datetime, timezone

import streamlit as st

from market_data import MarketDataError, IST

from pricing_inputs import get_rate, get_yields

ROOT=Path(__file__).resolve().parent

def latest_cloud_capture():
    from dashboard_cache import latest_cloud_capture as saved_summary
    return saved_summary(ROOT)

def drive_sync_label(info,now=None):
    status=str(info.get('status','unknown')).strip().lower()
    checked='time unavailable'
    try:
        raw_stamp=str(info.get('updated') or '').strip()
        if not raw_stamp:raise ValueError('Missing sync timestamp')
        stamp=datetime.fromisoformat(raw_stamp.replace('Z','+00:00'))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        checked=stamp.astimezone(IST).strftime('%d %b %H:%M IST')
        current=now or datetime.now(timezone.utc)
        if current.tzinfo is None:current=current.replace(tzinfo=timezone.utc)
        age=(current.astimezone(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()
        if status=='ready' and age>20*60:status='stale'
        elif status=='ready' and age < -5*60:status='unknown'
    except (ValueError,TypeError,OverflowError):
        if status=='ready':status='unknown'
    label={'ready':'healthy','partial':'needs attention','syncing':'syncing','waiting':'waiting','stale':'stale','unknown':'status unavailable'}.get(status,status)
    return label,checked

st.set_page_config(page_title='Volatility Lab',page_icon='◈',layout='wide',initial_sidebar_state='collapsed')

st.session_state.pop('login_notice', None)

# Keep browser authentication intact while reloading changed calculation modules.

# Dependency order ensures from-import references see the updated implementation.

MODULES=('market_data','dashboard_cache','catalogue_fetch','refresh_policy','upstox_data','upstox_adapter','broker_options','quote_status','data_accuracy','option_math','quote_quality','vol_lib','forecast','volatility_regime',

         'research_extensions','har_research','experimental_models','forecast_validation',

         'history_policy','option_context','dividend_yield','expiry_forecast','term_structure','corporate_event_calendar','smile','trade_review','snapshot_history',

         'market_table','relative_value','synthetic_future','expiry_data','model_view','research_ui',

         'trade_review_ui','research_health','restored_lab','options_context_cache','monitor_ui','futures_data','futures_rolling','futures_sync','futures_view_cache','futures_ui','broker_charges','backtest_index','backtest_engine','backtest_signals','strategy_backtest','backtest_report','futures_backtest','backtest_jobs','futures_backtest_ui','backtest_ui')

import monitor_ui

revision=tuple((ROOT/(name+'.py')).stat().st_mtime_ns for name in MODULES)

if getattr(monitor_ui,'_revision',None)!=revision:

    if getattr(monitor_ui,'_revision',None) is not None:
        import sys
        for name in MODULES:
            if name in sys.modules:importlib.reload(sys.modules[name])

    monitor_ui._revision=revision

if st.session_state.get('dashboard_revision')!=revision:

    st.session_state.dashboard_revision=revision

    st.session_state.expiry_initial_attempted=False

st.markdown('''<style>

.stApp{background:#0c1321;color:#e4ecf8}
[data-testid="stPopoverButton"]{border-radius:10px!important;background:#131e2d!important}
[data-testid="stSegmentedControl"] button{border-radius:8px!important;font-size:.85rem!important}
[data-testid="stDataFrame"]{border:1px solid #243348;border-radius:12px;overflow:hidden}
[data-testid="stPlotlyChart"]{border:1px solid #202e42;border-radius:14px;padding:8px;background:#0c1321}
[data-testid="stExpander"]{border-color:#243348!important;border-radius:10px!important}


.block-container{max-width:1450px;padding-top:1.2rem;padding-bottom:2rem;padding-left:1.5rem;padding-right:1.5rem}

h1{font-size:2.1rem!important;letter-spacing:-.055em;margin-bottom:0!important}

h2,h3{letter-spacing:-.025em}

h3{font-size:1.3rem!important}

[data-testid="stVerticalBlock"]{gap:.8rem}

section[data-testid="stSidebar"]{background:#111c2d}

div[data-testid="stMetric"]{background:#131f31;border:1px solid #26364d;border-radius:12px;padding:14px 18px}

div[data-testid="stMetricLabel"]{color:#a3b7d0}

div[data-testid="stDataFrame"]{border:1px solid #26364d;border-radius:10px;overflow:hidden}

div[data-testid="stExpander"]{border-color:#253247}

div[data-testid="stCaptionContainer"]{color:#91a5c0}

[data-testid="stHeader"]{background:#0c1321}

button[kind="primary"]{border-radius:8px}

div[data-testid="stRadio"]{padding:4px 0 10px}

div[data-testid="stRadio"] [role="radiogroup"]{gap:.45rem}

div[data-testid="stRadio"] label{border:1px solid #26364d;border-radius:8px;padding:7px 12px;background:#111c2d}

div[data-testid="stRadio"] label:has(input:checked){border-color:#50c5b4;background:#163037}

div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums}

hr{border-color:#253247!important;margin:1rem 0!important}

div[data-testid="stMetricValue"]{font-size:1.45rem}

div[data-testid="stExpander"]{border-radius:10px;background:#101a2a}

@media(max-width:700px){.block-container{padding-left:1rem;padding-right:1rem}h1{font-size:1.7rem!important}}

.brand-eyebrow{color:#50c5b4;font-size:.72rem;letter-spacing:.20em;font-weight:600;margin-top:8px}
h1{font-size:2.35rem!important;letter-spacing:-.045em!important}
h3{font-size:1.12rem!important;font-weight:600!important}
[data-testid="stCaptionContainer"]{font-size:.78rem;line-height:1.5}
[data-testid="stMetric"]{box-shadow:none!important}
.block-container{max-width:1500px;padding-top:2rem}
</style>''',unsafe_allow_html=True)

with st.sidebar:

    advanced=st.toggle('More tools',value=False,key='advanced_workspace',help='Adds research views, intraday tools and Backtest.')

    from collector_support import read_json, STATE
    from upstox_data import TOKEN_PATH
    from upstox_adapter import market_client
    if TOKEN_PATH.exists():
        version=(TOKEN_PATH.stat().st_mtime_ns,(ROOT/'upstox_data.py').stat().st_mtime_ns,(ROOT/'upstox_adapter.py').stat().st_mtime_ns)
        if st.session_state.get('_upstox_version')!=version:
            try:
                st.session_state.data_client=market_client()
                st.session_state._upstox_version=version
            except MarketDataError as error:st.error(str(error))
    client=st.session_state.get('data_client')
    if client is not None:
        from upstox_adapter import UpstoxAdapter
        client.__class__=UpstoxAdapter
    ready=bool(client and client.authenticated)
    st.caption('Upstox token saved · read-only' if ready else 'Upstox not set up · saved history remains available')
    capture=read_json(STATE/'status.json')
    backfill=read_json(ROOT/'data'/'upstox'/'collection_status.json')

    from upstox_data import render_connection
    render_connection()

    with st.expander('Model inputs'):

        rate_info=get_rate()

        rate=rate_info.get('model_rate')

        if rate is None:
            st.info('Model rate unavailable.')
        else:
            value=rate_info.get('yield_percent')
            st.metric('RBI 91-day T-bill',f'{value:.4f}%' if value is not None else 'Unavailable')
            checked=str(rate_info.get('checked_at') or 'unavailable')[:10]
            st.caption(f'Checked {checked} · model uses continuous ACT/365')
            if rate_info.get('error'):st.warning(rate_info['error'])
            if rate_info.get('source'):st.link_button('RBI source',rate_info['source'])

        dividend_info=get_yields()
        dividend={symbol:item.get('model_yield') for symbol,item in dividend_info.items()}
        for symbol,item in dividend_info.items():
            value=item.get('yield_percent')
            st.metric(symbol+' dividend yield',f'{value:.2f}%' if value is not None else 'Unavailable')
            st.caption('Published '+str(item.get('as_of') or 'unavailable')+' · checked '+str(item.get('checked_at') or 'unavailable')[:10])
            if item.get('source'):st.link_button(symbol+' factsheet',item['source'])
            if item.get('error'):st.warning(item['error'])
        spread=3.0  # Stored-history compatibility; this does not filter broker IV.

    st.session_state.pop('wide_atm_fallback',None)

st.title('Volatility Lab')

import google_archive_sync
@st.fragment(run_every=30)
def cloud_sync_status():
    google_archive_sync.ensure_sync(rate,dividend,spread)
    info=read_json(ROOT/'data'/'google_archive'/'status.json')
    if not info:
        st.warning('Drive sync status unavailable.')
    elif info.get('status')=='waiting':st.caption('Waiting for Google Drive')
    else:
        count=info.get('total_files',0)
        left,right=st.columns([5,1])
        state,checked=drive_sync_label(info)
        imported=info.get('imported',0)
        summary=f"Drive · {state} · {count} archives"+(f" · {imported} new" if imported else '')+f" · checked {checked}"+(f" · {len(info.get('errors') or [])} errors" if info.get('errors') else '')
        if state in ('stale','needs attention','status unavailable') or info.get('errors'):left.warning(summary)
        else:left.caption(summary)
        previous=st.session_state.setdefault('_cloud_seen',count)
        if count!=previous and right.button('Show updates',key='cloud_show_updates'):
            st.session_state['_cloud_seen']=count
            st.rerun()
with st.sidebar:
    with st.expander('Data collection'):
        cloud_sync_status()
        capture_summary=latest_cloud_capture()
        if capture_summary:st.caption(capture_summary)
        receipt=read_json(ROOT/'data'/'google_archive'/'receipt_status.json')
        verified=receipt.get('verified_at')
        if verified:
            try:
                receipt_time=datetime.fromisoformat(str(verified).replace('Z','+00:00'))
                if receipt_time.tzinfo is None:receipt_time=receipt_time.replace(tzinfo=timezone.utc)
                age_days=(datetime.now(timezone.utc)-receipt_time.astimezone(timezone.utc)).total_seconds()/86400
                if age_days>8:
                    st.warning('Local archive verification is overdue; Drive cleanup will retain unverified captures.')
                else:
                    st.caption('Local archive verified '+receipt_time.astimezone(IST).strftime('%d %b %H:%M IST'))
            except (ValueError,TypeError):st.caption('Local archive verification date unavailable')
        else:st.caption('No local archive verification receipt yet.')
        if capture and capture.get('status')=='recovered':
            st.caption('Local daily history through '+capture.get('recovered_through','unknown'))
        elif capture:
            st.caption('Laptop history backup · '+str(capture.get('status','unknown'))+' · '+str(capture.get('attempt',''))[:16].replace('T',' '))
        if backfill:
            st.caption('Historical archive download · '+str(backfill.get('status','unknown'))+' · '+str(backfill.get('stage','')))
        minute_status=read_json(ROOT/'data'/'backtest_1minute'/'status.json')
        if minute_status:
            jobs=minute_status.get('jobs') or {}
            current=minute_status.get('current') or {}
            progress=' · '.join(f'{label} {jobs[key]:,}' for label,key in [('done','done'),('queued','pending')] if isinstance(jobs.get(key),int))
            if minute_status.get('status')=='running':
                st.caption('Historical minute download · running'+(' · '+progress if progress else ''))
                if current.get('symbol'):
                    st.caption(f"Current: {current['symbol']} · {current.get('start','')} to {current.get('end','')}")
            elif minute_status.get('status'):
                st.caption('Historical minute download · '+str(minute_status.get('status')))


if capture.get('status') in ('failed','partial'):
    st.warning('Laptop history backup needs attention: '+'; '.join(capture.get('errors',[])[:2]))

markets=['Options','Futures']+(['Backtest'] if advanced else [])
if st.session_state.get('market_workspace') not in markets:st.session_state.market_workspace='Options'
workspace=st.segmented_control('Market',markets,label_visibility='collapsed',key='market_workspace') or 'Options'

from history_policy import register

dividend_ready=all(v is not None for v in dividend.values())
if dividend_ready:register(ROOT/'data'/'monitor.sqlite',rate,dividend,spread)

if workspace=='Backtest':
    import backtest_ui
    backtest_ui.render()
elif workspace=='Futures':

    import futures_ui

    futures_ui.render()

else:
    if not dividend_ready:st.info('Dividend inputs unavailable; broker data remains available, model scenarios are withheld.')
    monitor_ui.render_monitor(rate,dividend,spread)

