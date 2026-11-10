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


def implied_forward(strike: float, call_price: float, put_price: float, years: float = 0.0, rate: float = 0.066) -> float:
    """Return F = K + exp(r * T) * (C - P)."""
    return float(parity_implied_forward(strike, call_price, put_price, max(0.0, float(years)), float(rate)))


def calculate(
    chain: pd.DataFrame | list[dict[str, Any]],
    spot: float | None = None,
    rate: float = 0.066,
    years: float = 7.0 / 365.0,
    future_quote: dict[str, Any] | float | None = None,
) -> dict[str, Any]:
    """Compute ATM Synthetic Future F = K_ATM + e^{rT}(C - P), Black-76 ATM IV, and basis diagnostics."""
    if isinstance(chain, list):
        df = pd.DataFrame(chain)
    elif isinstance(chain, pd.DataFrame):
        df = chain.copy()
    else:
        df = pd.DataFrame()

    if df.empty or spot is None or not np.isfinite(float(spot)) or float(spot) <= 0:
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

    spot_f = float(spot)
    strike_col = 'strike' if 'strike' in df.columns else ('Strike' if 'Strike' in df.columns else 'strike_price')
    type_col = 'option_type' if 'option_type' in df.columns else ('Type' if 'Type' in df.columns else 'Kind')

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
        for col in ('Mid', 'mid', 'ltp', 'LTP', 'price', 'Close'):
            if col in row and row[col] is not None and np.isfinite(float(row[col])) and float(row[col]) > 0:
                return float(row[col])
        bid = float(row.get('bid', row.get('Bid', 0.0)) or 0.0)
        ask = float(row.get('ask', row.get('Ask', 0.0)) or 0.0)
        if bid > 0 and ask > 0:
            return 0.5 * (bid + ask)
        return np.nan

    c_px = _px(ce_row)
    p_px = _px(pe_row)
    t_years = max(1e-6, float(years or (7.0 / 365.0)))
    r_val = float(rate if rate is not None and np.isfinite(float(rate)) else 0.066)

    syn_f = implied_forward(atm_k, c_px, p_px, t_years, r_val) if (np.isfinite(c_px) and np.isfinite(p_px)) else np.nan
    b76_c_iv = solve_black76_iv(c_px, syn_f, atm_k, t_years, r_val, 'CE') if np.isfinite(syn_f) else np.nan
    b76_p_iv = solve_black76_iv(p_px, syn_f, atm_k, t_years, r_val, 'PE') if np.isfinite(syn_f) else np.nan
    b76_atm_iv = float(0.5 * (b76_c_iv + b76_p_iv) * 100.0) if (np.isfinite(b76_c_iv) and np.isfinite(b76_p_iv)) else None

    fut_ltp = None
    if isinstance(future_quote, (int, float)) and np.isfinite(float(future_quote)) and float(future_quote) > 0:
        fut_ltp = float(future_quote)
    elif isinstance(future_quote, dict):
        for k in ('Month future LTP', 'last_price', 'ltp', 'price', 'close'):
            v = future_quote.get(k)
            if v is not None and np.isfinite(float(v)) and float(v) > 0:
                fut_ltp = float(v)
                break

    basis = (float(syn_f) - (fut_ltp if fut_ltp is not None else spot_f)) if np.isfinite(syn_f) else None
    return {
        'ATM strike': float(atm_k),
        'ATM call LTP': float(c_px) if np.isfinite(c_px) else None,
        'ATM put LTP': float(p_px) if np.isfinite(p_px) else None,
        'Synthetic future': round(float(syn_f), 2) if np.isfinite(syn_f) else None,
        'Month future LTP': round(float(fut_ltp), 2) if fut_ltp is not None else None,
        'Basis': round(float(basis), 2) if basis is not None else None,
        'Black-76 ATM IV': round(b76_atm_iv, 2) if b76_atm_iv is not None else None,
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
) -> dict[str, Any]:
    """Determine forward price using 3-tier precedence."""
    if r is not None:
        rate = r
    if dte_days is not None:
        years = float(dte_days) / 365.0
    t_years = max(0.0, float(years or 0.0))
    r_val = float(rate or 0.0)
    q_val = float(dividend or 0.0)

    # Tier 1: Fresh listed future
    if future_quote is not None and isinstance(future_quote, dict):
        fut_px = future_quote.get("last_price") or future_quote.get("ltp") or future_quote.get("price")
        if fut_px is not None and float(fut_px) > 0:
            fut_val = float(fut_px)
            basis = (fut_val - spot) if (spot is not None and spot > 0) else 0.0
            return {
                "forward": fut_val,
                "source": "listed_future",
                "basis": basis,
                "is_synthetic": False,
                "confidence": "HIGH",
            }

    # Tier 2: ATM Put-Call Parity Synthetic Forward
    if chain_rows and spot is not None and spot > 0:
        step = 50.0 if spot < 35000 else 100.0
        atm_k = round(spot / step) * step
        atm_node = None
        for item in chain_rows:
            k = item.get("strike_price") or item.get("strike")
            if k is not None and abs(float(k) - atm_k) < 1e-4:
                atm_node = item
                break
        if atm_node is not None:
            c_data = (atm_node.get("call_options") or atm_node.get("call") or {}).get("market_data", {}) or (atm_node.get("call_options") or atm_node.get("call") or {})
            p_data = (atm_node.get("put_options") or atm_node.get("put") or {}).get("market_data", {}) or (atm_node.get("put_options") or atm_node.get("put") or {})
            c_ltp = float(c_data.get("ltp") or c_data.get("last_price") or 0.0)
            p_ltp = float(p_data.get("ltp") or p_data.get("last_price") or 0.0)
            if c_ltp > 0 and p_ltp > 0:
                fwd_val = implied_forward(atm_k, c_ltp, p_ltp, t_years, r_val)
                basis = fwd_val - spot
                return {
                    "forward": fwd_val,
                    "source": "synthetic_forward",
                    "basis": basis,
                    "is_synthetic": True,
                    "atm_strike": atm_k,
                    "confidence": "MEDIUM",
                }

    # Tier 3: Cost-of-carry theoretical forward
    fwd_theo = theoretical_forward(spot, t_years, r_val, q_val) if (spot is not None and spot > 0) else float("nan")
    basis = (fwd_theo - spot) if (spot is not None and spot > 0) else 0.0
    return {
        "forward": fwd_theo,
        "source": "cost_of_carry_forward",
        "basis": basis,
        "is_synthetic": False,
        "confidence": "THEORETICAL",
    }
