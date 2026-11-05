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

MODULES=('market_data','dashboard_cache','catalogue_fetch','refresh_policy','upstox_data','upstox_adapter','broker_options','quote_status','data_accuracy','option_math','quote_quality','vol_lib','vol_surface_3d','research_extensions','har_research','experimental_models','forecast_validation','history_policy','option_context','dividend_yield','expiry_forecast','term_structure','corporate_event_calendar','smile','trade_review','snapshot_history','market_table','relative_value','synthetic_future','expiry_data','model_view','research_ui','trade_review_ui','research_health','restored_lab','options_context_cache','monitor_ui','futures_data','futures_rolling','futures_sync','futures_view_cache','futures_ui','background_refresh','google_archive_sync')

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
:root {
    --bg: #0b1220;
    --bg-strong: #0d1728;
    --panel: #101d30;
    --panel-soft: #13233a;
    --panel-2: #152b43;
    --border: #243447;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --primary: #38bdf8;
    --primary-strong: #60a5fa;
    --success: #34d399;
    --warning: #fbbf24;
    --danger: #f87171;
    --info: #7dd3fc;
}

.stApp {
    background: var(--bg);
    color: var(--text);
}

[data-testid="stHeader"] {
    background: rgba(11, 18, 32, 0.8);
    box-shadow: none;
}

.block-container {
    max-width: 1480px;
    padding-top: 1.1rem;
    padding-bottom: 2rem;
    padding-left: 1.2rem;
    padding-right: 1.2rem;
}

section[data-testid="stSidebar"] {
    background: #0f1829;
    border-right: 1px solid var(--border);
}

[data-testid="stMetric"] {
    background: linear-gradient(180deg, #122235 0%, #0f1b2d 100%);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
}

[data-testid="stMetricLabel"] {
    color: var(--muted);
    font-size: .72rem;
    letter-spacing: .08em;
    text-transform: uppercase;
}

[data-testid="stMetricValue"] {
    font-weight: 700;
    letter-spacing: -0.04em;
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    background: #0d1a2b;
}

[data-testid="stPlotlyChart"] {
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel-soft);
    padding: 8px;
}

[data-testid="stExpander"] {
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel-soft);
    overflow: hidden;
}

[data-testid="stSegmentedControl"] button {
    border-radius: 8px !important;
    background: #122236;
    color: var(--text);
    border: 1px solid var(--border);
    font-size: .84rem !important;
}

[data-testid="stSegmentedControl"] button[data-checked="true"] {
    background: rgba(56, 189, 248, 0.12);
    border-color: rgba(56, 189, 248, 0.9);
    color: var(--primary);
}

button[kind="primary"] {
    border-radius: 8px;
    background: linear-gradient(180deg, #15314b 0%, #0d1d30 100%);
    border: 1px solid rgba(96, 165, 250, 0.6);
    color: var(--text);
}

button[kind="primary"]:hover {
    border-color: rgba(56, 189, 248, 0.9);
}

[data-testid="stVerticalBlock"] {
    gap: .75rem;
}

h1 {
    margin-bottom: 0 !important;
    letter-spacing: -0.06em;
    font-size: 2.2rem !important;
    color: var(--text);
}

h2, h3 {
    letter-spacing: -0.035em;
    color: var(--text);
}

.stCaptionContainer {
    color: var(--muted);
}

div[data-testid="stRadio"] label {
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel);
    padding: 7px 12px;
    color: var(--text);
}

div[data-testid="stRadio"] label:has(input:checked) {
    border-color: rgba(56, 189, 248, 0.7);
    background: rgba(56, 189, 248, 0.08);
    color: var(--primary);
}

.page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.6rem;
}

.header-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.45rem 0.8rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid var(--border);
    background: rgba(19, 34, 51, 0.9);
    color: var(--text);
}

.header-chip.live {
    color: var(--success);
    border-color: rgba(52, 211, 153, 0.4);
    background: rgba(52, 211, 153, 0.08);
}

.header-chip.closed {
    color: var(--muted);
    border-color: var(--border);
}

.header-chip.preopen {
    color: var(--warning);
    border-color: rgba(251, 191, 36, 0.45);
    background: rgba(251, 191, 36, 0.08);
}

.sync-status {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.7rem 0.85rem;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: rgba(17, 28, 41, 0.9);
    color: var(--text);
    font-size: .8rem;
}

.sync-status strong {
    color: var(--primary);
}

.sync-status.warning {
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.06);
}

.sync-status.stale {
    border-color: rgba(248, 113, 113, 0.35);
    background: rgba(248, 113, 113, 0.06);
}

.sync-status.healthy {
    border-color: rgba(52, 211, 153, 0.25);
    background: rgba(52, 211, 153, 0.06);
}

.sync-status .label {
    color: var(--muted);
    letter-spacing: .05em;
    text-transform: uppercase;
    font-size: .68rem;
}

.warning {
    background: rgba(251, 191, 36, 0.08);
    border: 1px solid rgba(251, 191, 36, 0.35);
    color: #fcd34d;
}

.success {
    background: rgba(52, 211, 153, 0.08);
    border: 1px solid rgba(52, 211, 153, 0.35);
    color: #6ee7b7;
}

.error {
    background: rgba(248, 113, 113, 0.08);
    border: 1px solid rgba(248, 113, 113, 0.35);
    color: #fca5a5;
}
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

header_cols = st.columns([4, 2])
with header_cols[0]:
    st.title('Volatility Lab')
with header_cols[1]:
    now=datetime.now(IST)
    is_open=(now.weekday()<5) and (555<=now.hour*60+now.minute<=930)
    if is_open:
        status_label='● Market Live'
        status_class='live'
    elif (now.weekday()<5 and 540<=now.hour*60+now.minute<555):
        status_label='◑ Pre-Open'
        status_class='preopen'
    else:
        status_label='○ Market Closed'
        status_class='closed'
    st.markdown(f'<div class="header-chip {status_class}">{status_label}</div>', unsafe_allow_html=True)


import google_archive_sync
@st.fragment(run_every=30)
def cloud_sync_status():
    google_archive_sync.ensure_sync(rate,dividend,spread)
    info=read_json(ROOT/'data'/'google_archive'/'status.json')
    if not info:
        st.warning('Drive sync status unavailable.')
    elif info.get('status')=='waiting':
        st.markdown('<div class="sync-status"><div><span class="label">Drive</span><br>Waiting for Google Drive</div></div>', unsafe_allow_html=True)
    else:
        count=info.get('total_files',0)
        state,checked=drive_sync_label(info)
        imported=info.get('imported',0)
        summary=f"Drive · {state} · {count:,} archives" + (f" · {imported} new" if imported else '') + f" · checked {checked}"
        status_class='healthy'
        if state in ('stale','needs attention','status unavailable') or info.get('errors'):
            status_class='warning' if state!='stale' else 'stale'
            st.markdown(f'<div class="sync-status {status_class}"><div><span class="label">Drive status</span><br>{summary}</div></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="sync-status {status_class}"><div><span class="label">Drive status</span><br>{summary}</div></div>', unsafe_allow_html=True)
        previous=st.session_state.setdefault('_cloud_seen',count)
        if count!=previous:
            st.session_state['_cloud_seen']=count
            st.rerun()

cloud_sync_status()

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

