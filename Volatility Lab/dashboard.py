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

def render_executive_header():
    now = datetime.now(IST)
    t_min = now.hour * 60 + now.minute
    is_open = (now.weekday() < 5) and (555 <= t_min <= 930)

    if is_open:
        status_pill = '<span style="background:rgba(16,185,129,0.12); color:#10b981; border:1px solid #10b981; padding:3px 10px; border-radius:9999px; font-size:0.72rem; font-weight:600; letter-spacing:0.02em;">● LIVE</span>'
    elif now.weekday() < 5 and 540 <= t_min < 555:
        status_pill = '<span style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid #f59e0b; padding:3px 10px; border-radius:9999px; font-size:0.72rem; font-weight:600;">◑ PRE-OPEN</span>'
    else:
        status_pill = '<span style="background:rgba(148,163,184,0.12); color:#94a3b8; border:1px solid #334155; padding:3px 10px; border-radius:9999px; font-size:0.72rem; font-weight:500;">○ CLOSED</span>'

    ist_time = now.strftime('%d %b %H:%M IST')

    st.markdown(f'''
    <div style="display:flex; justify-content:space-between; align-items:center; padding-bottom:8px; border-bottom:1px solid #1e2c40; margin-bottom:12px;">
        <div style="display:flex; align-items:center; gap:12px;">
            <span style="font-size:1.85rem; font-weight:700; letter-spacing:-0.04em; color:#f8fafc;">Volatility Lab</span>
            {status_pill}
        </div>
        <div style="color:#64748b; font-size:0.8rem; font-variant-numeric:tabular-nums;">
            {ist_time}
        </div>
    </div>
    ''', unsafe_allow_html=True)

    from expiry_data import load_snapshot
    import numpy as np
    snap = load_snapshot(None, allow_saved=True) or {}
    rows = snap.get('rows', [])
    nifty = next((r for r in rows if r.get('Instrument') == 'NIFTY'), {})
    bn = next((r for r in rows if r.get('Instrument') == 'BANKNIFTY'), {})

    def _num(v):
        if v is None:
            return None
        try:
            f = float(v)
            return f if np.isfinite(f) else None
        except (ValueError, TypeError):
            return None

    vix_val = snap.get('india_vix', {}).get('value')
    if vix_val is None:
        vix_val = read_json(ROOT / 'data' / 'upstox' / 'india_vix.json').get('price')

    info = read_json(ROOT / 'data' / 'google_archive' / 'status.json')
    sync_state, sync_checked = drive_sync_label(info)
    sync_count = info.get('total_files', 0)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        spot = _num(nifty.get('Price'))
        fut = _num(nifty.get('Month future LTP') or nifty.get('Listed future'))
        syn = _num(nifty.get('Synthetic future'))
        chg = _num(nifty.get('% change'))
        iv = _num(nifty.get('IV'))
        chg_color = '#10b981' if (chg is not None and chg >= 0) else ('#ef4444' if (chg is not None and chg < 0) else '#94a3b8')
        chg_arrow = '▲' if (chg is not None and chg >= 0) else ('▼' if (chg is not None and chg < 0) else '')
        chg_txt = f"{chg_arrow} {chg:+.2f}%" if chg is not None else "—"
        st.markdown(f'''
        <div style="background:#131f31; border:1px solid #26364d; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.88rem;">NIFTY</span>
                <span style="color:{chg_color}; font-size:0.75rem; font-weight:700;">{chg_txt}</span>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:2px;">
                <span style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums;">
                    {f"{spot:,.1f}" if spot is not None else "—"}
                </span>
                <span style="font-size:0.72rem; color:#94a3b8; font-variant-numeric:tabular-nums;">
                    Fut: <b style="color:#38bdf8;">{f"{fut:,.1f}" if fut is not None else "—"}</b>
                </span>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b; margin-top:4px;">
                <span>Syn: <b style="color:#a78bfa;">{f"{syn:,.1f}" if syn is not None else "—"}</b></span>
                <span>ATM IV: <b style="color:#38bdf8;">{f"{iv:.1f}%" if iv is not None else "—"}</b></span>
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c2:
        spot_bn = _num(bn.get('Price'))
        fut_bn = _num(bn.get('Month future LTP') or bn.get('Listed future'))
        syn_bn = _num(bn.get('Synthetic future'))
        chg_bn = _num(bn.get('% change'))
        iv_bn = _num(bn.get('IV'))
        chg_color_bn = '#10b981' if (chg_bn is not None and chg_bn >= 0) else ('#ef4444' if (chg_bn is not None and chg_bn < 0) else '#94a3b8')
        chg_arrow_bn = '▲' if (chg_bn is not None and chg_bn >= 0) else ('▼' if (chg_bn is not None and chg_bn < 0) else '')
        chg_txt_bn = f"{chg_arrow_bn} {chg_bn:+.2f}%" if chg_bn is not None else "—"
        st.markdown(f'''
        <div style="background:#131f31; border:1px solid #26364d; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.88rem;">BANKNIFTY</span>
                <span style="color:{chg_color_bn}; font-size:0.75rem; font-weight:700;">{chg_txt_bn}</span>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:2px;">
                <span style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums;">
                    {f"{spot_bn:,.1f}" if spot_bn is not None else "—"}
                </span>
                <span style="font-size:0.72rem; color:#94a3b8; font-variant-numeric:tabular-nums;">
                    Fut: <b style="color:#38bdf8;">{f"{fut_bn:,.1f}" if fut_bn is not None else "—"}</b>
                </span>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b; margin-top:4px;">
                <span>Syn: <b style="color:#a78bfa;">{f"{syn_bn:,.1f}" if syn_bn is not None else "—"}</b></span>
                <span>ATM IV: <b style="color:#38bdf8;">{f"{iv_bn:.1f}%" if iv_bn is not None else "—"}</b></span>
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c3:
        vix_f = _num(vix_val)
        vix_desc = 'Low Vol' if (vix_f is not None and vix_f < 13) else ('Normal' if (vix_f is not None and vix_f <= 18) else ('Elevated' if vix_f is not None else '—'))
        vix_color = '#10b981' if (vix_f is not None and vix_f <= 15) else ('#f59e0b' if (vix_f is not None and vix_f <= 20) else ('#ef4444' if vix_f is not None else '#94a3b8'))
        st.markdown(f'''
        <div style="background:#131f31; border:1px solid #26364d; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.88rem;">INDIA VIX</span>
                <span style="color:{vix_color}; font-size:0.72rem; font-weight:700;">{vix_desc}</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {f"{vix_f:.2f}" if vix_f is not None else "—"}
            </div>
            <div style="font-size:0.72rem; color:#64748b; margin-top:4px;">
                NSE Volatility Index
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c4:
        sync_badge_color = '#10b981' if sync_state == 'healthy' else ('#f59e0b' if sync_state == 'syncing' else '#64748b')
        st.markdown(f'''
        <div style="background:#131f31; border:1px solid #26364d; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.88rem;">CLOUD MIRROR</span>
                <span style="color:{sync_badge_color}; font-size:0.72rem; font-weight:700; text-transform:uppercase;">● {sync_state}</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {sync_count:,} <span style="font-size:0.76rem; font-weight:500; color:#64748b;">archives</span>
            </div>
            <div style="font-size:0.72rem; color:#64748b; margin-top:4px;">
                Google Drive 5TB storage
            </div>
        </div>
        ''', unsafe_allow_html=True)

render_executive_header()



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

