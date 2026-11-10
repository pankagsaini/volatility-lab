"""Institutional Options Pricing Engine: BSM (Primary Spot) & Black-76 (Forward / Futures).

Models:
1. BSM (Default Primary Engine: DEFAULT_PRICING_MODEL = "bsm"):
   Spot Black-Scholes-Merton with continuous dividend yield (q) for cash index and ETF research.
   Maintains 100% backward compatibility for all existing screens and calculations.

2. Black-76 (Second Pricing Model: model = "black76" / "sensibull"):
   Forward/Futures-anchored European options model.
   Inputs: Forward (F), strike (K), time to expiry (T in years), risk-free rate (r), vol (sigma).
   For T <= 0, returns exact intrinsic value (max(F-K, 0) for call, max(K-F, 0) for put).
   When F = S * exp((r - q) * T), Black-76 mathematically equals Spot BSM down to machine epsilon.
"""
import math
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import ndtr

_INV_SQRT_2PI = 0.3989422804014327


def _norm_pdf(x):
    return _INV_SQRT_2PI * np.exp(-0.5 * np.square(x))


# ==============================================================================
# 1. SPOT BLACK-SCHOLES-MERTON (BSM) ENGINE (PRIMARY DEFAULT)
# ==============================================================================

DEFAULT_PRICING_MODEL = "bsm"


def bsm_greeks(spot, strike, years, rate, vol, kind, dividend=0.):
    """Vectorized or scalar BSM price, spot delta, forward delta, gamma, vega (per 1% vol), and daily theta."""
    if kind not in ('CE', 'PE'):
        raise ValueError('Invalid option inputs.')
    spot_a, strike_a, years_a, vol_a = map(np.asarray, (spot, strike, years, vol))
    if np.any(spot_a <= 0) or np.any(strike_a <= 0) or np.any(years_a <= 0) or np.any(vol_a <= 0):
        raise ValueError('Invalid option inputs.')
    sqrt_t = np.sqrt(years_a)
    vol_sqrt_t = vol_a * sqrt_t
    d1 = (np.log(spot_a / strike_a) + (rate - dividend + 0.5 * np.square(vol_a)) * years_a) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    df_q = np.exp(-dividend * years_a)
    df_r = np.exp(-rate * years_a)
    pdf_d1 = _norm_pdf(d1)
    if kind == 'CE':
        nd1, nd2 = ndtr(d1), ndtr(d2)
        price = spot_a * df_q * nd1 - strike_a * df_r * nd2
        delta = df_q * nd1
        fwd_delta = nd1
        theta_ann = (-spot_a * df_q * pdf_d1 * vol_a / (2.0 * sqrt_t)
                     - rate * strike_a * df_r * nd2 + dividend * spot_a * df_q * nd1)
    else:
        nd1_neg, nd2_neg = ndtr(-d1), ndtr(-d2)
        price = strike_a * df_r * nd2_neg - spot_a * df_q * nd1_neg
        delta = -df_q * nd1_neg
        fwd_delta = -nd1_neg
        theta_ann = (-spot_a * df_q * pdf_d1 * vol_a / (2.0 * sqrt_t)
                     + rate * strike_a * df_r * nd2_neg - dividend * spot_a * df_q * nd1_neg)
    gamma = (df_q * pdf_d1) / (spot_a * vol_sqrt_t)
    vega = spot_a * df_q * pdf_d1 * sqrt_t * 0.01
    return {
        'price': price,
        'delta': delta,
        'fwd_delta': fwd_delta,
        'gamma': gamma,
        'vega': vega,
        'theta_daily': theta_ann / 365.0,
    }


def price_delta(spot, strike, years, rate, vol, kind, dividend=0.):
    res = bsm_greeks(spot, strike, years, rate, vol, kind, dividend)
    return float(res['price']), float(res['delta'])


def solve_iv(price, spot, strike, years, rate, kind, dividend=0., bounds=(1e-4, 5.0), tol=1e-8):
    """Arbitrage-bounded Brentq implied volatility solver for Spot BSM."""
    if kind not in ('CE', 'PE') or min(price, spot, strike, years) <= 0:
        return np.nan
    df_q = np.exp(-dividend * years)
    df_r = np.exp(-rate * years)
    intrinsic = max(0.0, spot * df_q - strike * df_r if kind == 'CE' else strike * df_r - spot * df_q)
    upper = spot * df_q if kind == 'CE' else strike * df_r
    if not (intrinsic < price < upper):
        return np.nan
    try:
        return float(brentq(
            lambda sigma: price_delta(spot, strike, years, rate, sigma, kind, dividend)[0] - price,
            bounds[0], bounds[1], xtol=tol, rtol=tol, maxiter=100
        ))
    except (ValueError, RuntimeError):
        return np.nan


