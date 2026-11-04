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
if not (ROOT/'market_data.py').exists() and (ROOT/'Volatility Lab'/'market_data.py').exists():
    ROOT=ROOT/'Volatility Lab'

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

         'trade_review_ui','research_health','restored_lab','options_context_cache','vol_surface_3d','monitor_ui','futures_data','futures_rolling','futures_sync','futures_view_cache','futures_ui','broker_charges','backtest_index','backtest_engine','backtest_signals','strategy_backtest','backtest_report','futures_backtest','backtest_jobs','futures_backtest_ui','backtest_ui')

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
div[data-testid="stRadio"] label{border:1px solid #26364d;border-radius:8px;padding:7px 14px;background:#131f31;font-size:.88rem}
div[data-testid="stRadio"] label:has(input:checked){border-color:#38bdf8;background:#172942;color:#38bdf8}
</style>''',unsafe_allow_html=True)

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
capture=read_json(STATE/'status.json')
backfill=read_json(ROOT/'data'/'upstox'/'collection_status.json')

rate_info=get_rate()
rate=rate_info.get('model_rate')
dividend_info=get_yields()
dividend={symbol:item.get('model_yield') for symbol,item in dividend_info.items()}
spread=3.0  # Stored-history compatibility; this does not filter broker IV.

st.session_state.pop('wide_atm_fallback',None)

if not ready:
    with st.expander('⚠️ Upstox API Setup / Token Refresh', expanded=False):
        from upstox_data import render_connection
        render_connection()

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
        left,right=st.columns([4,1])
        state,checked=drive_sync_label(info)
        imported=info.get('imported',0)
        summary=f"Drive · {state} · {count:,} archives"+(f" · {imported} new" if imported else '')+f" · checked {checked}"+(f" · {len(info.get('errors') or [])} errors" if info.get('errors') else '')
        if state in ('stale','needs attention','status unavailable') or info.get('errors'):left.warning(summary)
        else:left.caption(summary)
        now=datetime.now(IST)
        is_open=(now.weekday()<5) and (555<=now.hour*60+now.minute<=930)
        mkt_txt='● Market Live' if is_open else ('◑ Pre-Open' if (now.weekday()<5 and 540<=now.hour*60+now.minute<555) else '○ Market Closed')
        right.caption(f"{mkt_txt} · {now:%H:%M IST}")
        previous=st.session_state.setdefault('_cloud_seen',count)
        if count!=previous:
            st.session_state['_cloud_seen']=count
            st.rerun()

cloud_sync_status()

if capture.get('status') in ('failed','partial'):
    errors = capture.get('errors', [])
    is_normal_catchup = all('pending' in e.lower() or 'missing' in e.lower() for e in errors)
    if not is_normal_catchup and errors:
        st.warning('Laptop history backup: ' + '; '.join(errors[:2]))

workspace_labels={'Options':'Options','Backtest':'Backtest'}
label_to_mode={v:k for k,v in workspace_labels.items()}
raw_markets=['Options','Backtest']
markets=[workspace_labels[m] for m in raw_markets]

saved_ws=st.session_state.get('market_workspace')
if saved_ws in label_to_mode:current_label=saved_ws
elif saved_ws in workspace_labels:current_label=workspace_labels[saved_ws]
elif saved_ws in ('⚡ Options Terminal','📈 Futures Carry','🧪 Backtest Studio','Futures'):
    current_label='Backtest' if 'Backtest' in str(saved_ws) else 'Options'
else:current_label=workspace_labels['Options']

if current_label not in markets:current_label=markets[0]

chosen_label=st.segmented_control('Workspace',markets,default=current_label,label_visibility='collapsed',key='market_workspace_label') or current_label
workspace=label_to_mode.get(chosen_label,'Options')
st.session_state.market_workspace=workspace

from history_policy import register

dividend_ready=all(v is not None for v in dividend.values())
if dividend_ready:register(ROOT/'data'/'monitor.sqlite',rate,dividend,spread)

if workspace=='Backtest':
    import backtest_ui
    backtest_ui.render()
else:
    if not dividend_ready:st.info('Dividend inputs unavailable; broker data remains available, model scenarios are withheld.')
    monitor_ui.render_monitor(rate,dividend,spread)

