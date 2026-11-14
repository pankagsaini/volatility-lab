"""Standalone local options dashboard; no trading actions (Accelerated v7)."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT

from pathlib import Path
import importlib
import os
import gc
import gzip
import json
import time
import urllib.parse
from datetime import datetime, timezone
import streamlit as st
try:
    from market_data import MarketDataError, IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')
    class MarketDataError(RuntimeError): pass
from pricing_inputs import get_rate, get_yields

ROOT=PACKAGE_ROOT
if not (ROOT/'market_data.py').exists() and (ROOT/'Volatility Lab'/'market_data.py').exists():
    ROOT=ROOT/'Volatility Lab'

def _cached_pricing_inputs(ttl_seconds: float = 60.0):
    """Cache risk-free rate and dividend yields in session_state with file-mtime + TTL check."""
    now_mono = time.monotonic()
    r_path = runtime_data_dir() / 'risk_free_rate.json'
    d_path = runtime_data_dir() / 'dividend_yields.json'
    try:
        r_mtime = r_path.stat().st_mtime_ns if r_path.exists() else 0
    except OSError:
        r_mtime = 0
    try:
        d_mtime = d_path.stat().st_mtime_ns if d_path.exists() else 0
    except OSError:
        d_mtime = 0
    cache_entry = st.session_state.get('_pricing_inputs_cache')
    if cache_entry and (now_mono - cache_entry['ts'] < ttl_seconds) and cache_entry['mtimes'] == (r_mtime, d_mtime):
        return cache_entry['rate_info'], cache_entry['dividend_info']
    rate_info = get_rate()
    dividend_info = get_yields()
    st.session_state['_pricing_inputs_cache'] = {
        'ts': now_mono,
        'mtimes': (r_mtime, d_mtime),
        'rate_info': rate_info,
        'dividend_info': dividend_info,
    }
    return rate_info, dividend_info

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

st.set_page_config(page_title='Volatility Lab',page_icon='❖',layout='wide',initial_sidebar_state='collapsed')

try:
    import ui_theme
    ui_theme.install_classic_modern_theme()
except Exception:
    pass

st.session_state.pop('login_notice', None)

# Keep browser authentication intact while reloading changed calculation modules in local dev mode.
MODULES=(
    'market_data','dashboard_cache','catalogue_fetch','refresh_policy','upstox_data','upstox_adapter','broker_options','quote_status','data_accuracy','option_math','quote_quality','vol_lib','forecast','volatility_regime',
    'research_extensions','har_research','experimental_models','forecast_validation',
    'history_policy','option_context','dividend_yield','expiry_forecast','term_structure','corporate_event_calendar','smile','trade_review','snapshot_history',
    'market_table','relative_value','synthetic_future','expiry_data','model_view','research_ui',
    'trade_review_ui','research_health','restored_lab','options_context_cache','vol_surface_3d','monitor_ui','futures_data','futures_rolling','futures_sync','futures_view_cache','futures_ui','broker_charges','backtest_index','backtest_engine','backtest_signals','strategy_backtest','backtest_report','futures_backtest','backtest_jobs','futures_backtest_ui','backtest_ui','forward_test'
)

import monitor_ui

if os.environ.get('VOL_LAB_DEV') == '1':
    revision=tuple(Path(importlib.import_module(name).__file__).stat().st_mtime_ns for name in MODULES)
    if getattr(monitor_ui,'_revision',None)!=revision:
        if getattr(monitor_ui,'_revision',None) is not None:
            import sys
            for name in MODULES:
                if name in sys.modules:importlib.reload(sys.modules[name])
        monitor_ui._revision=revision
    if st.session_state.get('dashboard_revision')!=revision:
        st.session_state.dashboard_revision=revision
        st.session_state.expiry_initial_attempted=False

from collector_support import read_json, STATE
from upstox_data import TOKEN_PATH
from upstox_adapter import market_client

def _has_upstox_secret():
    try:
        return bool(hasattr(st, 'secrets') and 'UPSTOX_TOKEN' in st.secrets)
    except Exception:
        return False

from volatility_lab.runtime.settings import load_settings
settings = load_settings()
has_upstox_token = not settings.offline and (TOKEN_PATH.exists() or settings.upstox_token or _has_upstox_secret())

if has_upstox_token:
    token_mtime = TOKEN_PATH.stat().st_mtime_ns if TOKEN_PATH.exists() else 1
    version=(token_mtime,(ROOT/'ingestion/upstox_data.py').stat().st_mtime_ns,(ROOT/'ingestion/upstox_adapter.py').stat().st_mtime_ns)
    if st.session_state.get('_upstox_version')!=version:
        try:
            st.session_state.data_client=market_client()
            st.session_state._upstox_version=version
        except MarketDataError as error:st.error(str(error))

client=st.session_state.get('data_client') if has_upstox_token else None
if not has_upstox_token:
    st.session_state.pop('data_client', None)
    st.session_state.pop('_upstox_version', None)
if client is not None:
    from upstox_adapter import UpstoxAdapter
    client.__class__=UpstoxAdapter

ready=bool(client and client.authenticated)

rate_info, dividend_info = _cached_pricing_inputs()
rate=rate_info.get('model_rate')
dividend={symbol:item.get('model_yield') for symbol,item in dividend_info.items()}
spread=3.0  # Stored-history compatibility; this does not filter broker IV.

st.session_state.pop('wide_atm_fallback',None)

import google_archive_sync

workspace_labels={'Options':'Options','Backtest':'Backtest','Forward Test':'Forward Test (Auto Paper)'}
label_to_mode={v:k for k,v in workspace_labels.items()}
raw_markets=['Options','Backtest','Forward Test']
markets=[workspace_labels[m] for m in raw_markets]

saved_ws=st.session_state.get('market_workspace')
if saved_ws in label_to_mode:current_label=saved_ws
elif saved_ws in workspace_labels:current_label=workspace_labels[saved_ws]
elif saved_ws in ('⚡ Options Terminal','📈 Futures Carry','🧪 Backtest Studio','Futures'):
    current_label='Backtest' if 'Backtest' in str(saved_ws) else 'Options'
else:current_label=workspace_labels['Options']

if current_label not in markets:current_label=markets[0]

# Undistorted Institutional Masthead Header with compact top-right Icon Toolbar (📱 App, 🔗 Share, ★ Star, ✎ Edit) + Market Status
now = datetime.now(IST)
is_open = (now.weekday() < 5) and (555 <= now.hour * 60 + now.minute <= 930)
is_pre = (now.weekday() < 5) and (540 <= now.hour * 60 + now.minute < 555)
mkt_color = "#34d399" if is_open else ("#fbbf24" if is_pre else "#94a3b8")
mkt_border = "rgba(16, 185, 129, 0.42)" if is_open else ("rgba(245, 158, 11, 0.42)" if is_pre else "rgba(148, 163, 184, 0.28)")
mkt_bg = "rgba(16, 185, 129, 0.12)" if is_open else ("rgba(245, 158, 11, 0.12)" if is_pre else "rgba(15, 23, 42, 0.85)")
mkt_label = "● Market Live" if is_open else ("◐ Pre-Open" if is_pre else "○ Market Closed")

cloud_url = "https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/"
repo_url = "https://github.com/pankagsaini/volatility-lab"
edit_url = "https://github.com/pankagsaini/volatility-lab/edit/master/volatility_lab/ui/dashboard.py"
try:
    lan_ip = ui_theme.get_lan_ip()
except Exception:
    lan_ip = "127.0.0.1"
lan_url = f"http://{lan_ip}:8501"
qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=160x160&margin=6&data={urllib.parse.quote(cloud_url)}"

st.markdown(
    f"""
    <div class="vl-masthead">
      <div class="vl-masthead-left">
        <span class="vl-brand-crest"><span class="vl-brand-violet">❖</span>Volatility Lab</span>
        <span class="vl-brand-sub">NSE Index Volatility &amp; Relative Value</span>
      </div>
      <div class="vl-pill-row">
        <div class="vl-Quick-actions">
          <details class="vl-app-qr-dropdown">
            <summary class="vl-icon-action" title="Open on any phone, tablet, or app">📱 App</summary>
            <div class="vl-app-qr-card">
              <div style="font-size:0.74rem; font-weight:700; color:#a78bfa; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:6px;">📱 Open on Any Device</div>
              <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                <img src="{qr_url}" width="78" height="78" style="border-radius:6px; border:1px solid #334155; flex-shrink:0;" alt="QR" onerror="this.style.display='none'" />
                <div style="font-size:0.72rem; color:#cbd5e1; line-height:1.38;">
                  Scan with phone camera or <a href="{cloud_url}" target="_blank" style="color:#38bdf8; font-weight:600;">open Cloud App ↗</a><br/>
                  <span style="color:#94a3b8; font-size:0.68rem;">Tip: Tap <b>Add to Home Screen</b> in Safari/Chrome to install as an app.</span>
                </div>
              </div>
              <div style="font-family:var(--vl-font-mono, monospace); font-size:0.68rem; color:#94a3b8; background:#070b12; border:1px solid #1e293b; border-radius:6px; padding:5px 8px;">
                Wi-Fi LAN: <span style="color:#38bdf8;">{lan_url}</span>
              </div>
            </div>
          </details>
          <a class="vl-icon-action" href="{cloud_url}" target="_blank" title="Share live Streamlit app link">🔗 Share</a>
          <a class="vl-icon-action" href="{repo_url}" target="_blank" title="Star repository on GitHub">★ Star</a>
          <a class="vl-icon-action" href="{edit_url}" target="_blank" title="Edit on GitHub">✎ Edit</a>
        </div>
        <span style="display:inline-block; padding:5px 13px; border-radius:999px; font-family:var(--vl-font-mono, monospace); font-size:0.73rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border:1px solid {mkt_border}; background:{mkt_bg}; color:{mkt_color}; white-space:nowrap;">
          {mkt_label} · {now:%H:%M IST}
        </span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