implied_vol = solve_iv


def delta_iv_details(chain, kind, target):
    """Single-pass delta interpolation returning (iv, method, contributors)."""
    side = chain[chain.option_type == kind].copy()
    for column in ('iv', 'delta'):
        side[column] = pd.to_numeric(side[column], errors='coerce').astype(float)
    side = side[np.isfinite(side.iv) & np.isfinite(side.delta) & (side.iv > 0)]
    side = side[side.delta.between(0, 1) if kind == 'CE' else side.delta.between(-1, 0)]
    if side.empty:
        return np.nan, 'Unavailable', chain.iloc[:0]
    side['abs_delta'] = side.delta.abs()
    grouped = side.dropna(subset=['abs_delta', 'iv']).groupby('abs_delta', as_index=False).iv.mean().sort_values('abs_delta')
    exact = grouped[np.isclose(grouped.abs_delta, target, rtol=0, atol=1e-12)]
    if len(exact):
        val = float(exact.iv.iloc[0])
        contrib = side[np.isclose(side.abs_delta, exact.abs_delta.iloc[0], rtol=0, atol=1e-12)]
        return val, 'Exact delta', contrib
    if len(grouped) < 2 or not (grouped.abs_delta.min() <= target <= grouped.abs_delta.max()):
        return np.nan, 'Unavailable', chain.iloc[:0]
    val = float(np.interp(target, grouped.abs_delta, grouped.iv))
    lower = side.loc[side.abs_delta <= target, 'abs_delta'].max()
    upper = side.loc[side.abs_delta >= target, 'abs_delta'].min()
    contrib = side[side.abs_delta.isin([lower, upper])]
    return val, ('Exact delta' if lower == upper else 'Linear IV interpolation in absolute delta'), contrib


def delta_iv(chain, kind, target):
    return delta_iv_details(chain, kind, target)[0]


def skew_metrics(chain, spot=None):
    put = delta_iv(chain, 'PE', .25)
    call = delta_iv(chain, 'CE', .25)
    proxy = (delta_iv(chain, 'PE', .5) + delta_iv(chain, 'CE', .5)) / 2
    if spot is not None and 'strike' in chain.columns:
        valid = chain.copy()
        valid['iv'] = pd.to_numeric(valid.iv, errors='coerce').astype(float)
        valid['strike'] = pd.to_numeric(valid.strike, errors='coerce').astype(float)
        valid = valid[np.isfinite(valid.iv) & (valid.iv > 0) & valid.option_type.isin(['CE', 'PE'])]
        pairs = valid.groupby('strike').option_type.agg(set)
        paired = [float(k) for k, kinds in pairs.items() if kinds == {'CE', 'PE'}]
        if paired:
            atm_k = min(paired, key=lambda k: (abs(k - spot), k))
            proxy = float(valid[valid.strike == atm_k].iv.mean())
    return {
        '50-delta IV proxy %': proxy * 100,
        '25D put IV %': put * 100,
        '25D call IV %': call * 100,
        'Put-minus-call RR (pp)': (put - call) * 100,
        'Butterfly (pp)': ((put + call) / 2 - proxy) * 100,
    }


def reversal_values(chain, symbol=None):
    """Calculate put-minus-call reversal from accepted 25-delta IVs."""
    chain = chain.copy()
    for column in ('iv', 'delta', 'strike'):
        if column in chain.columns:
            chain[column] = pd.to_numeric(chain[column], errors='coerce').astype(float)
            chain = chain[np.isfinite(chain[column])]
    chain = chain[chain.iv > 0]
    put = delta_iv(chain, 'PE', .25)
    call = delta_iv(chain, 'CE', .25)
    complete = bool(np.isfinite(put) and np.isfinite(call))
    status = '25-delta interpolation' if complete else 'Unavailable: accepted quotes do not cover 25D'
    return {
        '25 Delta reversal': float(100 * (put - call)) if complete else np.nan,
        'Reversal method': status,
        'Reversal put delta': 25.0 if complete else np.nan,
        'Reversal call delta': 25.0 if complete else np.nan,
    }


