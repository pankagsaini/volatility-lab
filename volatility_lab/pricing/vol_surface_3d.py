"""Strike/expiry surfaces built from captured Upstox option Greeks."""

from datetime import datetime, date
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')

import option_math

SURFACE_VERSION = 'vol-surface-3d-v3-broker-iv'


def load_expiry_chains(symbol: str, data_dir: Path, today: Optional[date] = None, rate=None):
    """Read archived chains with explicit broker provenance and real capture timestamps."""
    from volatility_lab.pricing.pricing_policy import number, timestamp
    today = today or datetime.now(IST).date()
    result = []
    for path in sorted(data_dir.glob(f'{symbol.lower()}_option_quotes_*.json')):
        try:
            expiry = date.fromisoformat(path.stem.rsplit('_', 1)[-1])
            obj = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(obj,dict) or obj.get('provider') != 'Upstox' or expiry < today:
                continue
            observed = timestamp(obj.get('valuation'))
            spot = number(obj.get('spot'))
            if observed is None or spot is None or spot <= 0 or not isinstance(obj.get('broker_option_chain'), list):
                continue
            close = datetime.combine(expiry, datetime.min.time(), tzinfo=IST).replace(hour=15,minute=30)
            dte = (close - observed).total_seconds() / 86400
            if dte < 0 or observed.astimezone(IST).date() > today:
                continue
            chain = []
            for row in obj['broker_option_chain']:
                if not isinstance(row, dict) or row.get('expiry') != expiry.isoformat():
                    continue
                strike = number(row.get('strike_price'))
                if strike is None or strike <= 0:
                    continue
                chain.append({**row, 'strike_price':strike})
            if chain:
                result.append({'expiry':expiry.isoformat(), 'expiry_date':expiry, 'dte':dte, 'years':dte/365,
                               'spot':spot, 'chain':chain, 'as_of':observed.isoformat()})
        except (OSError, ValueError, TypeError, OverflowError):
            continue
    return sorted(result,key=lambda item:item['dte'])


def captured_iv(row, side):
    from volatility_lab.pricing.pricing_policy import number
    leg = row.get(side)
    greeks = leg.get('option_greeks') if isinstance(leg,dict) else None
    value = number(greeks.get('iv')) if isinstance(greeks,dict) else None
    return value if value is not None and value > 0 else float('nan')


