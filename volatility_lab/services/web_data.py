"""High-performance data engine for the Volatility Lab Web Application."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT

from datetime import datetime, date
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd

from market_data import IST
from volatility_lab.core.market_calendar import session_window, expiry_close, SPECIAL_SESSIONS
from volatility_lab.pricing.pricing_policy import number

ROOT = PACKAGE_ROOT
DATA = runtime_data_dir()


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def observed_count(value):
    value = number(value)
    return int(value) if value is not None and value >= 0 and value.is_integer() else None


def get_market_summary(now=None) -> Dict[str, Any]:
    """Return live market session status, IST time, NIFTY, BANKNIFTY, and INDIA VIX."""
    now = now or datetime.now(IST)
    now = pd.Timestamp(now).tz_convert(IST).to_pydatetime()
    window = session_window(now.date())
    is_open = bool(window and window[0] <= now < window[1])

    if is_open:
        status = 'NSE LIVE · SPECIAL SESSION' if now.date() in SPECIAL_SESSIONS else 'NSE LIVE · REGULAR SESSION'
        status_type = 'live'
    elif window and now.date() not in SPECIAL_SESSIONS and window[0] - pd.Timedelta(minutes=15) <= now < window[0]:
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
    vix_val = positive(vix_obj.get('value')) if isinstance(vix_obj, dict) else None

    # Google Cloud mirror sync info
    cloud_info = read_json(DATA / 'google_archive' / 'status.json')
    sync_state = str(cloud_info.get('status', 'unknown')).strip().lower()
    sync_count = observed_count(cloud_info.get('total_files'))

    def format_index(row: Dict[str, Any], default_name: str) -> Dict[str, Any]:
        spot = row.get('Price')
        fut = row.get('Month future LTP') or row.get('Listed future')
        syn = row.get('Synthetic future')
        chg = row.get('% change')
        iv = row.get('IV')
        sprd = row.get('IV-RV Spread')
        chg_f = number(chg)
        as_of_str = str(row.get('As of', ''))[:16].replace('T', ' ')
        fut_f, syn_f, spot_f = positive(fut), positive(syn), positive(spot)
        return {
            'symbol': default_name,
            'price': spot_f,
            'spot': spot_f,
            'future_price': fut_f,
            'synthetic_future': syn_f,
            'future_basis': round(fut_f - spot_f, 2) if fut_f is not None and spot_f is not None else None,
            'synthetic_basis': round(syn_f - spot_f, 2) if syn_f is not None and spot_f is not None else None,
            'change': chg_f,
            'pchange': chg_f,
            'change_pct': chg_f,
            'atm_iv': positive(iv),
            'iv_rv_spread': number(sprd),
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
            'regime': None if number(vix_val) is None or vix_val <= 0 else
                      'Low Vol' if vix_val < 13 else 'Normal Vol' if vix_val <= 18 else 'Elevated Vol'
        },
        'cloud_sync': {
            'status': sync_state,
            'last_snapshot': str(cloud_info.get('last_snapshot', cloud_info.get('last_update', ''))),
            'today_count': observed_count(cloud_info.get('today_count')),
            'total_files': sync_count
        }
    }


def get_available_expiries(symbol: str) -> List[Dict[str, Any]]:
    """Return all active listed expiries for a symbol with DTE and contract type."""
    now = datetime.now(IST)
    today = now.date()
    files = sorted(DATA.glob(f'{symbol.lower()}_option_quotes_*.json'))

    expiries = []
    for f in files:
        exp_str = f.stem.replace(f'{symbol.lower()}_option_quotes_', '')
        try:
            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
            if exp_date >= today:
                dte = max(0.0, (expiry_close(exp_date) - now).total_seconds() / 86400)
                expiries.append({
                    'expiry': exp_str,
                    'expiry_date': str(exp_date),
                    'dte': dte,
                    'label': f"{exp_date.strftime('%d %b %Y')} ({dte:.1f}d)"
                })
        except ValueError:
            continue

    expiries.sort(key=lambda x: x['dte'])
    return expiries


def get_option_chain_table(symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
    """Option chain table with Call Greeks, Strike, Put Greeks, PCR, and Max Pain."""
    available = get_available_expiries(symbol)
    if not available:
        return {'symbol': symbol, 'error': f'No active option chains found for {symbol}'}

    if expiry and not any(e['expiry'] == expiry for e in available):
        return {'symbol': symbol, 'expiry': expiry, 'error': 'Requested expiry is unavailable'}
    if not expiry:
        expiry = available[0]['expiry']

    file_path = DATA / f'{symbol.lower()}_option_quotes_{expiry}.json'
    if not file_path.exists():
        return {'symbol': symbol, 'expiry': expiry, 'error': 'Option chain quote file not found'}

    try:
        with file_path.open('r', encoding='utf-8') as f:
            content = json.load(f)
    except Exception as exc:
        return {'symbol': symbol, 'expiry': expiry, 'error': str(exc)}

    spot = number(content.get('spot'))
    if spot is None or spot <= 0:
        return {'symbol': symbol, 'expiry': expiry, 'error': 'Underlying spot is unavailable'}
    raw_chain = content.get('broker_option_chain', [])

    def aware_payload_timestamp(*values):
        for value in values:
            try:
                stamp = pd.Timestamp(value)
                if stamp.tzinfo is not None:
                    return stamp.tz_convert('Asia/Kolkata')
            except (TypeError, ValueError, OverflowError):
                continue
        return None

    # Strike step and nearest ATM strike
    strikes = [number(row.get('strike_price')) for row in raw_chain if isinstance(row, dict)]
    strikes = [strike for strike in strikes if strike is not None and strike > 0]
    if not strikes:
        return {'symbol': symbol, 'expiry': expiry, 'error': 'No captured strikes'}
    atm_strike = min(strikes, key=lambda strike: (abs(strike - spot), strike))

    rows = []
    oi_by_strike = {}
    volumes_by_strike = {}
    def observed_total(values):
        return sum(values) if values and all(v is not None for v in values) else None

    for item in raw_chain:
        k = positive(item.get('strike_price')) if isinstance(item, dict) else None
        if k is None:
            continue

        call = item.get('call_options') or {}
        put = item.get('put_options') or {}
        c_m = call.get('market_data') or {}
        c_g = call.get('option_greeks') or {}
        p_m = put.get('market_data') or {}
        p_g = put.get('option_greeks') or {}

        c_oi, p_oi = observed_count(c_m.get('oi')), observed_count(p_m.get('oi'))
        c_vol, p_vol = observed_count(c_m.get('volume')), observed_count(p_m.get('volume'))
        oi_by_strike[k] = (c_oi, p_oi)
        volumes_by_strike[k] = (c_vol, p_vol)

        # Filter strikes to active trading window around spot (e.g. +/- 8%)
        if abs(k - spot) / spot <= 0.08:
            rows.append({
                'strike': k,
                'is_atm': k == atm_strike,
                'call': {
                    'ltp': positive(c_m.get('ltp')),
                    'iv': positive(c_g.get('iv')),
                    'delta': number(c_g.get('delta')),
                    'gamma': number(c_g.get('gamma')),
                    'theta': number(c_g.get('theta')),
                    'vega': number(c_g.get('vega')),
                    'oi': c_oi,
                    'volume': c_vol
                },
                'put': {
                    'ltp': positive(p_m.get('ltp')),
                    'iv': positive(p_g.get('iv')),
                    'delta': number(p_g.get('delta')),
                    'gamma': number(p_g.get('gamma')),
                    'theta': number(p_g.get('theta')),
                    'vega': number(p_g.get('vega')),
                    'oi': p_oi,
                    'volume': p_vol
                }
            })

    rows.sort(key=lambda r: r['strike'])

    # Max Pain Calculation across all listed strikes
    max_pain = None
    total_call_oi, total_put_oi = (observed_total(side) for side in zip(*oi_by_strike.values()))
    total_call_vol, total_put_vol = (observed_total(side) for side in zip(*volumes_by_strike.values()))
    if total_call_oi is not None and total_put_oi is not None and total_call_oi + total_put_oi > 0:
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

    # ATM Straddle & Synthetic Future
    atm_row = next((r for r in rows if r['is_atm']), None)
    leg_prices = [atm_row[side]['ltp'] for side in ('call', 'put')] if atm_row else []
    atm_straddle = round(sum(leg_prices), 2) if leg_prices and all(p is not None and p > 0 for p in leg_prices) else None
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi and total_put_oi is not None else None

    matched_exp = next((e for e in available if e['expiry'] == expiry), None)
    dte_val = float(matched_exp['dte']) if matched_exp else 0.0

    def same_expiry(left, right):
        try:
            left_stamp, right_stamp = pd.Timestamp(left), pd.Timestamp(right)
            return left_stamp.date() == right_stamp.date()
        except (TypeError, ValueError):
            return False

    matching_futures = [
        contract for contract in content.get('contracts', [])
        if isinstance(contract, dict)
        and str(contract.get('instrument_type', '')).upper() == 'FUT'
        and same_expiry(contract.get('expiry'), expiry)
    ]
    # A forward reference is valid only when the archive identifies exactly one
    # listed future with the exact option expiry; never borrow a monthly forward for a weekly.
    future_contract = matching_futures[0] if len(matching_futures) == 1 else None
    future_quote = None
    if future_contract:
        future_symbol = future_contract.get('tradingsymbol') or future_contract.get('trading_symbol')
        quote = (content.get('quotes') or {}).get(f'NFO:{future_symbol}')
        if isinstance(quote, dict):
            future_quote = dict(quote)
            future_quote['expiry'] = str(future_contract.get('expiry'))[:10]

    from synthetic_future import determine_forward
    spot_quote = content.get('spot_quote') if isinstance(content.get('spot_quote'), dict) else {}
    valuation = aware_payload_timestamp(content.get('broker_received_at'), content.get('valuation'))
    # Valuation/retrieval time is not an instrument event time.  Without an
    # explicit spot timestamp, observed forwards are deliberately withheld.
    spot_timestamp = spot_quote.get('timestamp')
    dte_for_pricing = dte_val
    if valuation is not None:
        try:
            dte_for_pricing = max(0.0, (expiry_close(expiry) - valuation).total_seconds() / 86400.0)
        except (TypeError, ValueError):
            pass
    # Archive valuations use inputs recorded alongside that capture; never today's inputs.
    from pricing_policy import recorded_inputs
    rate, dividend = recorded_inputs(content.get('pricing_inputs') or {}, symbol, valuation)
    fwd_res = determine_forward(
        raw_chain, spot, future_quote=future_quote, dte_days=dte_for_pricing,
        expiry=expiry, as_of=valuation, spot_timestamp=spot_timestamp,
        rate=rate, dividend=dividend,
    )
    forward_value = fwd_res.get('forward')
    forward_source = fwd_res.get('source')
    observed_forward = (
        float(forward_value)
        if forward_source in {'listed_future', 'synthetic_forward'}
        and forward_value is not None and np.isfinite(forward_value) and forward_value > 0
        else None
    )
    theoretical_forward = (
        float(forward_value)
        if fwd_res.get('confidence') == 'THEORETICAL'
        and forward_value is not None and np.isfinite(forward_value) and forward_value > 0
        else None
    )
    future_price = observed_forward if forward_source == 'listed_future' else None
    synthetic_future = observed_forward if forward_source == 'synthetic_forward' else None

    future_basis = round(future_price - spot, 2) if future_price and spot else None
    synthetic_basis = round(synthetic_future - spot, 2) if synthetic_future and spot else None

    return {
        'symbol': symbol,
        'expiry': expiry,
        'dte': dte_val,
        'spot': spot,
        'future_price': future_price,
        'future_basis': future_basis,
        'synthetic_future': synthetic_future,
        'synthetic_basis': synthetic_basis,
        'forward_price': observed_forward,
        'forward_source': forward_source,
        'forward_confidence': fwd_res.get('confidence'),
        'forward_status': 'Validated forward available' if observed_forward is not None else 'No validated forward available',
        'theoretical_forward': theoretical_forward,
        'pricing_model': {'black76': 'Black-76', 'bsm': 'BSM'}.get(fwd_res.get('model'), 'Broker IV only'),
        'pricing_reason': fwd_res.get('reason'),
        'atm_strike': atm_strike,
        'straddle_ltp': atm_straddle,
        'atm_straddle': atm_straddle,
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
    """3D Volatility Surface, 2D Multi-Expiry Smile Overlay, and Term Structure."""
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
    def finite(value):
        return float(value) if np.isfinite(value) else None

    smile_curves = diags['curves']
    colors = ['#38bdf8', '#10b981', '#a78bfa', '#8b5cf6', '#f43f5e', '#3b82f6', '#14b8a6']
    for i, curve in enumerate(smile_curves):
        curve['color'] = colors[i % len(colors)]
        curve['label'] = expiry_labels[i]

    atm = [curve for curve in smile_curves if curve['atm_iv'] is not None]
    ts_dtes = [curve['dte'] for curve in atm]
    ts_ivs = [curve['atm_iv'] for curve in atm]
    ts_expiries = [curve['expiry'] for curve in atm]
    atm_ridge = {
        'x': [curve['atm_strike'] / curve['spot'] * 100 if space_mode == 'moneyness'
              else curve['atm_strike'] for curve in atm],
        'y': ts_dtes, 'z': ts_ivs, 'expiries': ts_expiries,
    }
    term_regime = 'Unavailable' if not np.isfinite(diags['term_slope']) else 'Contango (Normal)' if diags['term_slope'] > 0.5 else (
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
        'as_of_by_expiry': {curve['expiry']: curve['as_of'] for curve in smile_curves},
        'space_mode': space_mode,
        'x_grid': [float(x) for x in x_grid],
        'y_grid': [float(y) for y in y_grid],
        'z_matrix': [[finite(cell) for cell in row] for row in z_matrix],
        'expiry_labels': expiry_labels,
        'atm_ridge': atm_ridge,
        'smile_curves': smile_curves,
        'term_structure': term_structure,
        'diagnostics': {
            'num_expiries': diags['num_expiries'],
            'min_dte': diags['min_dte'],
            'max_dte': diags['max_dte'],
            'front_atm_iv': finite(diags['front_atm_iv']),
            'back_atm_iv': finite(diags['back_atm_iv']),
            'term_slope': finite(diags['term_slope']),
            'term_regime': term_regime,
            'front_skew': finite(diags['front_skew']),
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
    else:
        row = selected_rows[0] if selected_rows else None

    spot = positive(row.get('Price')) if row else None
    iv = positive(row.get('IV')) if row else None
    if spot is None or iv is None:
        return {'symbol': symbol, 'error': f'Live quotes not available for {symbol}'}

    spread = row.get('IV-RV Spread')
    spread_val = number(spread)
    exp = row.get('Expiry', '')

    try:
        captured = pd.Timestamp(row.get('As of'))
        if pd.isna(captured) or captured.tzinfo is None:
            raise ValueError('Undated snapshot')
        dte = max(0.0, (expiry_close(exp) - captured).total_seconds() / 86400)
    except (ValueError, TypeError):
        return {'symbol': symbol, 'expiry': exp, 'error': 'Expiry or capture time is unavailable'}

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
            bias_title = 'FAIR VALUE SPREAD'
            bias_type = 'neutral'
            bias_action = 'DELTA-NEUTRAL / SKEW RELATIVE VALUE'
            desc = 'Implied volatility tracks realized volatility within normal statistical bounds. Favor delta-neutral calendar spreads, gamma scalping, or strike skew relative-value arbitrage.'
    else:
        bias_title = 'PENDING ALIGNMENT'
        bias_type = 'pending'
        bias_action = 'HISTORICAL CLOSES UPDATING'
        desc = 'Daily historical candles aligning for IV-RV spread benchmark.'

    # Parity Check
    syn = positive(row.get('Synthetic future'))
    m_fut = positive(row.get('Month future LTP'))
    basis_pts = syn - m_fut if syn is not None and m_fut is not None else None

    if basis_pts is not None:
        basis_status = 'Parity Normal' if abs(basis_pts) <= (30.0 if symbol == 'NIFTY' else 75.0) else 'Basis Dislocation'
    else:
        basis_status = 'Parity Reference'

    # Compute ATM straddle LTP from option chain file
    straddle_ltp = None
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
                c_ltp = number((_atm_row.get('call_options') or {}).get('market_data', {}).get('ltp'))
                p_ltp = number((_atm_row.get('put_options') or {}).get('market_data', {}).get('ltp'))
                if c_ltp is not None and p_ltp is not None and min(c_ltp, p_ltp) > 0:
                    straddle_ltp = round(c_ltp + p_ltp, 2)
    except Exception:
        straddle_ltp = None

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
        'straddle_be_low': round(atm_strike_r - straddle_ltp, 1) if straddle_ltp is not None else None,
        'straddle_be_high': round(atm_strike_r + straddle_ltp, 1) if straddle_ltp is not None else None,
        'as_of': captured.isoformat(),
        'expected_move_pts': round(move_pts, 1),
        'expected_move_pct': round(move_pct, 2),
        'expected_range_low': round(spot - move_pts, 1),
        'expected_range_high': round(spot + move_pts, 1),
        'synthetic_future': round(syn, 2) if syn is not None else None,
        'month_future': round(m_fut, 2) if m_fut is not None else None,
        'synthetic_basis_pts': round(basis_pts, 2) if basis_pts is not None else None,
        'basis_pts': round(basis_pts, 2) if basis_pts is not None else None,
        'basis_status': basis_status
    }


def get_futures_carry_payload() -> Dict[str, Any]:
    """Futures basis, roll spread, and annualized carry."""
    from futures_data import load_latest
    from pricing_inputs import get_rate

    rate_info = get_rate()
    rf = number(rate_info.get('yield_percent'))

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
    """Historical broker candles with volume only where the source supplies it."""
    sym = 'banknifty' if 'BANK' in symbol.upper() else 'nifty'
    csv_path = DATA / f"{sym}_one_year.csv"
    if not csv_path.exists():
        return {'symbol': symbol, 'candles': [], 'volumes': []}

    try:
        import pandas as pd
        from volatility_lab.core.vol_lib import validate_ohlc
        raw = pd.read_csv(csv_path)
        df = validate_ohlc(raw).reset_index()
        if 'Volume' in raw:
            volumes_by_date = dict(zip(pd.to_datetime(raw['Date']), raw['Volume']))
            df['Volume'] = df['Date'].map(volumes_by_date)
        candles = []
        volumes = []
        for _, row in df.iterrows():
            d_str = pd.Timestamp(row['Date']).date().isoformat()
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
            volume = number(row.get('Volume'))
            if volume is not None and volume >= 0:
                volumes.append({
                    'time': d_str,
                    'value': volume,
                    'color': 'rgba(16, 185, 129, 0.45)' if c >= o else 'rgba(244, 63, 94, 0.45)'
                })
        return {
            'symbol': symbol,
            'count': len(candles),
            'last_price': candles[-1]['close'] if candles else None,
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
            if settings.get('engine') == 'dated_futures_v1':
                continue
            name = settings.get('name', 'Strategy')
            sym = settings.get('symbol', 'NIFTY')
            validated = raw.get('provenance_schema') == 'captured-minute-v1' and isinstance(trades, list)
            for trade in trades if validated else []:
                try:
                    amounts = [number(trade.get(key)) for key in ('Net', 'Gross', 'Charges', 'Slippage')]
                    legs = json.loads(trade['Legs']) if isinstance(trade.get('Legs'), str) else trade.get('Legs')
                    valid_legs = isinstance(legs, list) and len(legs) > 0 and all(
                        isinstance(leg, dict) and leg.get('key') and leg.get('side') in (-1, 1)
                        and all(number(leg.get(key)) is not None and number(leg[key]) > 0
                                for key in ('entry', 'exit', 'quantity'))
                        for leg in legs
                    )
                    versioned = (trade.get('Execution assumption version') == 'minute-bar-uniform-bps-proxy-v2'
                                 or trade.get('Execution model') == 'v3_prior_minute_required')
                    validated = validated and valid_legs and versioned and all(x is not None for x in amounts)
                    validated = validated and abs(amounts[0] - (amounts[1] - amounts[2] - amounts[3])) < .05
                except (KeyError, ValueError, TypeError):
                    validated = False
                if not validated:
                    break
            if not validated:
                reports.append({
                    'id': p.stem, 'name': name, 'symbol': sym,
                    'start': settings.get('start'), 'end': settings.get('end'),
                    'engine': settings.get('engine', 'legacy'),
                    'integrity_status': 'UNVERIFIED_ARCHIVED_REPORT',
                    'total_trades': len(trades) if isinstance(trades, list) else 0,
                    'wins': None, 'losses': None, 'win_rate_pct': None,
                    'total_net_pnl': None, 'total_gross_pnl': None,
                    'total_charges': None, 'total_slippage': None, 'trades': [],
                })
                continue

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
                'integrity_status': 'CAPTURED_MINUTE_SIMULATION',
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

