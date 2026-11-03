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
.stApp{background:#080c14;color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
[data-testid="stHeader"]{background:#080c14!important;border-bottom:none!important;height:0!important;min-height:0!important;visibility:hidden!important}
[data-testid="stSidebar"]{background:#0b111c!important;border-right:1px solid #141e30}
.block-container{max-width:1500px;padding-top:1.5rem!important;padding-bottom:2.5rem;padding-left:1.5rem;padding-right:1.5rem}

/* Typography & Numbers */
h1,h2,h3,h4{letter-spacing:-0.035em;color:#f8fafc;font-weight:600}
div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-weight:700;color:#f8fafc;font-size:1.35rem}
div[data-testid="stMetricLabel"]{color:#94a3b8;font-size:0.76rem;text-transform:uppercase;letter-spacing:0.06em;font-weight:600}

/* Cards & Containers */
div[data-testid="stMetric"]{background:linear-gradient(145deg,#0f1726 0%,#0c121e 100%);border:1px solid #1a273e;border-radius:12px;padding:12px 16px;box-shadow:0 4px 14px rgba(0,0,0,0.28)}
[data-testid="stDataFrame"]{border:1px solid #1a273e;border-radius:12px;overflow:hidden;background:#0c121e}
[data-testid="stPlotlyChart"]{border:1px solid #1a273e;border-radius:14px;padding:8px;background:#090e17;box-shadow:0 4px 18px rgba(0,0,0,0.3)}
[data-testid="stExpander"]{border:1px solid #1a273e!important;border-radius:10px!important;background:#0c1320!important}
[data-testid="stPopoverButton"]{border-radius:8px!important;background:#111927!important;border:1px solid #1f2f48!important;color:#cbd5e1!important}

/* Navigation Segmented Control */
[data-testid="stSegmentedControl"]{background:#0c1320;border:1px solid #1a273e;border-radius:10px;padding:4px}
[data-testid="stSegmentedControl"] button{border-radius:8px!important;font-size:0.86rem!important;font-weight:600!important;transition:all 0.15s ease}
[data-testid="stSegmentedControl"] button[aria-selected="true"]{background:#16243b!important;color:#38bdf8!important;box-shadow:0 2px 8px rgba(0,210,255,0.15);border:1px solid #0284c7!important}

/* Radio buttons & Pills */
div[data-testid="stRadio"] [role="radiogroup"]{gap:0.45rem}
div[data-testid="stRadio"] label{border:1px solid #1a273e;border-radius:8px;padding:7px 14px;background:#0f1726;font-size:0.84rem}
div[data-testid="stRadio"] label:has(input:checked){border-color:#06b6d4;background:#0f2738;color:#38bdf8}

/* Buttons */
button[kind="primary"]{border-radius:8px;background:#0284c7;border:none;font-weight:600}
button[kind="secondary"]{border-radius:8px;background:#111927;border:1px solid #1f2f48;color:#cbd5e1}

hr{border-color:#182335!important;margin:1.2rem 0!important}
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

def render_terminal_header():
    now=datetime.now(IST)
    t_min=now.hour*60+now.minute
    is_open=(now.weekday()<5) and (555<=t_min<=930)
    
    if is_open:
        status_pill='<span style="background:rgba(16,185,129,0.12); color:#10b981; border:1px solid #10b981; padding:4px 10px; border-radius:9999px; font-size:0.75rem; font-weight:700; letter-spacing:0.04em;">● NSE LIVE · REGULAR SESSION</span>'
    elif now.weekday()<5 and 540<=t_min<555:
        status_pill='<span style="background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid #f59e0b; padding:4px 10px; border-radius:9999px; font-size:0.75rem; font-weight:700;">◑ NSE PRE-OPEN</span>'
    else:
        status_pill='<span style="background:rgba(148,163,184,0.12); color:#94a3b8; border:1px solid #334155; padding:4px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600;">○ NSE CLOSED · POST-MARKET</span>'
        
    ist_time=now.strftime('%d %b %H:%M IST')
    
    st.markdown(f'''
    <div style="display:flex; justify-content:space-between; align-items:flex-end; padding-bottom:10px; border-bottom:1px solid #162236; margin-bottom:12px;">
        <div>
            <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:1.85rem; font-weight:800; letter-spacing:-0.03em; color:#f8fafc; font-family:-apple-system,sans-serif;">
                    <span style="color:#00d2ff;">◈</span> VOLATILITY LAB
                </span>
                <span style="background:#0f2038; color:#38bdf8; border:1px solid #0284c7; padding:2px 8px; border-radius:6px; font-size:0.68rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase;">
                    PRO TERMINAL
                </span>
            </div>
            <div style="color:#64748b; font-size:0.78rem; margin-top:2px; font-weight:500;">
                Institutional Volatility Surface, Forecasting & Derivatives Intelligence
            </div>
        </div>
        <div style="text-align:right;">
            {status_pill}
            <div style="color:#64748b; font-size:0.72rem; margin-top:4px; font-variant-numeric:tabular-nums;">
                {ist_time}
            </div>
        </div>
    </div>
    ''', unsafe_allow_html=True)

    from expiry_data import load_snapshot
    import numpy as np
    snap=load_snapshot(None,allow_saved=True) or {}
    rows=snap.get('rows',[])
    nifty=next((r for r in rows if r.get('Instrument')=='NIFTY'),{})
    bn=next((r for r in rows if r.get('Instrument')=='BANKNIFTY'),{})
    vix_val=snap.get('india_vix',{}).get('value')
    info=read_json(ROOT/'data'/'google_archive'/'status.json')
    sync_state,_=drive_sync_label(info)
    sync_count=info.get('total_files',0)

    c1,c2,c3,c4=st.columns(4)

    with c1:
        spot=nifty.get('Price')
        chg=nifty.get('% change')
        iv=nifty.get('IV')
        sprd=nifty.get('IV-RV Spread')
        chg_color='#10b981' if chg and chg>=0 else '#ef4444'
        chg_arrow='▲' if chg and chg>=0 else '▼'
        st.markdown(f'''
        <div style="background:#0f1726; border:1px solid #1a273e; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.86rem;">NIFTY 50</span>
                <span style="color:{chg_color}; font-size:0.75rem; font-weight:700;">{chg_arrow} {chg:+.2f}%</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {f"{spot:,.1f}" if spot else "—"}
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b; margin-top:4px;">
                <span>ATM IV: <b style="color:#38bdf8;">{f"{iv:.1f}%" if iv else "—"}</b></span>
                <span>Spread: <b style="color:#cbd5e1;">{f"{sprd:+.1f} pp" if sprd is not None and np.isfinite(sprd) else "—"}</b></span>
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c2:
        spot_bn=bn.get('Price')
        chg_bn=bn.get('% change')
        iv_bn=bn.get('IV')
        sprd_bn=bn.get('IV-RV Spread')
        chg_color_bn='#10b981' if chg_bn and chg_bn>=0 else '#ef4444'
        chg_arrow_bn='▲' if chg_bn and chg_bn>=0 else '▼'
        st.markdown(f'''
        <div style="background:#0f1726; border:1px solid #1a273e; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.86rem;">BANKNIFTY</span>
                <span style="color:{chg_color_bn}; font-size:0.75rem; font-weight:700;">{chg_arrow_bn} {chg_bn:+.2f}%</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {f"{spot_bn:,.1f}" if spot_bn else "—"}
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b; margin-top:4px;">
                <span>ATM IV: <b style="color:#818cf8;">{f"{iv_bn:.1f}%" if iv_bn else "—"}</b></span>
                <span>Spread: <b style="color:#cbd5e1;">{f"{sprd_bn:+.1f} pp" if sprd_bn is not None and np.isfinite(sprd_bn) else "—"}</b></span>
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c3:
        vix_desc='Low Vol' if vix_val and vix_val<13 else ('Normal' if vix_val and vix_val<=18 else 'Elevated')
        vix_color='#10b981' if vix_val and vix_val<=15 else ('#f59e0b' if vix_val and vix_val<=20 else '#ef4444')
        st.markdown(f'''
        <div style="background:#0f1726; border:1px solid #1a273e; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.86rem;">INDIA VIX</span>
                <span style="color:{vix_color}; font-size:0.72rem; font-weight:700;">{vix_desc}</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {f"{vix_val:.2f}" if vix_val else "—"}
            </div>
            <div style="font-size:0.72rem; color:#64748b; margin-top:4px;">
                Benchmark Volatility Index
            </div>
        </div>
        ''', unsafe_allow_html=True)

    with c4:
        sync_badge_color='#10b981' if sync_state=='healthy' else ('#f59e0b' if sync_state=='syncing' else '#64748b')
        st.markdown(f'''
        <div style="background:#0f1726; border:1px solid #1a273e; border-radius:10px; padding:10px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f8fafc; font-size:0.86rem;">CLOUD MIRROR</span>
                <span style="color:{sync_badge_color}; font-size:0.72rem; font-weight:700; text-transform:uppercase;">● {sync_state}</span>
            </div>
            <div style="font-size:1.25rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums; margin-top:2px;">
                {sync_count:,} <span style="font-size:0.76rem; font-weight:500; color:#64748b;">archives</span>
            </div>
            <div style="font-size:0.72rem; color:#64748b; margin-top:4px;">
                3-min snapshot pipeline
            </div>
        </div>
        ''', unsafe_allow_html=True)

render_terminal_header()

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
    errors = capture.get('errors', [])
    now_ist = datetime.now(IST)
    is_evening_pending = (
        all('pending' in e.lower() or 'missing' in e.lower() for e in errors)
        and (16 <= now_ist.hour < 21)
    )
    if is_evening_pending:
        st.info('Laptop history backup: waiting for Upstox evening candle publication (scheduled retries active).')
    else:
        st.warning('Laptop history backup needs attention: ' + '; '.join(errors[:2]))

workspace_labels={'Options':'⚡ Options Terminal','Futures':'📈 Futures Carry','Backtest':'🧪 Backtest Studio'}
label_to_mode={v:k for k,v in workspace_labels.items()}
raw_markets=['Options','Futures']+(['Backtest'] if advanced else [])
markets=[workspace_labels[m] for m in raw_markets]

saved_ws=st.session_state.get('market_workspace')
if saved_ws in label_to_mode:current_label=saved_ws
elif saved_ws in workspace_labels:current_label=workspace_labels[saved_ws]
else:current_label=workspace_labels['Options']

if current_label not in markets:current_label=markets[0]

chosen_label=st.segmented_control('Market',markets,default=current_label,label_visibility='collapsed',key='market_workspace_label') or current_label
workspace=label_to_mode.get(chosen_label,'Options')
st.session_state.market_workspace=workspace

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

