"""Recovered research views; no data fabrication or trading operations (Vectorized & Cached Black-76 Edition)."""
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
try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')
from trade_review_ui import table
import option_math

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
    df = load(Path(db_path_str), symbol, expiry, config)
    if df is not None and not df.empty:
        return df
    return load(Path(db_path_str), symbol, expiry, "default")


def compute_straddle_greeks(spot, strike, dte, vol_pct, rate=None, forward=None):
    """Compute ATM Straddle price, Net Delta, Gamma, 2-leg Vega, and 2-leg Daily Theta under Fischer Black (1976) off Forward F."""
    try:
        spot = float(spot)
        strike = float(strike)
        dte = float(dte)
        vol_pct = float(vol_pct)
        fwd = float(forward)
        rate = float(rate)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(spot) and spot > 0 and np.isfinite(strike) and strike > 0
            and np.isfinite(fwd) and fwd > 0 and np.isfinite(rate)
            and np.isfinite(dte) and dte >= 0 and np.isfinite(vol_pct) and vol_pct >= 0):
        return None
    T = dte / 365.0
    sigma = vol_pct / 100.0
    try:
        ce = option_math.black76_greeks(fwd, strike, T, rate, sigma, 'CE')
        pe = option_math.black76_greeks(fwd, strike, T, rate, sigma, 'PE')
        if not (np.isfinite(ce['price']) and np.isfinite(pe['price'])):
            return None
        call_p = float(ce['price'])
        put_p = float(pe['price'])
        straddle_p = call_p + put_p
        call_d = float(ce['delta'])
        put_d = float(pe['delta'])
        gamma = float(ce['gamma']) + float(pe['gamma'])
        straddle_vega = float(ce['vega']) + float(pe['vega'])
        straddle_theta = float(ce['theta_daily']) + float(pe['theta_daily'])
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return {
        'model': 'Black-76',
        'forward': fwd,
        'strike': strike,
        'straddle': straddle_p,
        'straddle_pct': (straddle_p / spot) * 100.0,
        'call_delta': call_d,
        'put_delta': put_d,
        'net_delta': call_d + put_d,
        'fwd_net_delta': float(ce['fwd_delta']) + float(pe['fwd_delta']),
        'gamma': gamma,
        'vega': straddle_vega,
        'theta_day': straddle_theta,
    }


def _compute_straddle_series_vectorized(prices: np.ndarray, strikes: np.ndarray, dtes: np.ndarray, ivs: np.ndarray, rate: float | None = None, forwards: np.ndarray | None = None):
    """Vectorized Black-76 ATM Straddle Price and Discounted Net Delta across an entire intraday session array."""
    prices_arr = np.asarray(prices, dtype=float)
    strikes_arr = np.asarray(strikes, dtype=float)
    dtes_arr = np.asarray(dtes, dtype=float)
    ivs_arr = np.asarray(ivs, dtype=float)
    fwds_arr = np.asarray(forwards, dtype=float) if forwards is not None else np.full_like(prices_arr, np.nan)
    rate = _clean_float(rate, float('nan'))

    valid = (
        np.isfinite(prices_arr) & (prices_arr > 0)
        & np.isfinite(fwds_arr) & (fwds_arr > 0)
        & np.isfinite(strikes_arr) & (strikes_arr > 0)
        & np.isfinite(ivs_arr) & (ivs_arr >= 0)
        & np.isfinite(dtes_arr) & (dtes_arr >= 0) & np.isfinite(rate)
    )
    straddle_out = np.full(len(prices_arr), np.nan, dtype=float)
    delta_out = np.full(len(prices_arr), np.nan, dtype=float)
    if not np.any(valid):
        return straddle_out, delta_out

    f = fwds_arr[valid]
    k = strikes_arr[valid]
    t = dtes_arr[valid] / 365.0
    sigma = ivs_arr[valid] / 100.0
    ce_vec = option_math.black76_greeks_vec(f, k, t, vol=sigma, rate=rate, kind='CE')
    pe_vec = option_math.black76_greeks_vec(f, k, t, vol=sigma, rate=rate, kind='PE')
    straddle_out[valid] = ce_vec['price'] + pe_vec['price']
    delta_out[valid] = ce_vec['delta'] + pe_vec['delta']
    return straddle_out, delta_out


def captured_session(frame, strike=None):
    """Keep captured values only; a fixed strike never splices different contracts."""
    from snapshot_history import broker_sourced, historical_fields
    out = pd.DataFrame([historical_fields(row) for row in frame.to_dict('records')], index=frame.index)
    for column in ('Price', 'IV', 'ATM strike', 'Straddle midpoint', 'ATM call LTP', 'ATM put LTP'):
        values = pd.to_numeric(out[column], errors='coerce') if column in out else pd.Series(np.nan, index=out.index)
        out[column] = values.where(np.isfinite(values) & values.gt(0))
    trusted = pd.Series([broker_sourced(row) for row in frame.to_dict('records')], index=out.index)
    both_legs = out['ATM call LTP'] + out['ATM put LTP']
    out['Captured straddle'] = out['Straddle midpoint'].combine_first(both_legs)
    valid_strike = out['ATM strike'].notna()
    if strike is not None:
        valid_strike &= out['ATM strike'].eq(float(strike))
    out['Captured straddle'] = out['Captured straddle'].where(trusted & valid_strike)
    return out