def bsm_greeks_vec(spot, strike, years, vol=None, rate=0.0, dividend=0.0, kind='CE', iv=None, div_yield=None):
    """Vectorized BSM Greeks supporting array ('CE'/'PE') and safe clamping at T <= 0."""
    sigma = np.maximum(np.asarray(iv if vol is None else vol, dtype=float), 1e-6)
    q = float(div_yield if div_yield is not None else dividend)
    r = float(rate)
    S = np.maximum(np.asarray(spot, dtype=float), 1e-6)
    K = np.maximum(np.asarray(strike, dtype=float), 1e-6)
    T = np.maximum(np.asarray(years, dtype=float), 1e-9)
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * np.square(sigma)) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    df_r = np.exp(-r * T)
    df_q = np.exp(-q * T)
    pdf_d1 = _norm_pdf(d1)
    is_call = np.isin(np.char.upper(np.asarray(kind, dtype=str)), ['CE', 'CALL', 'C'])
    price = np.where(is_call, S * df_q * ndtr(d1) - K * df_r * ndtr(d2), K * df_r * ndtr(-d2) - S * df_q * ndtr(-d1))
    delta = np.where(is_call, df_q * ndtr(d1), -df_q * ndtr(-d1))
    gamma = (df_q * pdf_d1) / (S * sigma * sqrt_T)
    vega = S * df_q * pdf_d1 * sqrt_T / 100.0
    theta_c = (-(S * df_q * pdf_d1 * sigma) / (2.0 * sqrt_T) - r * K * df_r * ndtr(d2) + q * S * df_q * ndtr(d1)) / 365.0
    theta_p = (-(S * df_q * pdf_d1 * sigma) / (2.0 * sqrt_T) + r * K * df_r * ndtr(-d2) - q * S * df_q * ndtr(-d1)) / 365.0
    theta = np.where(is_call, theta_c, theta_p)
    return {'price': price, 'delta': delta, 'gamma': gamma, 'vega': vega, 'theta': theta}


# ==============================================================================
# 2. BLACK-76 / SENSIBULL FORWARD-ANCHORED ENGINE (SECOND PRICING MODEL)
# ==============================================================================

def sensibull_dte_years(expiry_date, as_of=None) -> float:
    """Calculate precise Sensibull ACT/365 time to expiry down to 15:30:00 IST."""
    from datetime import datetime, date, time
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    if as_of is None:
        as_of = datetime.now(ist)
    elif isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of)
    if getattr(as_of, "tzinfo", None) is None:
        as_of = as_of.replace(tzinfo=ist)
    else:
        as_of = as_of.astimezone(ist)

    if isinstance(expiry_date, str):
        exp_d = date.fromisoformat(expiry_date)
    elif isinstance(expiry_date, datetime):
        exp_d = expiry_date.date()
    else:
        exp_d = expiry_date

    cutoff = datetime.combine(exp_d, time(15, 30, 0), tzinfo=ist)
    diff_sec = (cutoff - as_of).total_seconds()
    if diff_sec <= 0:
        return 0.0
    return diff_sec / (365.0 * 86400.0)


