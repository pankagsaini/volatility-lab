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
    if not np.isfinite(bid) or not np.isfinite(ask) or bid <= 0 or ask <= bid:
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


def implied_forward(strike: float, call_price: float, put_price: float, years: float = 0.0, rate: float = 0.066) -> float:
    """Return F = K + exp(r * T) * (C - P)."""
    return float(parity_implied_forward(strike, call_price, put_price, max(0.0, float(years)), float(rate)))


def calculate(
    chain: pd.DataFrame | list[dict[str, Any]],
    spot: float | None = None,
    rate: float = 0.066,
    years: float = 7.0 / 365.0,
    future_quote: dict[str, Any] | float | None = None,
    *,
    expiry: str | None = None,
    as_of: Any = None,
    spot_timestamp: Any = None,
    future_contract: dict[str, Any] | None = None,
    max_quote_age_seconds: float = 60.0,
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

    syn_f = implied_forward(atm_k, c_px, p_px, t_years, r_val) if (np.isfinite(c_px) and np.isfinite(p_px)) else np.nan
    b76_c_iv = solve_black76_iv(c_px, syn_f, atm_k, t_years, r_val, 'CE') if np.isfinite(syn_f) else np.nan
    b76_p_iv = solve_black76_iv(p_px, syn_f, atm_k, t_years, r_val, 'PE') if np.isfinite(syn_f) else np.nan
    b76_atm_iv = float(0.5 * (b76_c_iv + b76_p_iv) * 100.0) if (np.isfinite(b76_c_iv) and np.isfinite(b76_p_iv)) else None

    fut_ltp = None
    if isinstance(future_quote, dict) and future_contract:
        contract_expiry = str(future_contract.get('expiry') or '')[:10]
        quote_expiry = str(future_quote.get('expiry') or '')[:10]
        if (contract_expiry and quote_expiry == contract_expiry
                and _same_expiry_month(contract_expiry, expected_expiry)
                and _fresh_quote(future_quote, as_of, max_quote_age_seconds)):
            fut_ltp = _book_mid(future_quote)

    basis = (float(syn_f) - fut_ltp) if np.isfinite(syn_f) and fut_ltp is not None else None
    return {
        'ATM strike': float(atm_k),
        'ATM call LTP': float(c_px) if np.isfinite(c_px) else None,
        'ATM put LTP': float(p_px) if np.isfinite(p_px) else None,
        'Synthetic future': round(float(syn_f), 2) if np.isfinite(syn_f) else None,
        'Month future LTP': round(float(fut_ltp), 2) if fut_ltp is not None else None,
        'Basis': round(float(basis), 2) if basis is not None else None,
        'Black-76 ATM IV': round(b76_atm_iv, 2) if b76_atm_iv is not None else None,
        'Quote method': 'Bid/ask midpoint' if np.isfinite(syn_f) else 'Unavailable',
        'Synthetic status': 'Ready (Black-76 Put-Call Parity F = K + e^{rT}(C − P))' if np.isfinite(syn_f) else 'Unavailable',
        'Pricing model': MODEL_NAME,
    }


def theoretical_forward(spot: float, years: float, rate: float = 0.065, dividend: float = 0.0) -> float:
    """Compute cost-of-carry theoretical forward F = S * exp((r - q) * T)."""
    if spot is None or spot <= 0:
        return float("nan")
    t = max(0.0, float(years or 0.0))
    return float(spot * math.exp((float(rate or 0.0) - float(dividend or 0.0)) * t))


def determine_forward(
    chain_rows: list[dict[str, Any]],
    spot: float | None = None,
    future_quote: dict[str, Any] | None = None,
    rate: float = 0.065,
    dividend: float = 0.0,
    years: float = 7.0 / 365.0,
    r: float | None = None,
    dte_days: float | None = None,
    expiry: str | None = None,
    as_of: Any = None,
    spot_timestamp: Any = None,
    max_quote_age_seconds: float = 60.0,
) -> dict[str, Any]:
    """Determine forward price using 3-tier precedence."""
    if r is not None:
        rate = r
    try:
        spot = float(spot) if spot is not None else None
        t_years = float(dte_days) / 365.0 if dte_days is not None else float(years)
        r_val = float(0.0 if rate is None else rate)
        q_val = float(0.0 if dividend is None else dividend)
    except (TypeError, ValueError):
        return _unavailable_forward()
    if (not np.isfinite(t_years) or t_years < 0.0
            or not np.isfinite(r_val) or not np.isfinite(q_val)
            or (spot is not None and not np.isfinite(spot))):
        return _unavailable_forward()

    observed_expiries = {
        str(item.get('expiry') or item.get('Expiry') or '')[:10]
        for item in chain_rows if item.get('expiry') or item.get('Expiry')
    }
    expected_expiry = str(expiry)[:10] if expiry is not None else (
        next(iter(observed_expiries)) if len(observed_expiries) == 1 else None
    )

    # Tier 1: Accept only a timestamped, two-sided future book from the option-expiry month.
    if future_quote is not None and isinstance(future_quote, dict):
        fut_expiry = str(future_quote.get('expiry') or '')[:10]
        fut_px = _book_mid(future_quote)
        if (fut_px is not None and fut_expiry and expected_expiry
                and _same_expiry_month(fut_expiry, expected_expiry)
                and _fresh_quote(future_quote, as_of, max_quote_age_seconds)):
            fut_val = float(fut_px)
            basis = (fut_val - spot) if (spot is not None and spot > 0) else 0.0
            return {
                "forward": fut_val,
                "source": "listed_future",
                "basis": basis if _fresh_quote({'timestamp': spot_timestamp}, as_of, max_quote_age_seconds) else None,
                "is_synthetic": False,
                "confidence": "VALIDATED_BOOK",
                "expiry": fut_expiry,
            }

    # Tier 2: ATM parity from a fresh, two-sided matched-expiry option pair.
    spot_is_fresh = _fresh_quote({'timestamp': spot_timestamp}, as_of, max_quote_age_seconds)
    if (chain_rows and spot is not None and spot > 0 and expected_expiry and spot_is_fresh
            and observed_expiries == {expected_expiry} and as_of is not None):
        step = 50.0 if spot < 35000 else 100.0
        atm_k = round(spot / step) * step
        atm_node = None
        for item in chain_rows:
            k = item.get("strike_price") or item.get("strike")
            if (k is not None and str(item.get('expiry') or item.get('Expiry') or '')[:10] == expected_expiry
                    and abs(float(k) - atm_k) < 1e-4):
                atm_node = item
                break
        if atm_node is not None:
            c_data = (atm_node.get("call_options") or atm_node.get("call") or {}).get("market_data", {}) or (atm_node.get("call_options") or atm_node.get("call") or {})
            p_data = (atm_node.get("put_options") or atm_node.get("put") or {}).get("market_data", {}) or (atm_node.get("put_options") or atm_node.get("put") or {})
            c_mid = _book_mid(c_data) if _fresh_quote(c_data, as_of, max_quote_age_seconds) else None
            p_mid = _book_mid(p_data) if _fresh_quote(p_data, as_of, max_quote_age_seconds) else None
            if c_mid is not None and p_mid is not None:
                fwd_val = implied_forward(atm_k, c_mid, p_mid, t_years, r_val)
                basis = fwd_val - spot
                return {
                    "forward": fwd_val,
                    "source": "synthetic_forward",
                    "basis": basis,
                    "is_synthetic": True,
                    "atm_strike": atm_k,
                    "confidence": "VALIDATED_OPTION_BOOKS",
                    "expiry": expected_expiry,
                }

    # Tier 3: Cost-of-carry theoretical forward
    spot_is_fresh = _fresh_quote({'timestamp': spot_timestamp}, as_of, max_quote_age_seconds)
    fwd_theo = theoretical_forward(spot, t_years, r_val, q_val) if (spot is not None and spot > 0 and spot_is_fresh) else float("nan")
    basis = (fwd_theo - spot) if np.isfinite(fwd_theo) else None
    return {
        "forward": fwd_theo if np.isfinite(fwd_theo) else None,
        "source": "cost_of_carry_forward" if np.isfinite(fwd_theo) else "unavailable",
        "basis": basis,
        "is_synthetic": False,
        "confidence": "THEORETICAL" if np.isfinite(fwd_theo) else "UNAVAILABLE",
    }
