"""Multi-expiry 3D Implied Volatility Surface (Strike x DTE x IV).

Constructs an institutional 3D volatility surface mesh across all active
expiries and strikes surrounding ATM using broker-supplied IVs.
"""

from datetime import datetime, date
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from market_data import IST

SURFACE_VERSION = 'vol-surface-3d-v1'


def load_expiry_chains(symbol: str, data_dir: Path, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Scan and load option chains across all listed expiries for a symbol."""
    if today is None:
        today = datetime.now(IST).date()

    files = sorted(data_dir.glob(f'{symbol.lower()}_option_quotes_*.json'))
    expiries_data = []

    for path in files:
        exp_str = path.stem.replace(f'{symbol.lower()}_option_quotes_', '')
        try:
            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        # Keep expiries on or after today
        if exp_date < today:
            continue

        try:
            with path.open('r', encoding='utf-8') as f:
                content = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        spot = content.get('spot')
        chain = content.get('broker_option_chain', [])
        if not spot or not chain:
            continue

        # Calculate DTE from saved quote valuation timestamp when available
        val_str = content.get('valuation', '')
        val_date = today
        if val_str:
            try:
                val_ts = pd.Timestamp(val_str)
                if val_ts.tzinfo is not None:
                    val_ts = val_ts.tz_convert(IST)
                val_date = val_ts.date()
            except Exception:
                val_date = today
        dte = max(0.2, float((exp_date - val_date).days))

        expiries_data.append({
            'expiry': exp_str,
            'expiry_date': exp_date,
            'dte': dte,
            'spot': float(spot),
            'chain': chain,
            'as_of': content.get('valuation', '')
        })

    # Sort expiries in ascending DTE order
    expiries_data.sort(key=lambda item: item['dte'])
    return expiries_data


def extract_surface_mesh(
    expiries_data: List[Dict[str, Any]],
    iv_mode: str = 'otm',
    space_mode: str = 'moneyness',
    moneyness_range_pct: float = 8.0,
    n_strikes: int = 25,
    include_zero_dte: bool = True,
    iv_cap: float = 120.0
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
    """Build a uniform 2D mesh grid (Strikes/Moneyness x DTE -> IV%).

    Returns:
        x_grid: 1D array of strike or moneyness values (shape: [N])
        y_grid: 1D array of DTEs (shape: [M])
        z_grid: 2D array of IVs (shape: [M, N])
        expiry_labels: List of formatted expiry strings
        diagnostics: Summary metrics of the surface
    """
    if not expiries_data:
        return np.array([]), np.array([]), np.empty((0, 0)), [], {}

    # Filter 0-DTE if requested
    filtered = [e for e in expiries_data if include_zero_dte or e['dte'] >= 1.0]
    if not filtered:
        filtered = expiries_data

    # Reference spot from the nearest expiry
    ref_spot = filtered[0]['spot']

    # Define X-axis grid
    if space_mode == 'moneyness':
        # E.g. 92% to 108%
        x_grid = np.linspace(100.0 - moneyness_range_pct, 100.0 + moneyness_range_pct, n_strikes)
    else:
        # Absolute strike space centered around ATM
        raw_strikes = sorted({float(item['strike_price']) for item in filtered[0].get('chain', []) if item.get('strike_price')})
        strike_step = float(np.median(np.diff(raw_strikes))) if len(raw_strikes) >= 2 else (50.0 if ref_spot < 35000 else 100.0)
        atm_strike = round(ref_spot / strike_step) * strike_step
        half_span = ref_spot * (moneyness_range_pct / 100.0)
        x_grid = np.linspace(atm_strike - half_span, atm_strike + half_span, n_strikes)

    y_grid = []
    expiry_labels = []
    z_rows = []
    raw_point_counts = []

    for entry in filtered:
        dte = entry['dte']
        spot = entry['spot']
        chain = entry['chain']

        extracted_strikes = []
        extracted_ivs = []

        for item in chain:
            k = item.get('strike_price')
            if k is None or not np.isfinite(k):
                continue

            # Check moneyness boundary
            m_pct = (k / spot) * 100.0
            if abs(m_pct - 100.0) > (moneyness_range_pct * 1.35):
                continue

            c_iv = item.get('call_options', {}).get('option_greeks', {}).get('iv', 0.0) or 0.0
            p_iv = item.get('put_options', {}).get('option_greeks', {}).get('iv', 0.0) or 0.0

            # Select IV based on mode
            if iv_mode == 'otm':
                # Institutional standard: Puts below ATM, Calls above ATM
                chosen_iv = p_iv if k < spot else c_iv
                # If chosen IV is missing, fallback to available side
                if chosen_iv <= 0:
                    chosen_iv = c_iv if k < spot else p_iv
            elif iv_mode == 'call':
                chosen_iv = c_iv
            elif iv_mode == 'put':
                chosen_iv = p_iv
            elif iv_mode == 'mid':
                valid = [v for v in (c_iv, p_iv) if v > 0]
                chosen_iv = sum(valid) / len(valid) if valid else 0.0
            else:
                chosen_iv = p_iv if k < spot else c_iv

            if chosen_iv > 0 and np.isfinite(chosen_iv):
                # Apply sensible cap to prevent deep wing solver spikes near 0-DTE
                capped_iv = min(float(chosen_iv), iv_cap)
                x_val = m_pct if space_mode == 'moneyness' else float(k)
                extracted_strikes.append(x_val)
                extracted_ivs.append(capped_iv)

        if len(extracted_strikes) < 3:
            continue

        # Sort strikes monotonically
        order = np.argsort(extracted_strikes)
        s_arr = np.array(extracted_strikes)[order]
        iv_arr = np.array(extracted_ivs)[order]

        # Deduplicate identical strike entries if any
        s_unique, idx_unique = np.unique(s_arr, return_index=True)
        iv_unique = iv_arr[idx_unique]

        # Interpolate across the uniform grid
        # np.interp uses constant extrapolation outside the range
        z_interp = np.interp(x_grid, s_unique, iv_unique)
        z_rows.append(z_interp)
        y_grid.append(dte)
        expiry_labels.append(f"{entry['expiry']} ({dte:.1f}d)")
        raw_point_counts.append(len(s_unique))

    if not z_rows:
        return x_grid, np.array([]), np.empty((0, len(x_grid))), [], {}

    y_arr = np.array(y_grid)
    z_matrix = np.array(z_rows)

    # Diagnostic summary
    target_atm = 100.0 if space_mode == 'moneyness' else ref_spot
    atm_idx = int(np.argmin(np.abs(x_grid - target_atm)))
    front_atm_iv = float(z_matrix[0, atm_idx])
    back_atm_iv = float(z_matrix[-1, atm_idx])
    term_slope = back_atm_iv - front_atm_iv

    # Skew steepness: explicitly interpolate at 96% and 104% moneyness (or equivalent strikes)
    target_left = 96.0 if space_mode == 'moneyness' else ref_spot * 0.96
    target_right = 104.0 if space_mode == 'moneyness' else ref_spot * 1.04
    iv_left, iv_right = np.interp([target_left, target_right], x_grid, z_matrix[0])
    front_skew = float(iv_left - iv_right)

    diagnostics = {
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

    # Custom hover template
    hover_template = (
        '<b>%{text}</b><br>' +
        f'{x_title}: %{{x:.1f}}<br>' +
        'DTE: %{y:.1f} days<br>' +
        'Implied Vol: <b>%{z:.2f}%</b>' +
        '<extra></extra>'
    )

    # Hover text matrix matching shape of z_matrix
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

    # Camera viewpoints
    camera_views = {
        'default': dict(eye=dict(x=1.65, y=-1.65, z=1.25)),
        'smile': dict(eye=dict(x=0.0, y=-2.4, z=0.15)),      # Front view: Strike vs IV
        'term': dict(eye=dict(x=2.4, y=0.0, z=0.15)),       # Side view: DTE vs IV
        'topdown': dict(eye=dict(x=0.0, y=0.0, z=2.6))      # Top view: Heatmap contour
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
                title=dict(text='Implied Volatility (IV %)', font=dict(color='#94a3b8', size=11)),
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
                    Strike × DTE × IV
                </span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    expiries_data = load_expiry_chains(symbol, data_dir)
    if not expiries_data:
        st.info(f"No multi-expiry option chains found on disk for {symbol}. Chains sync automatically during market hours.")
        return

    # Surface configuration controls
    ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1.2, 1.2, 1.2, 1.4])

    with ctrl_col1:
        iv_mode = st.selectbox(
            "IV Selection",
            options=['otm', 'mid', 'call', 'put'],
            format_func=lambda x: {
                'otm': 'OTM Standard (P-Put / C-Call)',
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

    # Advanced filter toggles inside expander
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
                help="Cap extreme out-of-the-money IV solver spikes near expiry",
                key=f"3d_iv_cap_{symbol}"
            )

    # Extract surface mesh
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

    # Render Plotly 3D Figure
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

    # Metric Badges
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Expiries Mesh", f"{diag['num_expiries']} Expiries", f"{diag['min_dte']:.1f}d to {diag['max_dte']:.1f}d DTE")
    with m_col2:
        st.metric("Front ATM IV", f"{diag['front_atm_iv']:.2f}%")
    with m_col3:
        st.metric("Term Structure Slope", f"{diag['term_slope']:+.2f}%", help="Back ATM IV minus Front ATM IV")
    with m_col4:
        st.metric("Front Skew (96%-104%)", f"{diag['front_skew']:+.2f}%", help="Front expiry OTM Put IV minus OTM Call IV")
