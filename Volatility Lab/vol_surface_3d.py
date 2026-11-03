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
        
        # Calculate DTE in calendar days
        dte = max(0.2, float((exp_date - today).days))
        
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
        strike_step = 50.0 if ref_spot < 35000 else 100.0
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
    atm_idx = len(x_grid) // 2
    front_atm_iv = float(z_matrix[0, atm_idx])
    back_atm_iv = float(z_matrix[-1, atm_idx])
    term_slope = back_atm_iv - front_atm_iv
    
    # Skew steepness: 96% Moneyness IV minus 104% Moneyness IV at front expiry
    left_idx = max(0, int(len(x_grid) * 0.2))
    right_idx = min(len(x_grid) - 1, int(len(x_grid) * 0.8))
    front_skew = float(z_matrix[0, left_idx] - z_matrix[0, right_idx])

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
            aspectratio=dict(x=1.3, y=1.1, z=0.85)
        )
    )

    return fig


def render_vol_surface_3d(symbol: str, selected_row: Dict[str, Any], result: Dict[str, Any], data_dir: Path):
    """Interactive Streamlit component to render the 3D Volatility Surface."""
    st.markdown("""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <div>
                <span style="font-size:1.15rem; font-weight:600; color:#f8fafc; letter-spacing:-0.02em;">
                    Institutional 3D Volatility Surface
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

    # Surface Controls Row
    c1, c2, c3, c4 = st.columns([1.5, 1.5, 1.2, 1.5])
    
    space_label = c1.segmented_control(
        'X-Axis Space',
        ['Moneyness %', 'Absolute Strike (₹)'],
        default='Moneyness %',
        key=f'surf_space_{symbol}',
        label_visibility='collapsed'
    ) or 'Moneyness %'
    space_mode = 'moneyness' if space_label == 'Moneyness %' else 'strike'

    iv_label = c2.segmented_control(
        'IV Skew Metric',
        ['OTM Skew', 'Call IV', 'Put IV', 'Call/Put Mid'],
        default='OTM Skew',
        key=f'surf_iv_mode_{symbol}',
        label_visibility='collapsed'
    ) or 'OTM Skew'
    iv_mode_map = {'OTM Skew': 'otm', 'Call IV': 'call', 'Put IV': 'put', 'Call/Put Mid': 'mid'}
    iv_mode = iv_mode_map.get(iv_label, 'otm')

    preset_label = c3.selectbox(
        'Camera View',
        ['3D Isometric', 'Smile Profile (Strike)', 'Term Profile (DTE)', 'Top-Down Heatmap'],
        key=f'surf_cam_{symbol}'
    )
    cam_preset_map = {
        '3D Isometric': 'default',
        'Smile Profile (Strike)': 'smile',
        'Term Profile (DTE)': 'term',
        'Top-Down Heatmap': 'topdown'
    }
    camera_preset = cam_preset_map.get(preset_label, 'default')

    with c4.popover('⚙️ Surface Settings'):
        moneyness_range = st.slider('Strike Range Around ATM (±%)', 4.0, 15.0, 8.0, 0.5, key=f'surf_mrange_{symbol}')
        colorscale = st.selectbox('Palette', ['Tealgrn', 'Viridis', 'Turbo', 'Plasma', 'Electric'], key=f'surf_color_{symbol}')
        include_zero_dte = st.toggle('Include 0-DTE Expiry', value=True, key=f'surf_0dte_{symbol}', help='Near-expiry deep wings can have solver artifacts.')
        iv_cap = st.slider('IV Outlier Cap (%)', 60.0, 250.0, 120.0, 10.0, key=f'surf_ivcap_{symbol}')

    # Extract mesh
    x_grid, y_grid, z_matrix, expiry_labels, diags = extract_surface_mesh(
        expiries_data=expiries_data,
        iv_mode=iv_mode,
        space_mode=space_mode,
        moneyness_range_pct=moneyness_range,
        n_strikes=27,
        include_zero_dte=include_zero_dte,
        iv_cap=iv_cap
    )

    if z_matrix.size == 0 or len(y_grid) < 2:
        st.warning('Insufficient multi-expiry quotes to render a 3D surface mesh (at least 2 expiries required).')
        return

    # Metrics Strip
    m1, m2, m3, m4 = st.columns(4)
    m1.metric('Expiries Mapped', f"{diags['num_expiries']} listed dates", f"{diags['min_dte']:.1f}d to {diags['max_dte']:.0f}d")
    
    slope = diags['term_slope']
    slope_desc = 'Contango (Normal)' if slope > 0.5 else ('Backwardation (Inverted)' if slope < -0.5 else 'Flat Term')
    m2.metric('ATM Term Slope', f"{slope:+.2f} pp", slope_desc, delta_color='normal' if slope >= 0 else 'inverse')
    
    skew = diags['front_skew']
    m3.metric('Front Skew (Put−Call)', f"{skew:+.2f} pp", 'Downside Put Premium' if skew > 0 else 'Call Wing Premium')
    
    m4.metric('Reference Spot', f"{diags['ref_spot']:,.2f}", f"ATM IV: {diags['front_atm_iv']:.2f}%")

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

    st.plotly_chart(fig, use_container_width=True, key=f'plotly_surf_{symbol}', config={'displaylogo': False, 'modeBarButtonsToRemove': ['lasso2d', 'select2d']})

    st.caption('💡 **3D Projections**: Floor shows iso-volatility contours; Side wall shows Term Structure slices; Front wall shows Volatility Smile curvature. Click and drag in the chart to freely rotate in 3D.')

    # Export Popover
    with st.popover('Export 3D Surface Matrix'):
        x_col_names = [f"{v:.1f}%" if space_mode == 'moneyness' else f"{v:.0f}" for v in x_grid]
        df_export = pd.DataFrame(z_matrix, index=expiry_labels, columns=x_col_names)
        df_export.index.name = 'Expiry (DTE)'
        st.dataframe(df_export.round(2), use_container_width=True)
        st.download_button(
            'Download Surface CSV',
            df_export.to_csv().encode('utf-8'),
            file_name=f'{symbol.lower()}_vol_surface_3d.csv',
            mime='text/csv',
            key=f'dl_surf_{symbol}'
        )
