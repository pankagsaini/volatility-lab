"""Put-Call Parity Synthetic Future & Forward Basis Engine (Fischer Black 1976 Edition).

Computes the discounted Put-Call Parity Implied Forward:
    F = K_ATM + exp(r * T) * (C(K_ATM) - P(K_ATM))
and evaluates forward basis against spot and listed monthly futures.
"""
from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd
from option_math import (
    black76_greeks,
    parity_implied_forward,
    solve_black76_iv,
)

MODEL_NAME = 'Black-76 Forward Model · F = K + e^{rT}(C − P)'


def _timestamp_utc(value):
    """Parse ISO or explicit epoch timestamps without pandas' nanosecond guess."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            numeric = float(value)
            if not np.isfinite(numeric):
                return None
            stamp = pd.to_datetime(numeric, unit='ms' if abs(numeric) > 1e11 else 's', utc=True)
        else:
            stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        return stamp.tz_convert('UTC')
    except (TypeError, ValueError, OverflowError):
        return None


def _fresh_quote(value, as_of, max_age_seconds=60.0) -> bool:
    stamp_value = (value.get('timestamp') or value.get('quote_timestamp')) if hasattr(value, 'get') else None
    if not stamp_value or as_of is None:
        return False
    stamp = _timestamp_utc(stamp_value)
    reference = _timestamp_utc(as_of)
    if stamp is None or reference is None:
        return False
    try:
        age = (reference - stamp).total_seconds()
        return -5.0 <= age <= float(max_age_seconds)
    except (TypeError, ValueError, OverflowError):
        return False


def _book_mid(value):
    if not hasattr(value, 'get'):
        return None
    depth = value.get('depth') or {}
    bid_rows = depth.get('buy') or [] if isinstance(depth, dict) else []
    ask_rows = depth.get('sell') or [] if isinstance(depth, dict) else []
    bid_row = bid_rows[0] if bid_rows and isinstance(bid_rows[0], dict) else {}
    ask_row = ask_rows[0] if ask_rows and isinstance(ask_rows[0], dict) else {}
    bid = bid_row.get('price', value.get('bid', value.get('bid_price', value.get('Bid'))))
    ask = ask_row.get('price', value.get('ask', value.get('ask_price', value.get('Ask'))))
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(bid) or not np.isfinite(ask) or bid <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _same_expiry_month(first, second):
    try:
        a, b = pd.Timestamp(first), pd.Timestamp(second)
        return (a.year, a.month) == (b.year, b.month)
    except (TypeError, ValueError):
        return False


def _unavailable_forward():
    return {
        'forward': None, 'source': 'unavailable', 'basis': None,
        'is_synthetic': False, 'confidence': 'UNAVAILABLE',
    }


def implied_forward(strike: float, call_price: float, put_price: float, years: float = 0.0, rate: float | None = None) -> float:
    """Return F = K + exp(r * T) * (C - P)."""
    return float(parity_implied_forward(strike, call_price, put_price, max(0.0, float(years)), float(rate) if rate is not None else float('nan')))


def calculate(
    chain: pd.DataFrame | list[dict[str, Any]],
    spot: float | None = None,
    rate: float | None = None,
    years: float = 7.0 / 365.0,
    future_quote: dict[str, Any] | float | None = None,
    *,
    expiry: str | None = None,
    as_of: Any = None,
    spot_timestamp: Any = None,
    future_contract: dict[str, Any] | None = None,
    max_quote_age_seconds: float = 60.0,
    dividend: float | None = None,
) -> dict[str, Any]:
    """Use fresh, exact-expiry two-sided quote books; LTP-only data is never used."""
    if isinstance(chain, list):
        df = pd.DataFrame(chain)
    elif isinstance(chain, pd.DataFrame):
        df = chain.copy()
    else:
        df = pd.DataFrame()

    try:
        spot_f = float(spot)
    except (TypeError, ValueError):
        spot_f = np.nan
    if df.empty or not np.isfinite(spot_f) or spot_f <= 0:
        return {
            'ATM strike': None,
            'ATM call LTP': None,
            'ATM put LTP': None,
            'Synthetic future': None,
            'Month future LTP': None,
            'Basis': None,
            'Black-76 ATM IV': None,
            'Synthetic status': 'Synthetic future unavailable',
            'Pricing model': MODEL_NAME,
        }

    if 'expiry' not in df.columns:
        return {
            'ATM strike': None, 'ATM call LTP': None, 'ATM put LTP': None,
            'Synthetic future': None, 'Month future LTP': None, 'Basis': None,
            'Black-76 ATM IV': None, 'Synthetic status': 'Option expiry metadata unavailable',
            'Pricing model': MODEL_NAME,
        }
    observed_expiries = set(df['expiry'].dropna().astype(str).str[:10])
    expected_expiry = str(expiry)[:10] if expiry is not None else (
        next(iter(observed_expiries)) if len(observed_expiries) == 1 else None
    )
    if not expected_expiry or observed_expiries != {expected_expiry}:
        return {
            'ATM strike': None, 'ATM call LTP': None, 'ATM put LTP': None,
            'Synthetic future': None, 'Month future LTP': None, 'Basis': None,
            'Black-76 ATM IV': None, 'Synthetic status': 'Option expiry mismatch or unavailable',
            'Pricing model': MODEL_NAME,
        }
    if as_of is None:
        return {
            'ATM strike': None, 'ATM call LTP': None, 'ATM put LTP': None,
            'Synthetic future': None, 'Month future LTP': None, 'Basis': None,
            'Black-76 ATM IV': None, 'Synthetic status': 'Quote valuation timestamp unavailable',
            'Pricing model': MODEL_NAME,
        }
    if not _fresh_quote({'timestamp': spot_timestamp}, as_of, max_quote_age_seconds):
        return {
            'ATM strike': None, 'ATM call LTP': None, 'ATM put LTP': None,
            'Synthetic future': None, 'Month future LTP': None, 'Basis': None,
            'Black-76 ATM IV': None, 'Synthetic status': 'Spot quote is stale or unvalidated',
            'Pricing model': MODEL_NAME,
        }
    strike_col = 'strike' if 'strike' in df.columns else ('Strike' if 'Strike' in df.columns else 'strike_price')
    type_col = 'option_type' if 'option_type' in df.columns else ('Type' if 'Type' in df.columns else 'Kind')
    if strike_col not in df.columns or type_col not in df.columns:
        return {
            'ATM strike': None,
            'ATM call LTP': None,
            'ATM put LTP': None,
            'Synthetic future': None,
            'Month future LTP': None,
            'Basis': None,
            'Black-76 ATM IV': None,
            'Synthetic status': 'Normalized option quotes required',
            'Pricing model': MODEL_NAME,
        }

    df[strike_col] = pd.to_numeric(df[strike_col], errors='coerce')
    df = df[np.isfinite(df[strike_col]) & (df[strike_col] > 0)]
    if df.empty:
        return {
            'ATM strike': None,
            'ATM call LTP': None,
            'ATM put LTP': None,
            'Synthetic future': None,
            'Month future LTP': None,
            'Basis': None,
            'Black-76 ATM IV': None,
            'Synthetic status': 'Synthetic future unavailable',
            'Pricing model': MODEL_NAME,
        }

    pairs = df.groupby(strike_col)[type_col].agg(set)
    paired = [float(k) for k, kinds in pairs.items() if {'CE', 'PE'}.issubset({str(x).upper() for x in kinds})]
    if not paired:
        return {
            'ATM strike': None,
            'ATM call LTP': None,
            'ATM put LTP': None,
            'Synthetic future': None,
            'Month future LTP': None,
            'Basis': None,
            'Black-76 ATM IV': None,
            'Synthetic status': 'No paired ATM call/put quotes',
            'Pricing model': MODEL_NAME,
        }

    atm_k = min(paired, key=lambda k: (abs(k - spot_f), k))
    ce_row = df[(df[strike_col] == atm_k) & (df[type_col].astype(str).str.upper() == 'CE')].iloc[0]
    pe_row = df[(df[strike_col] == atm_k) & (df[type_col].astype(str).str.upper() == 'PE')].iloc[0]

    def _px(row):
        if not _fresh_quote(row, as_of, max_quote_age_seconds):
            return np.nan
        return _book_mid(row) or np.nan

    c_px = _px(ce_row)
    p_px = _px(pe_row)
    try:
        t_years = float(years)
        r_val = float(rate)
    except (TypeError, ValueError):
        t_years = r_val = np.nan
    if not np.isfinite(t_years) or t_years <= 0 or not np.isfinite(r_val):
        return {
            'ATM strike': float(atm_k), 'ATM call LTP': None, 'ATM put LTP': None,
            'Synthetic future': None, 'Month future LTP': None, 'Basis': None,
            'Black-76 ATM IV': None, 'Synthetic status': 'Invalid rate or time to expiry',
            'Pricing model': MODEL_NAME,
        }

    from pricing_policy import select_model, price_observed_option
    if not isinstance(future_quote, dict):
        future_quote = None
    if future_contract and str(future_contract.get('expiry'))[:10] != str((future_quote or {}).get('expiry'))[:10]:
        future_quote = None
    selected = select_model(
        [{'expiry': expected_expiry, 'strike_price': atm_k,
          'call_options': {'market_data': ce_row.to_dict()}, 'put_options': {'market_data': pe_row.to_dict()}}],
        spot=spot_f, expiry=expected_expiry, as_of=as_of, spot_timestamp=spot_timestamp,
        years=t_years, rate=r_val, dividend=dividend, future_quote=future_quote,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    c_iv = price_observed_option(c_px, atm_k, t_years, 'CE', selected, spot=spot_f)['iv']
    p_iv = price_observed_option(p_px, atm_k, t_years, 'PE', selected, spot=spot_f)['iv']
    model_iv = (c_iv+p_iv)*50 if c_iv is not None and p_iv is not None else None
    syn_f = selected['synthetic_forward']
    fut_ltp = selected['forward'] if selected['source'] == 'listed_future' else None
    basis = syn_f-fut_ltp if syn_f is not None and fut_ltp is not None else None
    return {
        'ATM strike': float(atm_k),
        'ATM call LTP': float(c_px) if np.isfinite(c_px) else None,
        'ATM put LTP': float(p_px) if np.isfinite(p_px) else None,
        'Synthetic future': round(syn_f, 2) if syn_f is not None else None,
        'Month future LTP': round(fut_ltp, 2) if fut_ltp is not None else None,
        'Basis': round(basis, 2) if basis is not None else None,
        'Black-76 ATM IV': round(model_iv, 2) if model_iv is not None and selected['model'] == 'black76' else None,
        'Model ATM IV': round(model_iv, 2) if model_iv is not None else None,
        'Quote method': 'Bid/ask midpoint' if model_iv is not None else 'Unavailable',
        'Synthetic status': selected['reason'],
        'Pricing model': {'black76':'Black-76', 'bsm':'BSM'}.get(selected['model'], 'Unavailable'),
    }


def theoretical_forward(spot: float, years: float, rate: float | None = None, dividend: float | None = None) -> float:
    """Compute cost-of-carry theoretical forward F = S * exp((r - q) * T)."""
    if spot is None or spot <= 0 or rate is None or dividend is None:
        return float("nan")
    t = max(0.0, float(years or 0.0))
    return float(spot * math.exp((float(rate or 0.0) - float(dividend or 0.0)) * t))


def determine_forward(
    chain_rows: list[dict[str, Any]],
    spot: float | None = None,
    future_quote: dict[str, Any] | None = None,
    rate: float | None = None,
    dividend: float | None = None,
    years: float = 7.0 / 365.0,
    r: float | None = None,
    dte_days: float | None = None,
    expiry: str | None = None,
    as_of: Any = None,
    spot_timestamp: Any = None,
    max_quote_age_seconds: float = 60.0,
) -> dict[str, Any]:
    """Select a matched future, tight same-expiry parity, or explicit BSM carry."""
    from pricing_policy import select_model
    return select_model(
        chain_rows, spot=spot, expiry=expiry, as_of=as_of, spot_timestamp=spot_timestamp,
        years=float(dte_days)/365 if dte_days is not None else years,
        rate=r if r is not None else rate, dividend=dividend, future_quote=future_quote,
        max_quote_age_seconds=max_quote_age_seconds,
    )
