"""Recovered research views; no data fabrication or trading operations."""
import json
import math
import sqlite3
from datetime import datetime
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from market_data import IST
from trade_review_ui import table


def compute_straddle_greeks(spot, strike, dte, vol_pct, rate=0.065):
    if spot <= 0 or strike <= 0 or vol_pct <= 0:
        return None
    T = max(0.5, float(dte)) / 245.0
    sigma = max(0.01, float(vol_pct) / 100.0)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    norm_cdf = lambda x: (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0
    norm_pdf = lambda x: math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    call_p = spot * norm_cdf(d1) - strike * math.exp(-rate * T) * norm_cdf(d2)
    put_p = strike * math.exp(-rate * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)
    straddle_p = call_p + put_p
    call_d = norm_cdf(d1)
    put_d = call_d - 1.0
    gamma = norm_pdf(d1) / (spot * sigma * math.sqrt(T))
    vega = spot * norm_pdf(d1) * math.sqrt(T) / 100.0
    theta_day = -(spot * norm_pdf(d1) * sigma / (2.0 * math.sqrt(T))) / 245.0
    return {
        'strike': strike,
        'straddle': straddle_p,
        'straddle_pct': (straddle_p / spot) * 100.0,
        'call_delta': call_d,
        'put_delta': put_d,
        'net_delta': call_d + put_d,
        'gamma': gamma,
        'vega': vega,
        'theta_day': theta_day * 2.0
    }


def render_archived_intraday(data, symbol, selected, config):
    spot = float(selected.get('Price') or 0.0)
    iv = float(selected.get('IV') or 0.0)
    step = 50 if symbol == 'NIFTY' else 100
    strike = round(spot / step) * step if spot > 0 else 0

    dte = 7
    try:
        if selected.get('Expiry') and selected.get('As of'):
            exp_d = pd.Timestamp(selected['Expiry']).date()
            asof_d = pd.Timestamp(selected['As of']).date()
            dte = max(0, (exp_d - asof_d).days)
    except Exception:
        pass

    greeks = compute_straddle_greeks(spot, strike, dte, iv)

    # 1. Top KPI Metric Cards for Live ATM Straddle & Greeks
    if greeks:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric('ATM Strike', f"{greeks['strike']:,}")
        c2.metric('Straddle Price', f"₹{greeks['straddle']:.2f}")
        c3.metric('Breakeven ±%', f"±{greeks['straddle_pct']:.2f}%")
        c4.metric('Net Delta', f"{greeks['net_delta']:+.3f}")
        c5.metric('Theta Decay', f"{greeks['theta_day']:.2f} ₹/d")
        c6.metric('Straddle Vega', f"{greeks['vega']:.2f} ₹/pt")

    # 2. Intraday History from monitor.sqlite
    from snapshot_history import load
    saved = load(data / 'monitor.sqlite', symbol, selected.get('Expiry'), config)

    if saved.empty:
        st.info(f"Collecting intraday snapshots for {symbol} {selected.get('Expiry')}... Live calculations above update continuously with 1-minute Google Cloud captures.")
        return

    # Filter to most recent captured session
    saved['day'] = pd.to_datetime(saved['As of']).dt.date
    latest_day = saved['day'].max()
    session_data = saved[saved['day'] == latest_day].copy().sort_values('As of').reset_index(drop=True)

    if len(session_data) < 2:
        st.info(f"Captured {len(session_data)} snapshot for {latest_day:%d %b %Y}. Intraday progression chart activates after multiple 1-minute captures.")
        return

    # Calculate Straddle Price and Net Delta for each historical snapshot
    straddle_pts = []
    deltas = []
    for _, row in session_data.iterrows():
        s_price = float(row.get('Price') or spot)
        s_iv = float(row.get('IV') or iv)
        s_strike = round(s_price / step) * step if s_price > 0 else strike
        res = compute_straddle_greeks(s_price, s_strike, dte, s_iv)
        if res:
            straddle_pts.append(res['straddle'])
            deltas.append(res['net_delta'])
        else:
            straddle_pts.append(np.nan)
            deltas.append(np.nan)

    session_data['Straddle Price'] = straddle_pts
    session_data['Net Delta'] = deltas

    chart_col, _ = st.columns([2, 5])
    with chart_col:
        view_metric = st.segmented_control(
            'Intraday Metric',
            ['Straddle Price (₹)', 'Implied Volatility (%)', 'Net Delta', 'Underlying Spot'],
            default='Straddle Price (₹)',
            label_visibility='collapsed',
            key=f'straddle_intraday_metric_{symbol}'
        ) or 'Straddle Price (₹)'

    fig = go.Figure()
    time_series = pd.to_datetime(session_data['As of'])

    if view_metric == 'Straddle Price (₹)':
        fig.add_trace(go.Scatter(
            x=time_series, y=session_data['Straddle Price'],
            name='ATM Straddle Price', mode='lines+markers',
            line={'color': '#10b981', 'width': 2.4},
            marker={'color': '#34d399', 'size': 5},
            hovertemplate='%{x|%H:%M IST}<br>Straddle: ₹%{y:.2f}<extra></extra>'
        ))
        y_title = 'Straddle Price (₹)'
    elif view_metric == 'Implied Volatility (%)':
        fig.add_trace(go.Scatter(
            x=time_series, y=pd.to_numeric(session_data['IV'], errors='coerce'),
            name='ATM IV', mode='lines+markers',
            line={'color': '#38bdf8', 'width': 2.4},
            marker={'color': '#60a5fa', 'size': 5},
            hovertemplate='%{x|%H:%M IST}<br>IV: %{y:.2f}%<extra></extra>'
        ))
        y_title = 'Implied Volatility (%)'
    elif view_metric == 'Net Delta':
        fig.add_trace(go.Scatter(
            x=time_series, y=session_data['Net Delta'],
            name='Straddle Net Delta', mode='lines+markers',
            line={'color': '#f59e0b', 'width': 2.4},
            marker={'color': '#fbbf24', 'size': 5},
            hovertemplate='%{x|%H:%M IST}<br>Delta: %{y:+.3f}<extra></extra>'
        ))
        y_title = 'Net Straddle Delta'
    else:
        fig.add_trace(go.Scatter(
            x=time_series, y=pd.to_numeric(session_data['Price'], errors='coerce'),
            name='Spot Price', mode='lines+markers',
            line={'color': '#a78bfa', 'width': 2.4},
            marker={'color': '#c084fc', 'size': 5},
            hovertemplate='%{x|%H:%M IST}<br>Spot: %{y:,.2f}<extra></extra>'
        ))
        y_title = 'Spot Price'

    fig.update_layout(
        height=350,
        template='plotly_dark',
        paper_bgcolor='#0c1321',
        plot_bgcolor='#0c1321',
        margin={'l': 10, 'r': 15, 't': 20, 'b': 15},
        yaxis_title=y_title,
        yaxis_tickformat='.2f',
        xaxis_tickformat='%H:%M',
        hovermode='x unified'
    )
    st.plotly_chart(fig, key=f'intraday_straddle_chart_{symbol}', width='stretch', config={'displaylogo': False})


def render_research_tools(data, symbol, selected, result, config):
    from monitor_ui import render_relative_value
    render_relative_value(symbol, selected, config)


def render_trade_tools(data, symbol, selected, rate, dividend, spread):
    from trade_review_ui import render_review
    from trade_review import option_book, atm_pair, parity_forward, make_position, position_greeks, stress_position, freshness
    from history_policy import assumptions
    from dividend_yield import for_symbol
    paths = sorted(data.glob(symbol.lower() + '_option_quotes_*.json'), reverse=True)
    if not paths:
        st.info('No saved option books for trade analysis.')
        return
    preferred = next((i for i, p in enumerate(paths) if p.stem.endswith(str(selected.get('Expiry')))), 0)
    path = st.selectbox('Saved option book', paths, index=preferred, format_func=lambda p: p.stem.replace(symbol.lower() + '_option_quotes_', ''), key='restored_trade_book')
    try:
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
            book = context['book']
            puts = sorted(book.loc[(book.Kind == 'PE') & (book.Strike < context['strike']), 'Strike'].unique())
            calls = sorted(book.loc[(book.Kind == 'CE') & (book.Strike > context['strike']), 'Strike'].unique())
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
