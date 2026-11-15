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
try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')
import option_math

_INV_SQRT_2PI = 0.3989422804014327


def _clean_float(val, fallback=0.0):
    try:
        f = float(val)
        return f if np.isfinite(f) else fallback
    except (TypeError, ValueError):
        return fallback






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
