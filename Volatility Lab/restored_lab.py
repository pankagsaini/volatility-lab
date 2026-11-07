"""Recovered research views; no data fabrication or trading operations (Vectorized & Cached)."""
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.special import ndtr
import plotly.graph_objects as go
import streamlit as st
from market_data import IST
from trade_review_ui import table

_INV_SQRT_2PI = 0.3989422804014327


def _clean_float(val, fallback=0.0):
    try:
        f = float(val)
        return f if np.isfinite(f) else fallback
    except (TypeError, ValueError):
        return fallback


def _sqlite_rev(db_path: Path):
    """Return revision tuple for SQLite database + WAL sidecar so cache invalidates on any write."""
    revs = []
    for p in (Path(db_path), Path(str(db_path) + "-wal")):
        try:
            st_info = p.stat()
            revs.append((st_info.st_mtime_ns, st_info.st_size))
        except OSError:
            revs.append(None)
    return tuple(revs)


@st.cache_data(show_spinner=False, max_entries=4, ttl=300)
def _cached_intraday_snapshots(db_path_str: str, symbol: str, expiry: str, config: str, db_rev: tuple):
    from snapshot_history import load
    return load(Path(db_path_str), symbol, expiry, config)


def compute_straddle_greeks(spot, strike, dte, vol_pct, rate=0.065):
    try:
        spot = float(spot)
        strike = float(strike)
        dte = float(dte)
        vol_pct = float(vol_pct)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(spot) and spot > 0 and np.isfinite(strike) and strike > 0 and np.isfinite(vol_pct) and vol_pct > 0):
        return None
    T = max(0.2, dte) / 365.0
    sigma = max(0.01, vol_pct / 100.0)
    try:
        sqrt_t = math.sqrt(T)
        d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        nd1 = float(ndtr(d1))
        nd2 = float(ndtr(d2))
        pdf_d1 = _INV_SQRT_2PI * math.exp(-0.5 * d1 * d1)
        df_r = math.exp(-rate * T)
        call_p = spot * nd1 - strike * df_r * nd2
        put_p = strike * df_r * (1.0 - nd2) - spot * (1.0 - nd1)
        straddle_p = call_p + put_p
        call_d = nd1
        put_d = call_d - 1.0
        gamma = pdf_d1 / (spot * sigma * sqrt_t)
        call_vega = spot * pdf_d1 * sqrt_t / 100.0
        straddle_vega = call_vega * 2.0
        theta_day = -(spot * pdf_d1 * sigma / (2.0 * sqrt_t)) / 365.0
        straddle_theta = theta_day * 2.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return {
        'strike': strike,
        'straddle': straddle_p,
        'straddle_pct': (straddle_p / spot) * 100.0,
        'call_delta': call_d,
        'put_delta': put_d,
        'net_delta': call_d + put_d,
        'gamma': gamma,
        'vega': straddle_vega,
        'theta_day': straddle_theta
    }