def black76_greeks(forward, strike, years, rate, vol, kind, discount_factor=None):
    """Black-76 option pricing and Greeks for forward or futures-anchored options.
    
    Inputs:
        forward (F): Forward or futures price (strictly positive)
        strike (K): Option strike price (strictly positive)
        years (T): Time to expiry in years (ACT/365). For T <= 0, returns exact intrinsic value.
        rate (r): Risk-free rate (decimal, e.g. 0.0585 for 5.85%)
        vol (sigma): Implied volatility as decimal (e.g. 0.165 for 16.5%)
        kind: 'CE' (Call) or 'PE' (Put)
        discount_factor (D): Optional override for exp(-r * T)
    
    Units:
        vol: decimal
        time: years (ACT/365)
        vega: per 1 percentage point vol change (0.01 vol point)
        vega_100: per 1.00 vol change (100% vol point)
        theta_daily: per calendar day (annual theta / 365.0)
    """
    if kind not in ("CE", "PE"):
        raise ValueError(f"Invalid option kind: {kind}. Expected 'CE' or 'PE'.")
    
    try:
        fwd_a = float(forward)
        strike_a = float(strike)
        years_a = float(years)
        vol_a = float(vol)
        r = float(rate)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Invalid numeric option inputs: {err}")

    if fwd_a <= 0 or strike_a <= 0 or vol_a <= 0:
        raise ValueError("Invalid option inputs: forward, strike, and vol must be strictly positive.")

    df_r = float(discount_factor) if discount_factor is not None else math.exp(-r * max(0.0, years_a))

    # T <= 0 Expiry boundary: return exact intrinsic value
    if years_a <= 0.0:
        if kind == "CE":
            intrinsic = max(0.0, fwd_a - strike_a)
            delta = 1.0 if fwd_a > strike_a else (0.5 if fwd_a == strike_a else 0.0)
        else:
            intrinsic = max(0.0, strike_a - fwd_a)
            delta = -1.0 if fwd_a < strike_a else (-0.5 if fwd_a == strike_a else 0.0)
        return {
            "price": intrinsic,
            "delta": delta,
            "fwd_delta": delta,
            "gamma": 0.0,
            "vega": 0.0,
            "vega_100": 0.0,
            "theta_daily": 0.0,
            "theta_ann": 0.0,
        }

    sqrt_t = math.sqrt(years_a)
    vol_sqrt_t = vol_a * sqrt_t
    d1 = (math.log(fwd_a / strike_a) + 0.5 * (vol_a ** 2) * years_a) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    pdf_d1 = _norm_pdf(d1)

    if kind == "CE":
        nd1 = float(ndtr(d1))
        nd2 = float(ndtr(d2))
        price = df_r * (fwd_a * nd1 - strike_a * nd2)
        delta = df_r * nd1
        fwd_delta = nd1
        theta_ann = -(fwd_a * df_r * pdf_d1 * vol_a) / (2.0 * sqrt_t) - r * price
    else:
        nd1_neg = float(ndtr(-d1))
        nd2_neg = float(ndtr(-d2))
        price = df_r * (strike_a * nd2_neg - fwd_a * nd1_neg)
        delta = -df_r * nd1_neg
        fwd_delta = -nd1_neg
        theta_ann = -(fwd_a * df_r * pdf_d1 * vol_a) / (2.0 * sqrt_t) - r * price

    gamma = (df_r * pdf_d1) / (fwd_a * vol_sqrt_t)
    vega_100 = fwd_a * df_r * pdf_d1 * sqrt_t
    vega = vega_100 * 0.01  # Per 1 percentage point vol change

    return {
        "price": price,
        "delta": delta,
        "fwd_delta": fwd_delta,
        "gamma": gamma,
        "vega": vega,
        "vega_100": vega_100,
        "theta_daily": theta_ann / 365.0,
        "theta_ann": theta_ann,
    }


def black76_greeks_vec(forward, strike, years, vol=None, rate=0.0, kind="CE", iv=None, discount_factor=None):
    """Vectorized Black-76 Greeks supporting arrays and mixed call/put with T <= 0 intrinsic value handling."""
    sigma = np.maximum(np.asarray(iv if vol is None else vol, dtype=float), 1e-6)
    r = float(rate)
    F = np.maximum(np.asarray(forward, dtype=float), 1e-6)
    K = np.maximum(np.asarray(strike, dtype=float), 1e-6)
    T = np.asarray(years, dtype=float)
    T_safe = np.maximum(T, 1e-9)
    sqrt_T = np.sqrt(T_safe)
    vol_sqrt_T = sigma * sqrt_T
    d1 = (np.log(F / K) + 0.5 * np.square(sigma) * T_safe) / vol_sqrt_T
    d2 = d1 - vol_sqrt_T
    df_r = np.asarray(discount_factor, dtype=float) if discount_factor is not None else np.exp(-r * np.maximum(0.0, T))
    pdf_d1 = _norm_pdf(d1)
    is_call = np.isin(np.char.upper(np.asarray(kind, dtype=str)), ["CE", "CALL", "C"])

    # Live option math
    price_live = np.where(is_call, df_r * (F * ndtr(d1) - K * ndtr(d2)), df_r * (K * ndtr(-d2) - F * ndtr(-d1)))
    delta_live = np.where(is_call, df_r * ndtr(d1), -df_r * ndtr(-d1))
    gamma_live = (df_r * pdf_d1) / (F * vol_sqrt_T)
    vega_live = F * df_r * pdf_d1 * sqrt_T / 100.0
    theta_ann_live = -(F * df_r * pdf_d1 * sigma) / (2.0 * sqrt_T) - r * price_live
    theta_live = theta_ann_live / 365.0

    # T <= 0 boundary
    call_intr = np.maximum(0.0, F - K)
    put_intr = np.maximum(0.0, K - F)
    price_exp = np.where(is_call, call_intr, put_intr)
    delta_exp = np.where(is_call, np.where(F > K, 1.0, np.where(F == K, 0.5, 0.0)),
                                  np.where(F < K, -1.0, np.where(F == K, -0.5, 0.0)))

    is_expired = T <= 0.0
    price = np.where(is_expired, price_exp, price_live)
    delta = np.where(is_expired, delta_exp, delta_live)
    gamma = np.where(is_expired, 0.0, gamma_live)
    vega = np.where(is_expired, 0.0, vega_live)
    theta = np.where(is_expired, 0.0, theta_live)

    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def solve_black76_iv(price, forward, strike, years, rate, kind, discount_factor=None, bounds=(1e-4, 5.0), tol=1e-8):
    """Arbitrage-bounded Brentq solver for Black-76 implied volatility."""
    if kind not in ("CE", "PE") or min(price, forward, strike) <= 0 or years <= 0:
        return np.nan
    df_r = float(discount_factor) if discount_factor is not None else np.exp(-rate * years)
    intrinsic = max(0.0, df_r * (forward - strike) if kind == "CE" else df_r * (strike - forward))
    upper = df_r * forward if kind == "CE" else df_r * strike
    if not (intrinsic < price < upper):
        return np.nan
    try:
        return float(brentq(
            lambda sigma: float(black76_greeks(forward, strike, years, rate, sigma, kind, discount_factor=df_r)["price"]) - price,
            bounds[0], bounds[1], xtol=tol, rtol=tol, maxiter=100
        ))
    except (ValueError, RuntimeError):
        return np.nan