def render_archived_intraday(data, symbol, selected, config):
    # Captured broker prices and IV remain independent of any pricing-model inputs.
    current = captured_session(pd.DataFrame([dict(selected)])).iloc[0]
    cols = st.columns(4)
    for col, label, field, pattern in (
        (cols[0], 'ATM strike', 'ATM strike', '{:,.0f}'),
        (cols[1], 'Captured straddle', 'Captured straddle', '₹{:,.2f}'),
        (cols[2], 'Captured ATM IV', 'IV', '{:.2f}%'),
        (cols[3], 'Underlying spot', 'Price', '{:,.2f}'),
    ):
        value = current.get(field)
        col.metric(label, pattern.format(value) if pd.notna(value) else '—')

    db_file = data / 'monitor.sqlite'
    saved = _cached_intraday_snapshots(
        str(db_file), symbol, str(selected.get('Expiry') or ''), str(config or ''), _sqlite_rev(db_file)
    ).copy()
    if saved.empty:
        st.info(f"No archived captures for {symbol} {selected.get('Expiry')}.")
        return

    saved['As of'] = pd.to_datetime(saved['As of'], format='ISO8601', utc=True).dt.tz_convert(IST)
    saved['day'] = saved['As of'].dt.date
    available_days = sorted(saved['day'].dropna().unique(), reverse=True)
    if not available_days:
        st.info('No valid session dates in the archived captures.')
        return
    col_day, col_metric = st.columns([1, 2])
    with col_day:
        chosen_day = st.selectbox(
            'Session date', available_days, format_func=lambda d: d.strftime('%d %b %Y'),
            key=f"straddle_session_day_{symbol}_{selected.get('Expiry')}"
        )
    session_data = captured_session(saved[saved['day'] == chosen_day].copy().sort_values('As of'))
    with col_metric:
        view_metric = st.segmented_control(
            'Intraday metric', ['Straddle Price (₹)', 'Implied Volatility (%)', 'Underlying Spot'],
            default='Straddle Price (₹)', label_visibility='collapsed',
            key=f'straddle_intraday_metric_{symbol}'
        ) or 'Straddle Price (₹)'

    if view_metric == 'Straddle Price (₹)':
        strikes = sorted(session_data.loc[session_data['Captured straddle'].notna(), 'ATM strike'].unique())
        if not strikes:
            st.info('No captured two-leg straddle prices for this session.')
            return
        strike = st.selectbox('Fixed straddle strike', strikes, format_func=lambda k: f'{k:,.0f}',
                              key=f"straddle_fixed_strike_{symbol}_{selected.get('Expiry')}")
        session_data = captured_session(session_data, strike=strike)
        field, name, color = 'Captured straddle', f'Captured straddle · {strike:,.0f}', '#10b981'
        hover = '%{x|%H:%M IST}<br>Straddle: ₹%{y:.2f}<extra></extra>'
    elif view_metric == 'Implied Volatility (%)':
        field, name, color = 'IV', 'Captured ATM IV', '#38bdf8'
        hover = '%{x|%H:%M IST}<br>IV: %{y:.2f}%<extra></extra>'
    else:
        field, name, color = 'Price', 'Captured spot', '#a78bfa'
        hover = '%{x|%H:%M IST}<br>Spot: %{y:,.2f}<extra></extra>'
    fig = go.Figure(go.Scatter(
        x=session_data['As of'], y=session_data[field], name=name, mode='lines+markers',
        connectgaps=False, line=dict(color=color, width=2.4), marker=dict(color=color, size=5),
        hovertemplate=hover,
    ))
    fig.update_layout(
        height=350, template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(13,19,32,0.55)', margin=dict(l=46,r=20,t=34,b=34),
        yaxis_title=view_metric, yaxis_tickformat='.2f', xaxis_tickformat='%H:%M', hovermode='x unified',
    )
    st.plotly_chart(fig, key=f'intraday_straddle_chart_{symbol}', width='stretch', config={'displaylogo':False})
    if field == 'Captured straddle':
        st.caption('Fixed strike · missing captures remain blank')
    with st.expander('Intraday session data & CSV export'):
        export_df = session_data.reindex(columns=['As of','ATM strike','Price','IV','Captured straddle','Source']).copy()
        export_df['As of'] = export_df['As of'].dt.strftime('%Y-%m-%d %H:%M:%S IST')
        st.dataframe(export_df, hide_index=True, width='stretch')
        st.download_button('Download intraday CSV', export_df.to_csv(index=False).encode(),
                           file_name=f"{symbol}_{selected.get('Expiry')}_intraday_{chosen_day}.csv", mime='text/csv')


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
        st.caption('Current IV/Greeks: Black-76 / Upstox · scenario P&L: lab model · bid/ask liquidity checks remain active')
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
