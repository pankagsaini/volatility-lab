"""High-performance data engine for the Opstra-inspired Web Application."""

from datetime import datetime, date
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd

from market_data import IST

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


def get_market_summary() -> Dict[str, Any]:
    """Return live market session status, IST time, NIFTY, BANKNIFTY, and INDIA VIX."""
    now = datetime.now(IST)
    t_min = now.hour * 60 + now.minute
    is_open = (now.weekday() < 5) and (555 <= t_min <= 930)

    if is_open:
        status = 'NSE LIVE · REGULAR SESSION'
        status_type = 'live'
    elif now.weekday() < 5 and 540 <= t_min < 555:
        status = 'NSE PRE-OPEN'
        status_type = 'preopen'
    else:
        status = 'NSE CLOSED · POST-MARKET'
        status_type = 'closed'

    from expiry_data import load_snapshot
    from collector_support import read_json
    
    snap = load_snapshot(None, allow_saved=True) or {}
    rows = snap.get('rows', [])
    nifty = next((r for r in rows if r.get('Instrument') == 'NIFTY'), {})
    bn = next((r for r in rows if r.get('Instrument') == 'BANKNIFTY'), {})
    vix_obj = snap.get('india_vix', {})
    vix_val = vix_obj.get('value') if isinstance(vix_obj, dict) else None

    # Google Cloud mirror sync info
    cloud_info = read_json(DATA / 'google_archive' / 'status.json')
    sync_state = str(cloud_info.get('status', 'unknown')).strip().lower()
    sync_count = int(cloud_info.get('total_files', 0))

    def format_index(row: Dict[str, Any], default_name: str) -> Dict[str, Any]:
        spot = row.get('Price')
        chg = row.get('% change')
        iv = row.get('IV')
        sprd = row.get('IV-RV Spread')
        chg_f = float(chg) if chg and np.isfinite(chg) else None
        as_of_str = str(row.get('As of', ''))[:16].replace('T', ' ')
        return {
            'symbol': default_name,
            'price': float(spot) if spot and np.isfinite(spot) else None,
            'change': chg_f,
            'pchange': chg_f,
            'change_pct': chg_f,
            'atm_iv': float(iv) if iv and np.isfinite(iv) else None,
            'iv_rv_spread': float(sprd) if sprd and np.isfinite(sprd) else None,
            'collected_at': as_of_str,
            'as_of': as_of_str
        }

    return {
        'ist_time': now.strftime('%d %b %Y %H:%M:%S IST'),
        'market_status': status,
        'status_type': status_type,
        'market_open': is_open,
        'is_open': is_open,
        'nifty': format_index(nifty, 'NIFTY 50'),
        'banknifty': format_index(bn, 'BANKNIFTY'),
        'india_vix': {
            'symbol': 'INDIA VIX',
            'price': float(vix_val) if vix_val and np.isfinite(vix_val) else None,
            'value': float(vix_val) if vix_val and np.isfinite(vix_val) else None,
            'change': None,
            'pchange': None,
            'regime': 'Low Vol' if vix_val and vix_val < 13 else ('Normal Vol' if vix_val and vix_val <= 18 else 'Elevated Vol')
        },
        'cloud_sync': {
            'status': sync_state,
            'last_snapshot': str(cloud_info.get('last_snapshot', cloud_info.get('last_update', ''))),
            'today_count': sync_count,
            'total_files': sync_count
        }
    }


def get_available_expiries(symbol: str) -> List[Dict[str, Any]]:
    """Return all active listed expiries for a symbol with DTE and contract type."""
    today = datetime.now(IST).date()
    files = sorted(DATA.glob(f'{symbol.lower()}_option_quotes_*.json'))
    
    expiries = []
    for f in files:
        exp_str = f.stem.replace(f'{symbol.lower()}_option_quotes_', '')
        try:
            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
            if exp_date >= today:
                dte = max(0.2, float((exp_date - today).days))
                # Heuristic: monthly is typically last Tuesday/Thursday of month
                expiries.append({
                    'expiry': exp_str,
                    'expiry_date': str(exp_date),
                    'dte': round(dte, 1),
                    'label': f"{exp_date.strftime('%d %b %Y')} ({dte:.1f}d)"
                })
        except ValueError:
            continue

    expiries.sort(key=lambda x: x['dte'])
    return expiries