# Sensibull aliases
sensibull_greeks = black76_greeks
sensibull_greeks_vec = black76_greeks_vec
sensibull_iv = solve_black76_iv


# ==============================================================================
# 3. UNIFIED CALCULATION & SIDE-BY-SIDE MODEL COMPARISON
# ==============================================================================

def calculate_greeks(underlying, strike, years, rate, vol, kind, model=DEFAULT_PRICING_MODEL, dividend=0.0):
    """Calculate Greeks using specified model (default: BSM, or 'black76' / 'sensibull')."""
    if str(model).lower() in ("black76", "black-76", "sensibull", "forward"):
        return black76_greeks(underlying, strike, years, rate, vol, kind)
    return bsm_greeks(underlying, strike, years, rate, vol, kind, dividend=dividend)


def calculate_iv(price, underlying, strike, years, rate, kind, model=DEFAULT_PRICING_MODEL, dividend=0.0, bounds=(1e-4, 5.0), tol=1e-8):
    """Calculate IV using specified model (default: BSM, or 'black76' / 'sensibull')."""
    if str(model).lower() in ("black76", "black-76", "sensibull", "forward"):
        return solve_black76_iv(price, underlying, strike, years, rate, kind, bounds=bounds, tol=tol)
    return solve_iv(price, underlying, strike, years, rate, kind, dividend=dividend, bounds=bounds, tol=tol)


def compare_bsm_and_black76(spot, strike, years, rate, vol, kind, dividend=0.0, future_price=None):
    """Side-by-side comparison between Spot BSM and Forward Black-76.
    
    1. Theoretical Forward: F_theo = S * exp((rate - dividend) * years)
       When F_theo is used with discount factor D = exp(-rate * years),
       Black-76 prices, delta, gamma, and vega mathematically equal Spot BSM.
    2. Traded Future (if provided): evaluates Black-76 with observed market basis.
    """
    bsm = bsm_greeks(spot, strike, years, rate, vol, kind, dividend=dividend)
    f_theo = spot * math.exp((float(rate) - float(dividend)) * float(years))
    b76_theo = black76_greeks(f_theo, strike, years, rate, vol, kind)
    
    res = {
        "bsm": bsm,
        "black76_theoretical": b76_theo,
        "price_diff_theo": abs(float(bsm["price"]) - float(b76_theo["price"])),
        "theoretical_forward": f_theo,
    }
    if future_price is not None and float(future_price) > 0:
        b76_traded = black76_greeks(future_price, strike, years, rate, vol, kind)
        res["black76_traded"] = b76_traded
        res["market_basis"] = float(future_price) - f_theo
        res["price_diff_traded"] = float(b76_traded["price"]) - float(bsm["price"])
    return res