chosen_label = st.segmented_control('Workspace', markets, default=current_label, label_visibility='collapsed', key='market_workspace_label') or current_label
workspace = label_to_mode.get(chosen_label, 'Options')
st.session_state.market_workspace = workspace

@st.fragment(run_every=30)
def cloud_sync_status():
    if not settings.offline:
        google_archive_sync.ensure_sync(rate, dividend, spread)
    info = read_json(runtime_data_dir() / 'google_archive' / 'status.json')
    if info and (info.get('total_files') or info.get('updated')):
        sync_version = (info.get('total_files', 0), info.get('updated', ''))
        previous = st.session_state.setdefault('_cloud_seen', sync_version)
        if sync_version != previous:
            st.session_state['_cloud_seen'] = sync_version
            gc.collect()
            st.rerun()

cloud_sync_status()

from history_policy import register

dividend_ready=all(v is not None for v in dividend.values())
if dividend_ready:
    reg_sig = (rate, tuple(sorted(dividend.items())), spread)
    if st.session_state.get('_registered_pricing_sig') != reg_sig:
        register(runtime_data_dir()/'monitor.sqlite',rate,dividend,spread)
        st.session_state['_registered_pricing_sig'] = reg_sig

if workspace=='Backtest':
    import backtest_ui
    backtest_ui.render()
elif workspace=='Forward Test':
    import forward_test
    forward_test.render_forward_test_ui(runtime_data_dir())
else:
    if not dividend_ready:st.info('Dividend inputs unavailable; broker data remains available, model scenarios are withheld.')
    monitor_ui.render_monitor(rate,dividend,spread)

def main():
    """Entrypoint hook for direct module execution from streamlit_app.py."""
    pass