def get_option_chain_table(symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    """Opstra-style option chain table with Call Greeks, Strike, Put Greeks, PCR, and Max Pain."""
    available = get_available_expiries(symbol)
    if not available:
        return {'symbol': symbol, 'error': f'No active option chains found for {symbol}'}

    if not expiry or not any(e['expiry'] == expiry for e in available):
        expiry = available[0]['expiry']

    file_path = DATA / f'{symbol.lower()}_option_quotes_{expiry}.json'
    if not file_path.exists():
        return {'symbol': symbol, 'expiry': expiry, 'error': 'Option chain quote file not found'}

    try:
        with file_path.open('r', encoding='utf-8') as f:
            content = json.load(f)
    except Exception as exc:
        return {'symbol': symbol, 'expiry': expiry, 'error': str(exc)}

    spot = float(content.get('spot', 0.0))
    raw_chain = content.get('broker_option_chain', [])

    # Strike step and nearest ATM strike
    strike_step = 50.0 if spot < 35000 else 100.0
    atm_strike = round(spot / strike_step) * strike_step

    rows = []
    total_call_oi = 0
    total_put_oi = 0
    total_call_vol = 0
    total_put_vol = 0
    oi_by_strike = {}

    for item in raw_chain:
        k = float(item.get('strike_price', 0.0))
        if k <= 0:
            continue

        call = item.get('call_options', {})
        put = item.get('put_options', {})
        c_m = call.get('market_data', {})
        c_g = call.get('option_greeks', {})
        p_m = put.get('market_data', {})
        p_g = put.get('option_greeks', {})

        c_oi = int(c_m.get('oi') or 0)
        p_oi = int(p_m.get('oi') or 0)
        c_vol = int(c_m.get('volume') or 0)
        p_vol = int(p_m.get('volume') or 0)

        total_call_oi += c_oi
        total_put_oi += p_oi
        total_call_vol += c_vol
        total_put_vol += p_vol
        oi_by_strike[k] = (c_oi, p_oi)

        # Filter strikes to active trading window around spot (e.g. +/- 8%)
        if abs(k - spot) / spot <= 0.08:
            rows.append({
                'strike': k,
                'is_atm': abs(k - atm_strike) < (strike_step * 0.5),
                'call': {
                    'ltp': float(c_m.get('ltp') or 0.0),
                    'iv': float(c_g.get('iv') or 0.0),
                    'delta': float(c_g.get('delta') or 0.0),
                    'gamma': float(c_g.get('gamma') or 0.0),
                    'theta': float(c_g.get('theta') or 0.0),
                    'vega': float(c_g.get('vega') or 0.0),
                    'oi': c_oi,
                    'volume': c_vol
                },
                'put': {
                    'ltp': float(p_m.get('ltp') or 0.0),
                    'iv': float(p_g.get('iv') or 0.0),
                    'delta': float(p_g.get('delta') or 0.0),
                    'gamma': float(p_g.get('gamma') or 0.0),
                    'theta': float(p_g.get('theta') or 0.0),
                    'vega': float(p_g.get('vega') or 0.0),
                    'oi': p_oi,
                    'volume': p_vol
                }
            })

    rows.sort(key=lambda r: r['strike'])

    # Max Pain Calculation across all listed strikes
    max_pain = atm_strike
    if oi_by_strike:
        strikes = sorted(oi_by_strike.keys())
        min_loss = float('inf')
        for test_s in strikes:
            total_loss = 0.0
            for s, (coi, poi) in oi_by_strike.items():
                if test_s > s:
                    total_loss += (test_s - s) * coi
                elif test_s < s:
                    total_loss += (s - test_s) * poi
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain = test_s

    # ATM Straddle Price
    atm_row = next((r for r in rows if r['is_atm']), None)
    atm_straddle = (atm_row['call']['ltp'] + atm_row['put']['ltp']) if atm_row else 0.0
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

    matched_exp = next((e for e in available if e['expiry'] == expiry), None)
    dte_val = float(matched_exp['dte']) if matched_exp else 0.0

    return {
        'symbol': symbol,
        'expiry': expiry,
        'dte': dte_val,
        'spot': spot,
        'atm_strike': atm_strike,
        'straddle_ltp': round(atm_straddle, 2),
        'atm_straddle': round(atm_straddle, 2),
        'total_call_oi': total_call_oi,
        'total_put_oi': total_put_oi,
        'total_call_vol': total_call_vol,
        'total_put_vol': total_put_vol,
        'pcr': pcr,
        'max_pain': max_pain,
        'rows': rows
    }


def get_vol_surface_payload(
    symbol: str,
    iv_mode: str = 'otm',
    space_mode: str = 'strike',
    range_pct: float = 8.0,
    include_zero_dte: bool = True,
    selected_expiry: Optional[str] = None
) -> Dict[str, Any]:
    """Opstra-grade 3D Volatility Surface, 2D Multi-Expiry Smile Overlay, and Term Structure."""
    from vol_surface_3d import load_expiry_chains, extract_surface_mesh

    chains = load_expiry_chains(symbol, DATA)
    if not chains:
        return {'symbol': symbol, 'error': f'No chain observations found for {symbol}'}

    x_grid, y_grid, z_matrix, expiry_labels, diags = extract_surface_mesh(
        expiries_data=chains,
        iv_mode=iv_mode,
        space_mode=space_mode,
        moneyness_range_pct=range_pct,
        n_strikes=25,
        include_zero_dte=include_zero_dte,
        iv_cap=120.0
    )

    if z_matrix.size == 0 or len(y_grid) < 2:
        return {'symbol': symbol, 'error': 'Insufficient expiries to construct 3D surface (min 2 required)'}

    ref_spot = diags['ref_spot']

    # 1. ATM Ridge (The exact Term Structure spine along ATM across expiries)
    target_atm = 100.0 if space_mode == 'moneyness' else ref_spot
    atm_idx = int(np.argmin(np.abs(np.array(x_grid) - target_atm)))
    atm_ridge_x = []
    atm_ridge_y = []
    atm_ridge_z = []
    atm_ridge_expiries = []
    for i, entry in enumerate(chains if include_zero_dte else [c for c in chains if c['dte'] >= 1.0]):
        if i < len(y_grid):
            atm_ridge_x.append(float(x_grid[atm_idx]))
            atm_ridge_y.append(float(y_grid[i]))
            atm_ridge_z.append(float(z_matrix[i, atm_idx]))
            atm_ridge_expiries.append(entry['expiry'])
    atm_ridge = {
        'x': atm_ridge_x,
        'y': atm_ridge_y,
        'z': atm_ridge_z,
        'expiries': atm_ridge_expiries
    }

    # 2. Opstra-Style 2D Smile Curves for every listed expiry
    smile_curves = []
    colors = ['#06b6d4', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#3b82f6', '#14b8a6']
    
    for i, entry in enumerate(chains if include_zero_dte else [c for c in chains if c['dte'] >= 1.0]):
        if i < len(y_grid):
            # Extract raw strikes within range for clean curve
            extracted_s = []
            extracted_iv = []
            spot = entry['spot']
            for item in entry['chain']:
                k = item.get('strike_price')
                if not k or abs(k - spot) / spot > (range_pct / 100.0 * 1.15):
                    continue
                c_iv = item.get('call_options', {}).get('option_greeks', {}).get('iv', 0.0) or 0.0
                p_iv = item.get('put_options', {}).get('option_greeks', {}).get('iv', 0.0) or 0.0
                val = p_iv if k < spot else c_iv
                if val > 0:
                    extracted_s.append(float(k))
                    extracted_iv.append(min(float(val), 120.0))
            
            if len(extracted_s) >= 4:
                order = np.argsort(extracted_s)
                smile_curves.append({
                    'expiry': entry['expiry'],
                    'label': f"{entry['expiry']} ({entry['dte']:.1f}d)",
                    'dte': float(entry['dte']),
                    'color': colors[i % len(colors)],
                    'strikes': [float(x) for x in np.array(extracted_s)[order]],
                    'ivs': [float(y) for y in np.array(extracted_iv)[order]]
                })

    # 3. Term Structure Curve (ATM IV vs DTE)
    ts_dtes = []
    ts_ivs = []
    ts_expiries = []
    for sc in smile_curves:
        atm_iv_ts = next(
            (iv for s, iv in zip(sc['strikes'], sc['ivs']) if abs(s - ref_spot) < 150),
            sc['ivs'][len(sc['ivs']) // 2] if sc['ivs'] else 0.0
        )
        ts_dtes.append(float(sc['dte']))
        ts_ivs.append(float(atm_iv_ts))
        ts_expiries.append(sc['expiry'])

    term_regime = 'Contango (Normal)' if diags['term_slope'] > 0.5 else (
        'Backwardation (Inverted)' if diags['term_slope'] < -0.5 else 'Flat Term'
    )
    term_structure = {
        'dtes': ts_dtes,
        'ivs': ts_ivs,
        'expiries': ts_expiries,
        'regime': term_regime
    }

    selected_dte = next((float(sc['dte']) for sc in smile_curves if sc.get('expiry') == selected_expiry), None) if selected_expiry else None
    for sc in smile_curves:
        sc['is_selected'] = (sc.get('expiry') == selected_expiry) if selected_expiry else False

    return {
        'symbol': symbol,
        'spot': ref_spot,
        'ref_spot': ref_spot,
        'selected_expiry': selected_expiry,
        'selected_dte': selected_dte,
        'space_mode': space_mode,
        'x_grid': [float(x) for x in x_grid],
        'y_grid': [float(y) for y in y_grid],
        'z_matrix': [[float(cell) for cell in row] for row in z_matrix],
        'expiry_labels': expiry_labels,
        'atm_ridge': atm_ridge,
        'smile_curves': smile_curves,
        'term_structure': term_structure,
        'diagnostics': {
            'num_expiries': diags['num_expiries'],
            'min_dte': diags['min_dte'],
            'max_dte': diags['max_dte'],
            'front_atm_iv': diags['front_atm_iv'],
            'back_atm_iv': diags['back_atm_iv'],
            'term_slope': diags['term_slope'],
            'term_regime': term_regime,
            'front_skew': diags['front_skew'],
            'interp_method': 'linear',
            'space_mode': space_mode,
            'iv_mode': iv_mode,
            'range_pct': range_pct
        }
    }


def get_trade_radar(symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    """Executive trade opportunity radar and ATM expected expiry move metrics."""
    from expiry_data import load_snapshot
    snap = load_snapshot(None, allow_saved=True) or {}
    rows = snap.get('rows', [])
    selected_rows = [r for r in rows if r.get('Instrument') == symbol]
    if expiry:
        row = next((r for r in selected_rows if r.get('Expiry') == expiry), None)
        if not row and selected_rows:
            row = selected_rows[0]
    else:
        row = selected_rows[0] if selected_rows else None
    
    if not row or not row.get('Price') or not row.get('IV'):
        return {'symbol': symbol, 'error': f'Live quotes not available for {symbol}'}

    spot = float(row['Price'])
    iv = float(row['IV'])
    spread = row.get('IV-RV Spread')
    spread_val = float(spread) if spread and np.isfinite(spread) else None
    exp = row.get('Expiry', '')

    today = datetime.now(IST).date()
    try:
        exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
        dte = max(0.2, float((exp_date - today).days))
    except Exception:
        dte = 6.0

    move_pts = spot * (iv / 100.0) * np.sqrt(dte / 365.0)
    move_pct = (move_pts / spot) * 100.0

    # Trade Opportunity Classification
    if spread_val is not None:
        if spread_val > 1.5:
            bias_title = 'RICH VOLATILITY'
            bias_type = 'rich'
            bias_action = 'PREMIUM SELLING FAVORABLE'
            desc = 'Option pricing commands an attractive volatility premium over 30-day realized volatility. Credit Spreads, Iron Condors, and short theta capture favored.'
        elif spread_val < -1.5:
            bias_title = 'COMPRESSED VOLATILITY'
            bias_type = 'compressed'
            bias_action = 'LONG GAMMA / DEBIT SPREADS FAVORABLE'
            desc = 'Option IV is statistically discounted relative to historical price movement. Long Straddles, Calendars, and Debit Diagonal Spreads favored.'
        else:
            bias_title = 'FAIR VALUE'
            bias_type = 'neutral'
            bias_action = 'DIRECTIONAL / SKEW NEUTRAL'
            desc = 'Implied volatility tracks realized volatility within normal statistical bounds. Favor directional trend or strike skew strategies.'
    else:
        bias_title = 'PENDING ALIGNMENT'
        bias_type = 'pending'
        bias_action = 'HISTORICAL CLOSES UPDATING'
        desc = 'Daily historical candles aligning for IV-RV spread benchmark.'

    # Parity Check
    syn = row.get('Synthetic future')
    m_fut = row.get('Month future LTP')
    basis_pts = (float(syn) - float(m_fut)) if syn and m_fut and np.isfinite(syn) and np.isfinite(m_fut) else None
    
    if basis_pts is not None:
        basis_status = 'Parity Normal' if abs(basis_pts) <= (30.0 if symbol == 'NIFTY' else 75.0) else 'Basis Dislocation'
    else:
        basis_status = 'Parity Reference'

    # Compute ATM straddle LTP from option chain file
    straddle_ltp = 0.0
    strike_step = 50.0 if ('NIFTY' in symbol.upper() and 'BANK' not in symbol.upper()) else 100.0
    atm_strike_r = round(spot / strike_step) * strike_step
    try:
        chain_file = DATA / f"{symbol.lower()}_option_quotes_{exp}.json"
        if chain_file.exists():
            import json as _json
            _content = _json.loads(chain_file.read_text(encoding='utf-8'))
            _chain = _content.get('broker_option_chain', [])
            _atm_row = next(
                (r for r in _chain if abs(float(r.get('strike_price', 0)) - atm_strike_r) < (strike_step * 0.6)),
                None
            )
            if _atm_row:
                c_ltp = float(_atm_row.get('call_options', {}).get('market_data', {}).get('ltp') or 0.0)
                p_ltp = float(_atm_row.get('put_options', {}).get('market_data', {}).get('ltp') or 0.0)
                straddle_ltp = round(c_ltp + p_ltp, 2)
    except Exception:
        straddle_ltp = 0.0

    return {
        'symbol': symbol,
        'spot': spot,
        'expiry': exp,
        'dte': round(dte, 1),
        'atm_strike': atm_strike_r,
        'atm_iv': round(iv, 2),
        'iv': round(iv, 2),
        'spread': round(spread_val, 2) if spread_val is not None else None,
        'bias_title': bias_title,
        'bias_type': bias_type,
        'bias_action': bias_action,
        'notes': [desc],
        'description': desc,
        'straddle_ltp': straddle_ltp,
        'straddle_be_low': round(spot - straddle_ltp, 1) if straddle_ltp > 0 else None,
        'straddle_be_high': round(spot + straddle_ltp, 1) if straddle_ltp > 0 else None,
        'expected_move_pts': round(move_pts, 1),
        'expected_move_pct': round(move_pct, 2),
        'expected_range_low': round(spot - move_pts, 1),
        'expected_range_high': round(spot + move_pts, 1),
        'synthetic_future': round(float(syn), 2) if syn and np.isfinite(syn) else None,
        'month_future': round(float(m_fut), 2) if m_fut and np.isfinite(m_fut) else None,
        'synthetic_basis_pts': round(basis_pts, 2) if basis_pts is not None else None,
        'basis_pts': round(basis_pts, 2) if basis_pts is not None else None,
        'basis_status': basis_status
    }


def get_futures_carry_payload() -> Dict[str, Any]:
    """Futures basis, roll spread, and annualized carry."""
    from futures_data import load_latest
    from pricing_inputs import get_rate
    
    rate_info = get_rate()
    rf = rate_info.get('yield_percent', 5.9)
    
    results = []
    for symbol in ('NIFTY', 'BANKNIFTY'):
        latest = load_latest(DATA / 'futures', symbol)
        if not latest:
            continue
        results.append({
            'symbol': symbol,
            'collected': latest.get('collected'),
            'pairs': latest.get('pairs', []),
            'legs': latest.get('legs', [])
        })
            
    return {'rate_percent': rf, 'indices': results}


def get_historical_candles(symbol: str = 'NIFTY') -> dict:
    """Historical daily candles and volume estimates for TradingView Lightweight Charts."""
    sym = 'banknifty' if 'BANK' in symbol.upper() else 'nifty'
    csv_path = DATA / f"{sym}_one_year.csv"
    if not csv_path.exists():
        return {'symbol': symbol, 'candles': [], 'volume': []}
    
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        candles = []
        volumes = []
        for _, row in df.iterrows():
            d_str = str(row['Date'])
            o = float(row['Open'])
            h = float(row['High'])
            l = float(row['Low'])
            c = float(row['Close'])
            candles.append({
                'time': d_str,
                'open': round(o, 2),
                'high': round(h, 2),
                'low': round(l, 2),
                'close': round(c, 2),
            })
            # Use real volume from CSV if present, otherwise estimate from true range
            if 'Volume' in df.columns and pd.notna(row.get('Volume')):
                vol_val = max(0, int(row.get('Volume', 0) or 0))
            else:
                vol_val = max(1000, int(abs(h - l) * (150 if sym == 'nifty' else 40)))
            volumes.append({
                'time': d_str,
                'value': vol_val,
                'color': 'rgba(16, 185, 129, 0.45)' if c >= o else 'rgba(244, 63, 94, 0.45)'
            })
        return {
            'symbol': symbol,
            'count': len(candles),
            'last_price': candles[-1]['close'] if candles else 0.0,
            'candles': candles,
            'volumes': volumes
        }
    except Exception as exc:
        return {'symbol': symbol, 'error': str(exc), 'candles': [], 'volumes': []}


def get_backtest_overview() -> dict:
    """Recent backtest reports with win rate, net PnL, drawdowns, and trade records."""
    runs_dir = DATA / 'backtest_runs'
    if not runs_dir.exists():
        return {'runs': []}
    
    reports = []
    import json
    for p in sorted(runs_dir.glob('*.json'), reverse=True):
        try:
            raw = json.loads(p.read_text(encoding='utf-8'))
            trades = raw.get('trades', [])
            settings = raw.get('settings', {})
            name = settings.get('name', 'Strategy')
            sym = settings.get('symbol', 'NIFTY')
            
            wins = sum(1 for t in trades if float(t.get('Net', 0)) > 0)
            losses = sum(1 for t in trades if float(t.get('Net', 0)) < 0)
            total_net = sum(float(t.get('Net', 0)) for t in trades)
            total_gross = sum(float(t.get('Gross', 0)) for t in trades)
            total_charges = sum(float(t.get('Charges', 0)) for t in trades)
            total_slippage = sum(float(t.get('Slippage', 0)) for t in trades)
            win_rate = round((wins / len(trades) * 100), 1) if trades else 0.0

            reports.append({
                'id': p.stem,
                'name': name,
                'symbol': sym,
                'start': settings.get('start'),
                'end': settings.get('end'),
                'engine': settings.get('engine', 'v1'),
                'total_trades': len(trades),
                'wins': wins,
                'losses': losses,
                'win_rate_pct': win_rate,
                'total_net_pnl': round(total_net, 2),
                'total_gross_pnl': round(total_gross, 2),
                'total_charges': round(total_charges, 2),
                'total_slippage': round(total_slippage, 2),
                'trades': trades[:15]  # recent sample
            })
        except Exception:
            continue
    return {'runs': reports}