def _compute_straddle_series_vectorized(prices: np.ndarray, strikes: np.ndarray, dtes: np.ndarray, ivs: np.ndarray, rate: float = 0.065):
    """Vectorized BSM ATM Straddle Price and Net Delta across an entire intraday session array."""
    valid = np.isfinite(prices) & (prices > 0) & np.isfinite(strikes) & (strikes > 0) & np.isfinite(ivs) & (ivs > 0)
    straddle_out = np.full(len(prices), np.nan, dtype=float)
    delta_out = np.full(len(prices), np.nan, dtype=float)
    if not np.any(valid):
        return straddle_out, delta_out

    s = prices[valid]
    k = strikes[valid]
    t = np.maximum(0.2, dtes[valid]) / 365.0
    sigma = np.maximum(0.01, ivs[valid] / 100.0)
    sqrt_t = np.sqrt(t)
    d1 = (np.log(s / k) + (rate + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = ndtr(d1)
    nd2 = ndtr(d2)
    df_r = np.exp(-rate * t)
    call_p = s * nd1 - k * df_r * nd2
    put_p = k * df_r * (1.0 - nd2) - s * (1.0 - nd1)
    straddle_out[valid] = call_p + put_p
    delta_out[valid] = 2.0 * nd1 - 1.0
    return straddle_out, delta_out


def render_archived_intraday(data, symbol, selected, config):
    spot = _clean_float(selected.get('Price'))
    iv = _clean_float(selected.get('IV'))
    step = 50 if symbol == 'NIFTY' else 100
    atm_k = _clean_float(selected.get('ATM strike'))
    if atm_k > 0:
        strike = atm_k
    elif spot > 0:
        strike = round(spot / step) * step
    else:
        strike = 0.0

    dte = 7.0
    if selected.get('DTE') is not None:
        dte = _clean_float(selected.get('DTE'), 7.0)
    elif selected.get('Expiry') and selected.get('As of'):
        try:
            exp_d = pd.Timestamp(selected['Expiry']).date()
            asof_d = pd.Timestamp(selected['As of']).date()
            dte = max(0.2, float((exp_d - asof_d).days))
        except Exception:
            dte = 7.0

    # 1. Load Intraday History from monitor.sqlite (cached by WAL revision)
    db_file = data / 'monitor.sqlite'
    saved = _cached_intraday_snapshots(
        str(db_file), symbol, str(selected.get('Expiry') or ''), str(config or ''), _sqlite_rev(db_file)
    ).copy()

    if (spot <= 0 or iv <= 0) and not saved.empty:
        valid_prices = pd.to_numeric(saved['Price'], errors='coerce').dropna()
        valid_ivs = pd.to_numeric(saved['IV'], errors='coerce').dropna()
        if spot <= 0 and not valid_prices.empty:
            spot = float(valid_prices.iloc[-1])
            if strike <= 0:
                strike = round(spot / step) * step
        if iv <= 0 and not valid_ivs.empty:
            iv = float(valid_ivs.iloc[-1])

    # 2. Top KPI Metric Cards for Live ATM Straddle & Greeks
    greeks = compute_straddle_greeks(spot, strike, dte, iv)
    if greeks:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("ATM Strike", f"{int(round(greeks['strike'])):,}")
        c2.metric("Straddle Price", f"₹{greeks['straddle']:.2f}")
        c3.metric("Breakeven ±%", f"±{greeks['straddle_pct']:.2f}%")
        c4.metric("Net Delta", f"{greeks['net_delta']:+.3f}")
        c5.metric("Theta Decay", f"₹{greeks['theta_day']:.2f} ₹/d")
        c6.metric("Straddle Vega", f"{greeks['vega']:.2f} ₹/pt")
    else:
        st.info("ATM Straddle pricing & Greeks will update when valid spot and IV quotes are captured.")

    if saved.empty:
        st.info(f"Collecting intraday snapshots for {symbol} {selected.get('Expiry')}... Live calculations above update continuously with 1-minute Google Cloud captures.")
        return

    saved['day'] = pd.to_datetime(saved['As of']).dt.date
    available_days = sorted(saved['day'].dropna().unique(), reverse=True)
    if not available_days:
        st.info(f"No valid session dates found in intraday snapshots for {symbol} {selected.get('Expiry')}.")
        return

    col_day, col_metric = st.columns([1, 2])
    if len(available_days) > 1:
        with col_day:
            chosen_day = st.selectbox(
                "Session Date",
                available_days,
                format_func=lambda d: d.strftime('%d %b %Y'),
                key=f"straddle_session_day_{symbol}_{selected.get('Expiry')}"
            )
    else:
        chosen_day = available_days[0]
        with col_day:
            st.caption(f"Session: {chosen_day.strftime('%d %b %Y')}")

    session_data = saved[saved['day'] == chosen_day].copy().sort_values('As of').reset_index(drop=True)

    if len(session_data) < 2:
        st.info(f"Captured {len(session_data)} snapshot for {chosen_day:%d %b %Y}. Intraday progression chart activates after multiple 1-minute captures.")
        return

    # Vectorized calculation of Straddle Price and Net Delta across all session snapshots
    s_prices = pd.to_numeric(session_data['Price'], errors='coerce').fillna(spot).to_numpy(dtype=float)
    s_ivs = pd.to_numeric(session_data['IV'], errors='coerce').fillna(iv).to_numpy(dtype=float)
    if 'ATM strike' in session_data:
        raw_k = pd.to_numeric(session_data['ATM strike'], errors='coerce').to_numpy(dtype=float)
    else:
        raw_k = np.full(len(session_data), np.nan)
    fallback_k = np.where(s_prices > 0, np.round(s_prices / step) * step, strike)
    s_strikes = np.where(np.isfinite(raw_k) & (raw_k > 0), raw_k, fallback_k)

    try:
        exp_date = pd.Timestamp(selected.get('Expiry')).date()
        row_dates = pd.to_datetime(session_data['As of']).dt.date
        s_dtes = np.array([max(0.2, float((exp_date - d).days)) for d in row_dates], dtype=float)
    except Exception:
        s_dtes = np.full(len(session_data), dte, dtype=float)

    straddle_pts, deltas = _compute_straddle_series_vectorized(s_prices, s_strikes, s_dtes, s_ivs)
    session_data['Straddle Price'] = straddle_pts
    session_data['Net Delta'] = deltas

    with col_metric:
        view_metric = st.segmented_control(
            "Intraday Metric",
            ["Straddle Price (₹)", "Implied Volatility (%)", "Net Delta", "Underlying Spot"],
            default="Straddle Price (₹)",
            label_visibility="collapsed",
            key=f"straddle_intraday_metric_{symbol}"
        ) or "Straddle Price (₹)"

    fig = go.Figure()
    time_series = pd.to_datetime(session_data['As of'])

    if view_metric == "Straddle Price (₹)":
        fig.add_trace(go.Scatter(
            x=time_series,
            y=session_data['Straddle Price'],
            name="ATM Straddle Price",
            mode="lines+markers",
            connectgaps=False,
            line=dict(color="#10b981", width=2.4),
            marker=dict(color="#34d399", size=5),
            hovertemplate="%{x|%H:%M IST}<br>Straddle: ₹%{y:.2f}<extra></extra>"
        ))
        y_title = "Straddle Price (₹)"
    elif view_metric == "Implied Volatility (%)":
        fig.add_trace(go.Scatter(
            x=time_series,
            y=pd.to_numeric(session_data['IV'], errors='coerce'),
            name="ATM IV",
            mode="lines+markers",
            connectgaps=False,
            line=dict(color="#38bdf8", width=2.4),
            marker=dict(color="#60a5fa", size=5),
            hovertemplate="%{x|%H:%M IST}<br>IV: %{y:.2f}%<extra></extra>"
        ))
        y_title = "Implied Volatility (%)"
    elif view_metric == "Net Delta":
        fig.add_trace(go.Scatter(
            x=time_series,
            y=session_data['Net Delta'],
            name="Straddle Net Delta",
            mode="lines+markers",
            connectgaps=False,
            line=dict(color="#d4af37", width=2.4),
            marker=dict(color="#fbbf24", size=5),
            hovertemplate="%{x|%H:%M IST}<br>Delta: %{y:+.3f}<extra></extra>"
        ))
        y_title = "Net Straddle Delta"
    else:
        fig.add_trace(go.Scatter(
            x=time_series,
            y=pd.to_numeric(session_data['Price'], errors='coerce'),
            name="Spot Price",
            mode="lines+markers",
            connectgaps=False,
            line=dict(color="#a78bfa", width=2.4),
            marker=dict(color="#c084fc", size=5),
            hovertemplate="%{x|%H:%M IST}<br>Spot: %{y:,.2f}<extra></extra>"
        ))
        y_title = "Spot Price"

    fig.update_layout(
        height=350,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(13, 19, 32, 0.55)",
        margin=dict(l=46, r=20, t=34, b=34),
        yaxis_title=y_title,
        yaxis_tickformat=".2f",
        xaxis_tickformat="%H:%M",
        hovermode="x unified"
    )
    st.plotly_chart(fig, key=f"intraday_straddle_chart_{symbol}", width="stretch", config={'displaylogo': False})
    st.caption(f"{len(session_data)} intraday snapshots for {chosen_day.strftime('%d %b %Y')} · 1-minute Google Cloud capture sync")

    with st.expander("Intraday session data table & CSV export"):
        export_df = session_data[['As of', 'Price', 'IV', 'Straddle Price', 'Net Delta', 'Source']].copy()
        export_df['As of'] = export_df['As of'].dt.strftime('%Y-%m-%d %H:%M:%S IST')
        st.dataframe(export_df, hide_index=True, width="stretch")
        st.download_button(
            "Download Intraday CSV",
            export_df.to_csv(index=False).encode(),
            file_name=f"{symbol}_{selected.get('Expiry')}_intraday_{chosen_day}.csv",
            mime="text/csv"
        )

def render_research_tools(data, symbol, selected, result, config):
    from monitor_ui import render_relative_value
    render_relative_value(symbol, selected, config)

def render_trade_tools(data, symbol, selected, rate, dividend, spread):
    from trade_review_ui import render_review
    from trade_review_ui import atm_pair, freshness, make_position, option_book, parity_forward, position_greeks, stress_position
    from history_policy import assumptions
    from dividend_yield import for_symbol
    paths = sorted(data.glob(symbol.lower() + '_option_quotes_*.json'), reverse=True)
    if not paths:
        st.info('Live scenario stress testing requires local option book JSON archives.')
        return
    try:
        path = paths[0]
        payload = json.loads(path.read_text(encoding='utf-8'))
        settings = assumptions(data / 'monitor.sqlite', payload.get('config'))
        if not settings:
            raise ValueError('Original pricing assumptions unavailable for this book.')
        if settings['rate'] is None or (settings['dividend'].get(symbol) if isinstance(settings['dividend'], dict) else settings['dividend']) is None:
            raise ValueError('Model scenarios require rate/dividend inputs; broker IV remains available in the other views.')
        expiry = path.stem.replace(symbol.lower() + '_option_quotes_', '')
        context = option_book(payload, expiry, settings['rate'], for_symbol(settings['dividend'], symbol), min(3.0, settings['max_spread']))
        st.caption('Book captured ' + str(context['stamp']) + ' · ' + freshness(context, datetime.now(IST), payload.get('spot_quote')))
        st.caption('Current IV/Greeks: Upstox · scenario P&L: lab model · bid/ask liquidity checks remain active')
        protected = st.toggle('Add protective wings', value=True, key='stress_wings')
        put = call = None
        if protected:
            book = context.get('book') if 'book' in context else context.get('chain', pd.DataFrame())
            puts = sorted(book.loc[(book.Kind == 'PE') & (book.Strike < context['strike'])]['Strike'].unique())
            calls = sorted(book.loc[(book.Kind == 'CE') & (book.Strike > context['strike'])]['Strike'].unique())
            if not puts or not calls:
                raise ValueError('No valid protective wing quotes in the saved book.')
            put = st.selectbox('Put wing', puts, index=len(puts) - 1)
            call = st.selectbox('Call wing', calls)
        position = make_position(context, put, call)
        days = st.number_input('Elapsed calendar days', min_value=0.0, max_value=365.0, value=1.0, step=0.25)
        costs = st.number_input('Additional costs per position (₹)', min_value=0.0, value=0.0, step=10.0)
        scenarios, summary = stress_position(context, position, days, costs)
        st.markdown('**Position Legs**')
        table(position.drop(columns=['Bid IV', 'Ask IV'], errors='ignore'))
        st.markdown('**Position Greeks & Capital Summary**')
        table(pd.DataFrame([{**summary, **position_greeks(context, position)}]))
        st.markdown('**Scenario P&L Matrix (₹)**')
        grid = scenarios.pivot(index='Spot move %', columns='IV shock · pp', values='Unhedged P&L · ₹').reset_index()
        grid.columns = [str(c) if c == 'Spot move %' else 'IV +' + str(c) + ' pp' for c in grid.columns]
        table(grid)
        st.caption('One lot · sticky-strike IV shocks · bid/ask costs · frozen hedge is hypothetical spot units, not futures lots')
        st.download_button('Download stress scenarios', scenarios.to_csv(index=False).encode(), file_name=symbol + '_stress.csv')
    except (OSError, ValueError, TypeError, KeyError) as error:
        st.info(str(error))