def extract_surface_mesh(
    expiries_data: List[Dict[str, Any]],
    iv_mode: str = 'otm',
    space_mode: str = 'moneyness',
    moneyness_range_pct: float = 8.0,
    n_strikes: int = 25,
    include_zero_dte: bool = True,
    iv_cap: float = 120.0,
    rate: float | None = None,
    use_black76: bool = False,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
    """Build a uniform 2D mesh grid (Strikes/Moneyness x DTE -> IV%) from captured broker IVs."""
    if use_black76 or iv_mode == 'black76':
        raise ValueError('Archived surfaces require broker-supplied IV')
    if not expiries_data:
        return np.array([]), np.array([]), np.empty((0, 0)), [], {}

    filtered = [e for e in expiries_data if include_zero_dte or e['dte'] >= 1.0]
    if not filtered:
        return np.array([]), np.array([]), np.empty((0, 0)), [], {}

    ref_spot = filtered[0]['spot']
    ref_forward = filtered[0].get('forward', ref_spot)

    if space_mode == 'moneyness':
        x_grid = np.linspace(100.0 - moneyness_range_pct, 100.0 + moneyness_range_pct, n_strikes)
    else:
        raw_strikes = sorted({float(item['strike_price']) for item in filtered[0].get('chain', []) if item.get('strike_price')})
        strike_step = float(np.median(np.diff(raw_strikes))) if len(raw_strikes) >= 2 else (50.0 if ref_spot < 35000 else 100.0)
        atm_strike = round(ref_spot / strike_step) * strike_step
        half_span = ref_spot * (moneyness_range_pct / 100.0)
        x_grid = np.linspace(atm_strike - half_span, atm_strike + half_span, n_strikes)

    y_grid = []
    expiry_labels = []
    z_rows = []
    raw_point_counts = []
    included_expiries = []

    for entry in filtered:
        dte = entry['dte']
        years = entry.get('years', max(0.2, dte) / 365.0)
        spot = entry['spot']
        chain = entry['chain']
        extracted_strikes = []
        extracted_ivs = []

        for item in chain:
            k = item.get('strike_price')
            if k is None or not np.isfinite(k):
                continue
            k_f = float(k)

            m_pct = (k_f / spot) * 100.0
            if abs(m_pct - 100.0) > (moneyness_range_pct * 1.35):
                continue

            c_iv = captured_iv(item,'call_options')
            p_iv = captured_iv(item,'put_options')
            if iv_mode == 'otm':
                chosen_iv = p_iv if k_f < spot else c_iv
            elif iv_mode == 'call':
                chosen_iv = c_iv
            elif iv_mode == 'put':
                chosen_iv = p_iv
            elif iv_mode == 'mid':
                valid = [v for v in (c_iv, p_iv) if v > 0]
                chosen_iv = sum(valid) / len(valid) if valid else 0.0
            else:
                chosen_iv = p_iv if k_f < spot else c_iv

            if 0 < chosen_iv <= iv_cap and np.isfinite(chosen_iv):
                capped_iv = min(float(chosen_iv), iv_cap)
                x_val = m_pct if space_mode == 'moneyness' else k_f
                extracted_strikes.append(x_val)
                extracted_ivs.append(capped_iv)

        if len(extracted_strikes) < 3:
            continue

        order = np.argsort(extracted_strikes)
        s_arr = np.array(extracted_strikes)[order]
        iv_arr = np.array(extracted_ivs)[order]

        s_unique, idx_unique = np.unique(s_arr, return_index=True)
        iv_unique = iv_arr[idx_unique]

        if len(s_unique) < 3:
            continue
        z_interp = np.interp(x_grid, s_unique, iv_unique, left=np.nan, right=np.nan)
        included_expiries.append(entry['expiry'])
        z_rows.append(z_interp)
        y_grid.append(dte)
        expiry_labels.append(f"{entry['expiry']} ({dte:.1f}d)")
        raw_point_counts.append(len(s_unique))

    if not z_rows:
        return x_grid, np.array([]), np.empty((0, len(x_grid))), [], {}

    y_arr = np.array(y_grid)
    z_matrix = np.array(z_rows)

    target_atm = 100.0 if space_mode == 'moneyness' else ref_spot
    atm_idx = int(np.argmin(np.abs(x_grid - target_atm)))
    front_atm_iv = float(z_matrix[0, atm_idx])
    back_atm_iv = float(z_matrix[-1, atm_idx])
    term_slope = back_atm_iv - front_atm_iv

    target_left = 96.0 if space_mode == 'moneyness' else ref_spot * 0.96
    target_right = 104.0 if space_mode == 'moneyness' else ref_spot * 1.04
    iv_left, iv_right = np.interp([target_left, target_right], x_grid, z_matrix[0])
    front_skew = float(iv_left - iv_right)

    diagnostics = {
        'pricing_model': 'Captured broker IV',
        'expiries': included_expiries,
        'num_expiries': len(y_arr),
        'min_dte': float(y_arr.min()),
        'max_dte': float(y_arr.max()),
        'ref_spot': float(ref_spot),
        'front_atm_iv': front_atm_iv,
        'back_atm_iv': back_atm_iv,
        'term_slope': term_slope,
        'front_skew': front_skew,
        'total_raw_points': sum(raw_point_counts)
    }

    return x_grid, y_arr, z_matrix, expiry_labels, diagnostics


def build_surface_figure(
    x_grid: np.ndarray,
    y_grid: np.ndarray,
    z_matrix: np.ndarray,
    expiry_labels: List[str],
    space_mode: str = 'moneyness',
    colorscale: str = 'Tealgrn',
    camera_preset: str = 'default'
) -> go.Figure:
    """Create a Plotly 3D Surface figure with terminal styling and contour projections."""
    x_title = 'Moneyness % (Strike / Spot)' if space_mode == 'moneyness' else 'Strike Price (₹)'

    hover_template = (
        '<b>%{text}</b><br>' +
        f'{x_title}: %{{x:.1f}}<br>' +
        'DTE: %{y:.1f} days<br>' +
        'Broker IV: <b>%{z:.2f}%</b>' +
        '<extra></extra>'
    )

    text_matrix = [[expiry_labels[i] for _ in range(len(x_grid))] for i in range(len(y_grid))]

    surface = go.Surface(
        x=x_grid,
        y=y_grid,
        z=z_matrix,
        text=text_matrix,
        hovertemplate=hover_template,
        colorscale=colorscale,
        showscale=True,
        colorbar=dict(
            title=dict(text='IV %', font=dict(color='#94a3b8', size=11)),
            tickfont=dict(color='#cbd5e1', size=10),
            thickness=14,
            len=0.75,
            x=1.02
        ),
        contours=dict(
            z=dict(
                show=True,
                usecolormap=True,
                highlightcolor='#10b981',
                project_z=True
            ),
            x=dict(
                show=True,
                usecolormap=False,
                color='rgba(6, 182, 212, 0.4)',
                highlightcolor='#06b6d4',
                project_x=True
            ),
            y=dict(
                show=True,
                usecolormap=False,
                color='rgba(239, 68, 68, 0.4)',
                highlightcolor='#ef4444',
                project_y=True
            )
        ),
        opacity=0.92
    )

    fig = go.Figure(data=[surface])

    camera_views = {
        'default': dict(eye=dict(x=1.65, y=-1.65, z=1.25)),
        'smile': dict(eye=dict(x=0.0, y=-2.4, z=0.15)),
        'term': dict(eye=dict(x=2.4, y=0.0, z=0.15)),
        'topdown': dict(eye=dict(x=0.0, y=0.0, z=2.6))
    }
    chosen_camera = camera_views.get(camera_preset, camera_views['default'])

    fig.update_layout(
        template='plotly_dark',
        paper_bgcolor='#090e17',
        plot_bgcolor='#090e17',
        margin=dict(l=10, r=20, t=25, b=20),
        height=580,
        scene=dict(
            xaxis=dict(
                title=dict(text=x_title, font=dict(color='#94a3b8', size=11)),
                tickfont=dict(color='#cbd5e1', size=10),
                backgroundcolor='#0d1424',
                gridcolor='#1e293b',
                showbackground=True,
                zerolinecolor='#334155'
            ),
            yaxis=dict(
                title=dict(text='Days to Expiry (DTE)', font=dict(color='#94a3b8', size=11)),
                tickfont=dict(color='#cbd5e1', size=10),
                backgroundcolor='#0d1424',
                gridcolor='#1e293b',
                showbackground=True,
                zerolinecolor='#334155'
            ),
            zaxis=dict(
                title=dict(text='Broker IV (%)', font=dict(color='#94a3b8', size=11)),
                tickfont=dict(color='#cbd5e1', size=10),
                backgroundcolor='#0d1424',
                gridcolor='#1e293b',
                showbackground=True,
                zerolinecolor='#334155'
            ),
            camera=chosen_camera,
            aspectratio=dict(x=1.1, y=1.2, z=0.7)
        )
    )

    return fig


def render_vol_surface_3d(symbol: str, selected_row: Dict[str, Any], result: Dict[str, Any], data_dir: Path):
    """Interactive Streamlit component to render the 3D Volatility Surface."""
    st.markdown("""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <div>
                <span style="font-size:1.15rem; font-weight:600; color:#f8fafc; letter-spacing:-0.02em;">
                    3D Volatility Surface
                </span>
                <span style="background:#162238; color:#06b6d4; font-size:0.75rem; font-weight:600; padding:3px 8px; border-radius:6px; margin-left:8px; border:1px solid #1e3a5f;">
                    Captured broker IV · surface interpolated between quoted strikes
                </span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    expiries_data = load_expiry_chains(symbol, data_dir)
    if not expiries_data:
        st.info(f"No multi-expiry option chains found on disk for {symbol}. Chains sync automatically during market hours.")
        return

    ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1.2, 1.2, 1.2, 1.4])

    with ctrl_col1:
        iv_mode = st.selectbox(
            "IV Selection",
            options=['otm', 'mid', 'call', 'put'],
            format_func=lambda x: {
                'otm': 'OTM broker IV',
                'mid': 'Mid-Market (Avg Call/Put)',
                'call': 'Calls Only',
                'put': 'Puts Only'
            }.get(x, x),
            key=f"3d_iv_mode_{symbol}"
        )

    with ctrl_col2:
        space_mode = st.selectbox(
            "Strike Axis",
            options=['moneyness', 'strike'],
            format_func=lambda x: "Moneyness % (Strike/Spot)" if x == 'moneyness' else "Absolute Strike (₹)",
            key=f"3d_space_mode_{symbol}"
        )

    with ctrl_col3:
        colorscale = st.selectbox(
            "Color Theme",
            options=['Tealgrn', 'Viridis', 'Plasma', 'Turbo', 'Spectral'],
            key=f"3d_colorscale_{symbol}"
        )

    with ctrl_col4:
        camera_preset = st.selectbox(
            "Camera Angle",
            options=['default', 'smile', 'term', 'topdown'],
            format_func=lambda x: {
                'default': '3D Isometric',
                'smile': 'Smile Profile (Strike vs IV)',
                'term': 'Term Structure (DTE vs IV)',
                'topdown': 'Top-Down Heatmap'
            }.get(x, x),
            key=f"3d_camera_{symbol}"
        )

    with st.expander("Surface Mesh Settings & Range Filters", expanded=False):
        exp_col1, exp_col2, exp_col3 = st.columns(3)

        with exp_col1:
            moneyness_range_pct = st.slider(
                "Moneyness Span (±%)",
                min_value=3.0,
                max_value=15.0,
                value=8.0,
                step=1.0,
                key=f"3d_m_span_{symbol}"
            )

        with exp_col2:
            include_zero_dte = st.checkbox(
                "Include 0-DTE Expiry",
                value=True,
                help="Include same-day expiring option chain if available",
                key=f"3d_0dte_{symbol}"
            )

        with exp_col3:
            iv_cap = st.slider(
                "Max IV Cap (%)",
                min_value=40.0,
                max_value=200.0,
                value=120.0,
                step=10.0,
                help="Omit observations above this display limit",
                key=f"3d_iv_cap_{symbol}"
            )

    x_grid, y_grid, z_matrix, expiry_labels, diag = extract_surface_mesh(
        expiries_data=expiries_data,
        iv_mode=iv_mode,
        space_mode=space_mode,
        moneyness_range_pct=moneyness_range_pct,
        n_strikes=25,
        include_zero_dte=include_zero_dte,
        iv_cap=iv_cap
    )

    if z_matrix.size == 0:
        st.warning("Insufficient valid option chain data across expiries to construct 3D surface.")
        return

    fig = build_surface_figure(
        x_grid=x_grid,
        y_grid=y_grid,
        z_matrix=z_matrix,
        expiry_labels=expiry_labels,
        space_mode=space_mode,
        colorscale=colorscale,
        camera_preset=camera_preset
    )

    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': True})

    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Expiries Mesh", f"{diag['num_expiries']} Expiries", f"{diag['min_dte']:.1f}d to {diag['max_dte']:.1f}d DTE")
    with m_col2:
        st.metric("Front ATM IV", f"{diag['front_atm_iv']:.2f}%")
    with m_col3:
        st.metric("Term Structure Slope", f"{diag['term_slope']:+.2f}%", help="Back ATM IV minus Front ATM IV")
    with m_col4:
        st.metric("Front Skew (96%-104%)", f"{diag['front_skew']:+.2f}%", help="Front expiry OTM Put IV minus OTM Call IV")


build_surface = build_surface_figure
