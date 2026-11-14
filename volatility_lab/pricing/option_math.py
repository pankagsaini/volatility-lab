"""European spot Black-Scholes-Merton (BSM) and Fischer Black (1976) Futures/Forward (Black-76) pricing, Greeks & IV solvers."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import erf, ndtr

_INV_SQRT_2PI = 0.3989422804014327
_VALID_CALL_KINDS = ('CE', 'CALL', 'C')
_VALID_PUT_KINDS = ('PE', 'PUT', 'P')
_VALID_KINDS = _VALID_CALL_KINDS + _VALID_PUT_KINDS


def _norm_pdf(x):
    with np.errstate(over='ignore', under='ignore', invalid='ignore'):
        return _INV_SQRT_2PI * np.exp(-0.5 * np.square(x))


def _is_scalar_inputs(*args) -> bool:
    return all(np.ndim(a) == 0 for a in args if a is not None)


# ==============================================================================
# 1. SPOT BLACK-SCHOLES-MERTON (BSM) MODEL (KEPT INTACT FOR SPOT COMPARISONS)
# ==============================================================================
@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def bsm_greeks(spot, strike, years, rate, vol, kind, dividend=0., *, div_yield=None):
    """Vectorized or scalar BSM price, spot delta, forward delta, gamma, vega (per 1% vol), and daily theta."""
    if kind not in ('CE', 'PE'):
        raise ValueError('Invalid option inputs.')
    if div_yield is not None:
        dividend = div_yield
    spot_a, strike_a, years_a, rate_a, vol_a, dividend_a = np.broadcast_arrays(
        np.asarray(spot, dtype=float), np.asarray(strike, dtype=float),
        np.asarray(years, dtype=float), np.asarray(rate, dtype=float),
        np.asarray(vol, dtype=float), np.asarray(dividend, dtype=float),
    )
    expired = years_a <= 0
    valid = (
        np.isfinite(spot_a) & (spot_a > 0)
        & np.isfinite(strike_a) & (strike_a > 0)
        & np.isfinite(years_a)
        & np.isfinite(rate_a)
        & np.isfinite(vol_a) & (vol_a >= 0)
        & np.isfinite(dividend_a)
        & (expired | (vol_a > 0))
    )
    if not np.all(valid):
        raise ValueError('Invalid option inputs.')
    active_t = np.where(expired, 1.0, years_a)
    active_vol = np.where(expired, 1.0, vol_a)
    sqrt_t = np.sqrt(active_t)
    vol_sqrt_t = active_vol * sqrt_t
    d1 = ((np.log(spot_a) - np.log(strike_a)) + (rate_a - dividend_a + 0.5 * np.square(active_vol)) * active_t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    df_q = np.exp(-dividend_a * active_t)
    df_r = np.exp(-rate_a * active_t)
    pdf_d1 = _norm_pdf(d1)
    if kind == 'CE':
        nd1, nd2 = ndtr(d1), ndtr(d2)
        price = spot_a * df_q * nd1 - strike_a * df_r * nd2
        delta = df_q * nd1
        fwd_delta = nd1
        theta_ann = (-spot_a * df_q * pdf_d1 * active_vol / (2.0 * sqrt_t)
                     - rate_a * strike_a * df_r * nd2 + dividend_a * spot_a * df_q * nd1)
    else:
        nd1_neg, nd2_neg = ndtr(-d1), ndtr(-d2)
        price = strike_a * df_r * nd2_neg - spot_a * df_q * nd1_neg
        delta = -df_q * nd1_neg
        fwd_delta = -nd1_neg
        theta_ann = (-spot_a * df_q * pdf_d1 * active_vol / (2.0 * sqrt_t)
                     + rate_a * strike_a * df_r * nd2_neg - dividend_a * spot_a * df_q * nd1_neg)
    gamma = (df_q * pdf_d1) / (spot_a * vol_sqrt_t)
    vega = spot_a * df_q * pdf_d1 * sqrt_t * 0.01
    call_delta_at_expiry = np.where(spot_a > strike_a, 1.0, np.where(spot_a < strike_a, 0.0, 0.5))
    expiry_delta = call_delta_at_expiry if kind == 'CE' else -np.where(
        spot_a < strike_a, 1.0, np.where(spot_a > strike_a, 0.0, 0.5)
    )
    price = np.where(expired, np.maximum(spot_a - strike_a, 0.0) if kind == 'CE'
                     else np.maximum(strike_a - spot_a, 0.0), price)
    delta = np.where(expired, expiry_delta, delta)
    fwd_delta = np.where(expired, expiry_delta, fwd_delta)
    gamma = np.where(expired, 0.0, gamma)
    vega = np.where(expired, 0.0, vega)
    theta_daily = np.where(expired, 0.0, theta_ann / 365.0)
    return {
        key: np.where(np.isfinite(value), value, np.nan) for key, value in
        [('price', price), ('delta', delta), ('fwd_delta', fwd_delta), ('gamma', gamma), ('vega', vega), ('theta_daily', theta_daily)]
    }


def price_delta(spot, strike, years, rate, vol, kind, dividend=0., *, div_yield=None):
    res = bsm_greeks(spot, strike, years, rate, vol, kind, dividend, div_yield=div_yield)
    return float(res['price']), float(res['delta'])


@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def solve_iv(price, spot, strike, years, rate, kind, dividend=0., bounds=(1e-4, 5.0), tol=1e-8):
    """Arbitrage-bounded Brentq implied volatility solver for spot BSM."""
    try:
        price, spot, strike, years, rate, dividend, lo, hi, tol = map(float, (
            price, spot, strike, years, rate, dividend, *bounds, tol))
    except (TypeError, ValueError, OverflowError):
        return np.nan
    if (not np.isfinite([price, spot, strike, years, rate, dividend, lo, hi, tol]).all()
            or kind not in ('CE', 'PE') or min(price, spot, strike, years, lo, tol) <= 0 or hi <= lo):
        return np.nan
    df_q = np.exp(-dividend * years)
    df_r = np.exp(-rate * years)
    intrinsic = max(0.0, spot * df_q - strike * df_r if kind == 'CE' else strike * df_r - spot * df_q)
    upper = spot * df_q if kind == 'CE' else strike * df_r
    if not (np.isfinite(upper) and intrinsic < price < upper):
        return np.nan
    try:
        return float(brentq(
            lambda sigma: price_delta(spot, strike, years, rate, sigma, kind, dividend)[0] - price,
            lo, hi, xtol=tol, rtol=tol, maxiter=100
        ))
    except (ValueError, RuntimeError):
        return np.nan


implied_vol = solve_iv


@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def bsm_greeks_vec(spot, strike, years, vol=None, rate=0.0, dividend=0.0, kind='CE', iv=None, div_yield=None):
    """Vectorized BSM Greeks; expired contracts are intrinsic and invalid lanes are NaN."""
    sigma = np.asarray(iv if vol is None else vol, dtype=float)
    try:
        q = float(div_yield if div_yield is not None else dividend)
        r = float(rate)
    except (TypeError, ValueError):
        q = r = np.nan
    S, K, T, sigma, kind_a = np.broadcast_arrays(
        np.asarray(spot, dtype=float), np.asarray(strike, dtype=float),
        np.asarray(years, dtype=float), sigma, np.asarray(kind, dtype=str),
    )
    is_call = np.isin(np.char.upper(kind_a), ['CE', 'CALL', 'C'])
    is_put = np.isin(np.char.upper(kind_a), ['PE', 'PUT', 'P'])
    expired = np.isfinite(T) & (T <= 0)
    valid = (
        np.isfinite(S) & (S > 0)
        & np.isfinite(K) & (K > 0)
        & np.isfinite(T)
        & np.isfinite(sigma) & (sigma >= 0)
        & (expired | (sigma > 0))
        & (is_call | is_put)
        & np.isfinite(r) & np.isfinite(q)
    )
    active = valid & (T > 0)
    safe_S = np.where(valid, S, 1.0)
    safe_K = np.where(valid, K, 1.0)
    safe_T = np.where(active, T, 1.0)
    safe_sigma = np.where(active, sigma, 1.0)
    sqrt_T = np.sqrt(safe_T)
    d1 = ((np.log(safe_S) - np.log(safe_K)) + (r - q + 0.5 * np.square(safe_sigma)) * safe_T) / (safe_sigma * sqrt_T)
    d2 = d1 - safe_sigma * sqrt_T
    df_r = np.exp(-r * safe_T)
    df_q = np.exp(-q * safe_T)
    pdf_d1 = _norm_pdf(d1)
    price = np.where(is_call, safe_S * df_q * ndtr(d1) - safe_K * df_r * ndtr(d2), safe_K * df_r * ndtr(-d2) - safe_S * df_q * ndtr(-d1))
    delta = np.where(is_call, df_q * ndtr(d1), -df_q * ndtr(-d1))
    gamma = (df_q * pdf_d1) / (safe_S * safe_sigma * sqrt_T)
    vega = safe_S * df_q * pdf_d1 * sqrt_T / 100.0
    theta_c = (-(safe_S * df_q * pdf_d1 * safe_sigma) / (2.0 * sqrt_T) - r * safe_K * df_r * ndtr(d2) + q * safe_S * df_q * ndtr(d1)) / 365.0
    theta_p = (-(safe_S * df_q * pdf_d1 * safe_sigma) / (2.0 * sqrt_T) + r * safe_K * df_r * ndtr(-d2) - q * safe_S * df_q * ndtr(-d1)) / 365.0
    theta = np.where(is_call, theta_c, theta_p)
    intrinsic = np.where(is_call, np.maximum(safe_S - safe_K, 0.0), np.maximum(safe_K - safe_S, 0.0))
    expiry_delta = np.where(
        is_call,
        np.where(safe_S > safe_K, 1.0, np.where(safe_S < safe_K, 0.0, 0.5)),
        np.where(safe_S < safe_K, -1.0, np.where(safe_S > safe_K, 0.0, -0.5)),
    )
    price = np.where(expired, intrinsic, price)
    delta = np.where(expired, expiry_delta, delta)
    gamma = np.where(expired, 0.0, gamma)
    vega = np.where(expired, 0.0, vega)
    theta = np.where(expired, 0.0, theta)
    price = np.where(valid & np.isfinite(price), price, np.nan)
    delta = np.where(valid & np.isfinite(delta), delta, np.nan)
    gamma = np.where(valid & np.isfinite(gamma), gamma, np.nan)
    vega = np.where(valid & np.isfinite(vega), vega, np.nan)
    theta = np.where(valid & np.isfinite(theta), theta, np.nan)
    return {'price': price, 'delta': delta, 'gamma': gamma, 'vega': vega, 'theta': theta}


# ==============================================================================
# 2. FISCHER BLACK (1976) FUTURES / FORWARD MODEL (BLACK-76 ENGINE)
# ==============================================================================
@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def forward_from_spot(spot, years, rate=0.0, div_yield=0.0, cost_of_carry=None, dividend=None):
    """Derive implied forward price F = S * exp(b * T) from spot and carry rate b = (r - q) or explicit cost_of_carry."""
    scalar = _is_scalar_inputs(spot, years, rate, div_yield, cost_of_carry, dividend)
    q = dividend if dividend is not None else div_yield
    try:
        s_arr = np.asarray(spot, dtype=float)
        t_arr = np.asarray(years, dtype=float)
        r_arr = np.asarray(rate, dtype=float)
        q_arr = np.asarray(0.0 if q is None else q, dtype=float)
        b_arr = np.asarray(cost_of_carry, dtype=float) if cost_of_carry is not None else (r_arr - q_arr)
    except (TypeError, ValueError):
        return float(np.nan) if scalar else np.array([np.nan], dtype=float)

    s_b, t_b, b_b = np.broadcast_arrays(s_arr, t_arr, b_arr)
    valid = np.isfinite(s_b) & (s_b > 0.0) & np.isfinite(t_b) & (t_b >= 0.0) & np.isfinite(b_b)
    out = np.full(s_b.shape, np.nan, dtype=float)
    with np.errstate(over='ignore', under='ignore', invalid='ignore'):
        out[valid] = s_b[valid] * np.exp(b_b[valid] * t_b[valid])
    out = np.where(np.isfinite(out) & (out > 0), out, np.nan)
    return float(out.item()) if scalar else out


def parity_implied_forward(strike, call_price, put_price, years, rate=0.0):
    """Compute Put-Call Parity Implied Forward F = K + exp(r * T) * (C - P)."""
    scalar = _is_scalar_inputs(strike, call_price, put_price, years, rate)
    try:
        k_arr = np.asarray(strike, dtype=float)
        c_arr = np.asarray(call_price, dtype=float)
        p_arr = np.asarray(put_price, dtype=float)
        t_arr = np.asarray(years, dtype=float)
        r_arr = np.asarray(rate, dtype=float)
    except (TypeError, ValueError):
        return float(np.nan) if scalar else np.array([np.nan], dtype=float)

    k_b, c_b, p_b, t_b, r_b = np.broadcast_arrays(k_arr, c_arr, p_arr, t_arr, r_arr)
    valid = (
        np.isfinite(k_b) & (k_b > 0.0)
        & np.isfinite(c_b) & (c_b >= 0.0)
        & np.isfinite(p_b) & (p_b >= 0.0)
        & np.isfinite(t_b) & (t_b >= 0.0)
        & np.isfinite(r_b)
    )
    out = np.full(k_b.shape, np.nan, dtype=float)
    with np.errstate(over='ignore', under='ignore', invalid='ignore'):
        t_pos = np.maximum(0.0, t_b[valid])
        out[valid] = k_b[valid] + np.exp(r_b[valid] * t_pos) * (c_b[valid] - p_b[valid])
    out = np.where(np.isfinite(out) & (out > 0), out, np.nan)
    return float(out.item()) if scalar else out


@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def _black76_core(forward, strike, years, rate, vol, kind, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None, force_array=False):
    """Core scalar & vectorized Black-76 option pricing and Greeks engine with full edge-case & T<=0 handling."""
    if forward is None and spot is not None:
        # At or after expiry use the supplied spot as the settlement reference.
        # Clamping only the carry horizon keeps positive-time lanes unchanged.
        try:
            carry_years = np.maximum(np.asarray(years, dtype=float), 0.0)
            forward = forward_from_spot(
                spot, carry_years, rate=rate, div_yield=div_yield,
                cost_of_carry=cost_of_carry, dividend=dividend,
            )
        except (TypeError, ValueError):
            forward = np.nan

    scalar = (not force_array) and _is_scalar_inputs(forward, strike, years, rate, vol, kind)

    try:
        f_arr = np.asarray(forward, dtype=float)
        k_arr = np.asarray(strike, dtype=float)
        t_arr = np.asarray(years, dtype=float)
        r_arr = np.asarray(rate, dtype=float)
        v_arr = np.asarray(vol, dtype=float)
        kind_arr = np.char.upper(np.char.strip(np.asarray(kind, dtype=str)))
    except (TypeError, ValueError):
        nan_val = float(np.nan) if scalar else np.array([np.nan], dtype=float)
        return {
            'price': nan_val,
            'delta': nan_val,
            'fwd_delta': nan_val,
            'forward_delta': nan_val,
            'gamma': nan_val,
            'vega': nan_val,
            'theta_daily': nan_val,
            'theta': nan_val,
        }

    f_b, k_b, t_b, r_b, v_b, kind_b = np.broadcast_arrays(f_arr, k_arr, t_arr, r_arr, v_arr, kind_arr)
    shape = f_b.shape

    price = np.full(shape, np.nan, dtype=float)
    delta = np.full(shape, np.nan, dtype=float)
    fwd_delta = np.full(shape, np.nan, dtype=float)
    gamma = np.full(shape, np.nan, dtype=float)
    vega = np.full(shape, np.nan, dtype=float)
    theta_daily = np.full(shape, np.nan, dtype=float)

    valid_kind = np.isin(kind_b, _VALID_KINDS)
    is_call = np.isin(kind_b, _VALID_CALL_KINDS)

    valid_base = (
        valid_kind
        & np.isfinite(f_b) & (f_b > 0.0)
        & np.isfinite(k_b) & (k_b > 0.0)
        & np.isfinite(t_b)
        & np.isfinite(r_b)
        & np.isfinite(v_b) & (v_b >= 0.0)
    )

    # 1. Expiry or post-expiry regime (T <= 0): intrinsic value, deterministic delta, zero gamma/vega/theta
    at_expiry = valid_base & (t_b <= 0.0)
    if np.any(at_expiry):
        f_e = f_b[at_expiry]
        k_e = k_b[at_expiry]
        c_e = is_call[at_expiry]
        price[at_expiry] = np.where(c_e, np.maximum(f_e - k_e, 0.0), np.maximum(k_e - f_e, 0.0))
        call_det_d = np.where(f_e > k_e, 1.0, np.where(f_e == k_e, 0.5, 0.0))
        put_det_d = np.where(f_e < k_e, -1.0, np.where(f_e == k_e, -0.5, 0.0))
        det_d = np.where(c_e, call_det_d, put_det_d)
        delta[at_expiry] = det_d
        fwd_delta[at_expiry] = det_d
        gamma[at_expiry] = 0.0
        vega[at_expiry] = 0.0
        theta_daily[at_expiry] = 0.0

    # 1b. Zero-volatility limit for positive time. The payoff is deterministic;
    # gamma is undefined exactly at the kink, while vega is its right derivative.
    zero_vol = valid_base & (t_b > 0.0) & (v_b == 0.0)
    if np.any(zero_vol):
        f_z = f_b[zero_vol]
        k_z = k_b[zero_vol]
        t_z = t_b[zero_vol]
        r_z = r_b[zero_vol]
        c_z = is_call[zero_vol]
        disc_z = np.exp(-r_z * t_z)
        call_payoff = np.maximum(f_z - k_z, 0.0)
        put_payoff = np.maximum(k_z - f_z, 0.0)
        payoff = np.where(c_z, call_payoff, put_payoff)
        call_det_d = np.where(f_z > k_z, 1.0, np.where(f_z < k_z, 0.0, 0.5))
        put_det_d = np.where(f_z < k_z, -1.0, np.where(f_z > k_z, 0.0, -0.5))
        det_d = np.where(c_z, call_det_d, put_det_d)
        at_kink = f_z == k_z
        price[zero_vol] = disc_z * payoff
        delta[zero_vol] = disc_z * det_d
        fwd_delta[zero_vol] = det_d
        gamma[zero_vol] = np.where(at_kink, np.nan, 0.0)
        vega[zero_vol] = np.where(
            at_kink, 0.01 * disc_z * f_z * _INV_SQRT_2PI * np.sqrt(t_z), 0.0
        )
        theta_daily[zero_vol] = r_z * disc_z * payoff / 365.0

    # 2. Active time-to-expiry regime (T > 0), including near-zero expiry (T -> 0+)
    pos_t = valid_base & (t_b > 0.0) & (v_b > 0.0)
    if np.any(pos_t):
        with np.errstate(divide='ignore', over='ignore', under='ignore', invalid='ignore'):
            F = f_b[pos_t]
            K = k_b[pos_t]
            T = t_b[pos_t]
            r = r_b[pos_t]
            sigma = v_b[pos_t]
            c_mask = is_call[pos_t]

            D = np.exp(-r * T)
            sqrt_T = np.sqrt(T)
            vol_sqrt_T = sigma * sqrt_T
            log_fk = np.log(F) - np.log(K)
            # For any representable positive v√T the ATM gamma and theta are
            # nonzero. An arbitrary small-time cutoff understates expiry risk.
            d1 = np.divide(log_fk, vol_sqrt_T) + 0.5 * vol_sqrt_T
            d1 = np.where(F == K, 0.5 * vol_sqrt_T, d1)
            d2 = d1 - vol_sqrt_T

            nd1 = ndtr(d1)
            nd2 = ndtr(d2)
            nd1_neg = ndtr(-d1)
            nd2_neg = ndtr(-d2)
            pdf_d1 = _norm_pdf(d1)

            call_p = np.maximum(0.0, D * (F * nd1 - K * nd2))
            put_p = np.maximum(0.0, D * (K * nd2_neg - F * nd1_neg))
            opt_p = np.where(c_mask, call_p, put_p)

            opt_p = np.where(F == K, D * F * erf(vol_sqrt_T / (2 * np.sqrt(2.0))), opt_p)

            fwd_d = np.where(c_mask, nd1, -nd1_neg)
            disc_d = D * fwd_d

            gam = np.where(pdf_d1 == 0.0, 0.0, (D * pdf_d1) / (F * vol_sqrt_T))
            veg = np.where(pdf_d1 == 0.0, 0.0, 0.01 * D * F * pdf_d1 * sqrt_T)

            diff_term = np.where(pdf_d1 == 0.0, 0.0, -(D * F * pdf_d1 * sigma) / (2.0 * sqrt_T))
            theta_ann = diff_term + r * opt_p
            th_daily = theta_ann / 365.0

            price[pos_t] = opt_p
            delta[pos_t] = disc_d
            fwd_delta[pos_t] = fwd_d
            gamma[pos_t] = gam
            vega[pos_t] = veg
            theta_daily[pos_t] = th_daily

    for values in (price, delta, fwd_delta, gamma, vega, theta_daily):
        values[~np.isfinite(values)] = np.nan
    if scalar:
        p_f = float(price.item())
        d_f = float(delta.item())
        fd_f = float(fwd_delta.item())
        g_f = float(gamma.item())
        v_f = float(vega.item())
        th_f = float(theta_daily.item())
        return {
            'price': p_f,
            'delta': d_f,
            'fwd_delta': fd_f,
            'forward_delta': fd_f,
            'gamma': g_f,
            'vega': v_f,
            'theta_daily': th_f,
            'theta': th_f,
        }

    return {
        'price': price,
        'delta': delta,
        'fwd_delta': fwd_delta,
        'forward_delta': fwd_delta.copy(),
        'gamma': gamma,
        'vega': vega,
        'theta_daily': theta_daily,
        'theta': theta_daily.copy(),
    }


def black76_greeks(forward=None, strike=None, years=None, rate=0.0, vol=None, kind='CE', *, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None, iv=None):
    """Scalar or vectorized Fischer Black (1976) option price, discounted delta, forward delta, gamma, vega (per 1% vol), and daily theta."""
    sigma = iv if vol is None else vol
    return _black76_core(
        forward=forward,
        strike=strike,
        years=years,
        rate=rate,
        vol=sigma,
        kind=kind,
        spot=spot,
        div_yield=div_yield,
        cost_of_carry=cost_of_carry,
        dividend=dividend,
        force_array=False,
    )


def black76_greeks_vec(forward=None, strike=None, years=None, rate=0.0, vol=None, kind='CE', iv=None, *, is_call=None, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None):
    """Vectorized Fischer Black (1976) Greeks supporting array inputs and safe T <= 0 / invalid-input handling."""
    if is_call is not None:
        kind = np.where(is_call, 'CE', 'PE')
    sigma = iv if vol is None else vol
    return _black76_core(
        forward=forward,
        strike=strike,
        years=years,
        rate=rate,
        vol=sigma,
        kind=kind,
        spot=spot,
        div_yield=div_yield,
        cost_of_carry=cost_of_carry,
        dividend=dividend,
        force_array=True,
    )


def black76_price_delta(forward=None, strike=None, years=None, rate=0.0, vol=None, kind='CE', *, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None):
    """Return (price, discounted_delta) under the Fischer Black (1976) model."""
    res = black76_greeks(
        forward=forward,
        strike=strike,
        years=years,
        rate=rate,
        vol=vol,
        kind=kind,
        spot=spot,
        div_yield=div_yield,
        cost_of_carry=cost_of_carry,
        dividend=dividend,
    )
    if _is_scalar_inputs(res['price'], res['delta']):
        return float(res['price']), float(res['delta'])
    return res['price'], res['delta']


@np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore')
def _solve_black76_iv_scalar(price, forward, strike, years, rate=0.0, kind='CE', bounds=(1e-4, 5.0), tol=1e-8):
    """Single-contract arbitrage-bounded Brentq implied volatility solver for Black-76."""
    if not isinstance(kind, (str, np.str_)):
        return float(np.nan)
    k_str = str(kind).strip().upper()
    if k_str not in _VALID_KINDS:
        return float(np.nan)
    is_call = k_str in _VALID_CALL_KINDS

    try:
        px = float(price)
        F = float(forward)
        K = float(strike)
        T = float(years)
        r = float(rate)
        low_b = float(bounds[0])
        high_b = float(bounds[1])
        tol = float(tol)
    except (TypeError, ValueError, OverflowError, IndexError):
        return float(np.nan)

    if not (np.isfinite(px) and np.isfinite(F) and np.isfinite(K) and np.isfinite(T) and np.isfinite(r)):
        return float(np.nan)
    if not np.isfinite([low_b, high_b, tol]).all() or tol <= 0:
        return float(np.nan)
    if px <= 0.0 or F <= 0.0 or K <= 0.0 or T <= 0.0 or low_b <= 0.0 or high_b <= low_b:
        return float(np.nan)

    D = float(np.exp(-r * T))
    intrinsic = max(0.0, D * (F - K) if is_call else D * (K - F))
    upper = D * F if is_call else D * K
    if not (np.isfinite(upper) and intrinsic < px < upper):
        return float(np.nan)

    canonical_kind = 'CE' if is_call else 'PE'
    try:
        f_low = _black76_core(F, K, T, r, low_b, canonical_kind)['price'] - px
        f_high = _black76_core(F, K, T, r, high_b, canonical_kind)['price'] - px
        if not (np.isfinite(f_low) and np.isfinite(f_high)):
            return float(np.nan)
        if abs(f_low) <= tol:
            return low_b
        if abs(f_high) <= tol:
            return high_b
        if f_low * f_high > 0.0:
            return float(np.nan)
        return float(brentq(
            lambda sigma: _black76_core(F, K, T, r, sigma, canonical_kind)['price'] - px,
            low_b,
            high_b,
            xtol=tol,
            rtol=tol,
            maxiter=100,
        ))
    except (ValueError, RuntimeError, ZeroDivisionError, OverflowError):
        return float(np.nan)


def solve_black76_iv_vec(price, forward=None, strike=None, years=None, rate=0.0, kind='CE', bounds=(1e-4, 5.0), tol=1e-8, *, is_call=None, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None):
    if is_call is not None:
        kind = np.where(is_call, 'CE', 'PE')
    """Vectorized Black-76 implied volatility solver over NumPy arrays."""
    if forward is None and spot is not None:
        forward = forward_from_spot(spot, years, rate=rate, div_yield=div_yield, cost_of_carry=cost_of_carry, dividend=dividend)
    try:
        p_arr = np.asarray(price, dtype=float)
        f_arr = np.asarray(forward, dtype=float)
        k_arr = np.asarray(strike, dtype=float)
        t_arr = np.asarray(years, dtype=float)
        r_arr = np.asarray(rate, dtype=float)
        kind_arr = np.asarray(kind, dtype=str)
    except (TypeError, ValueError):
        return np.array([np.nan], dtype=float)

    p_b, f_b, k_b, t_b, r_b, kind_b = np.broadcast_arrays(p_arr, f_arr, k_arr, t_arr, r_arr, kind_arr)
    out = np.full(p_b.shape, np.nan, dtype=float)
    for idx in np.ndindex(p_b.shape):
        out[idx] = _solve_black76_iv_scalar(
            p_b[idx], f_b[idx], k_b[idx], t_b[idx], r_b[idx], kind_b[idx], bounds=bounds, tol=tol
        )
    return out


def solve_black76_iv(price, forward=None, strike=None, years=None, rate=0.0, kind='CE', bounds=(1e-4, 5.0), tol=1e-8, *, spot=None, div_yield=0.0, cost_of_carry=None, dividend=None):
    """Scalar or array Black-76 implied volatility solver using Put-Call Parity Forward F."""
    if forward is None and spot is not None:
        forward = forward_from_spot(spot, years, rate=rate, div_yield=div_yield, cost_of_carry=cost_of_carry, dividend=dividend)
    if not _is_scalar_inputs(price, forward, strike, years, rate, kind):
        return solve_black76_iv_vec(
            price, forward=forward, strike=strike, years=years, rate=rate, kind=kind, bounds=bounds, tol=tol
        )
    return _solve_black76_iv_scalar(price, forward, strike, years, rate=rate, kind=kind, bounds=bounds, tol=tol)


black76_implied_vol = solve_black76_iv


def black76_chain_greeks(
    chain: pd.DataFrame,
    forward: float | None = None,
    spot: float | None = None,
    years: float | None = None,
    rate: float | None = None,
    *,
    expiry: str | None = None,
    as_of=None,
    max_quote_age_seconds: float = 120.0,
    allow_unvalidated_quotes: bool = False,
) -> pd.DataFrame:
    """Calculate model IV solely from fresh, same-expiry bid/ask midpoints."""
    if allow_unvalidated_quotes:
        raise ValueError('Chain valuation requires observed two-sided books and a valuation timestamp.')
    if chain is None or chain.empty:
        return pd.DataFrame() if chain is None else chain.copy()
    df = chain.copy()
    strike_col = 'strike' if 'strike' in df.columns else ('Strike' if 'Strike' in df.columns else 'strike_price')
    type_col = 'option_type' if 'option_type' in df.columns else ('Type' if 'Type' in df.columns else 'Kind')
    from volatility_lab.pricing.pricing_policy import book, number
    expiry_col = next((c for c in ('expiry', 'expiration') if c in df), None)
    expires = df[expiry_col].astype(str).str[:10] if expiry_col else pd.Series('', index=df.index)
    listed = set(expires) - {'', 'nan', 'NaT', 'None'}
    target = str(expiry)[:10] if expiry is not None else next(iter(listed)) if len(listed) == 1 else None
    eligible = (expires == target).to_numpy() if target else np.zeros(len(df), dtype=bool)
    strikes = pd.to_numeric(df[strike_col], errors='coerce').to_numpy(dtype=float)
    kinds = df[type_col].astype(str).str.strip().str.upper().to_numpy()
    prices = np.full(len(df), np.nan)
    books = {}
    # Map broker aliases while using the same depth, freshness and spread rules
    # as the live pricing policy. Missing observation times never imply freshness.
    for i, record in enumerate(df.to_dict('records')):
        if not eligible[i]:
            continue
        for dest, aliases in [('quote_timestamp', ('timestamp','feed_timestamp','currentTs','ts')),
                              ('bid', ('Bid',)), ('ask', ('Ask',))]:
            if dest not in record:
                record[dest] = next((record[col] for col in aliases if col in record), None)
        current = book(record, as_of, max_quote_age_seconds)
        if current is not None:
            books[i] = current
            prices[i] = current['mid']
    if forward is None:
        listed_strikes = np.unique(strikes[eligible & np.isfinite(strikes) & (strikes > 0)])
        ref = number(spot)
        atm = None
        if len(listed_strikes) == 1:
            atm = listed_strikes[0]
        elif len(listed_strikes) and ref is not None and ref > 0:
            atm = min(listed_strikes, key=lambda k: (abs(k - ref), k))
        calls = np.flatnonzero(eligible & (strikes == atm) & np.isin(kinds, _VALID_CALL_KINDS))
        puts = np.flatnonzero(eligible & (strikes == atm) & np.isin(kinds, _VALID_PUT_KINDS))
        if len(calls) == len(puts) == 1:
            call, put = books.get(calls[0]), books.get(puts[0])
            if call and put and max(call['spread'], put['spread']) < 3:
                if abs((call['timestamp'] - put['timestamp']).total_seconds()) <= 5:
                    forward = parity_implied_forward(atm, call['mid'], put['mid'], years, rate)
    forward = number(forward)
    forward = forward if forward is not None and forward > 0 else np.nan
    ivs = solve_black76_iv_vec(prices, forward=forward, strike=strikes, years=years, rate=rate, kind=kinds)
    g = black76_greeks_vec(forward=forward, strike=strikes, years=years, vol=ivs, rate=rate, kind=kinds)
    df['forward'] = forward
    df['iv_b76'] = ivs
    df['delta_b76'] = g['delta']
    df['fwd_delta_b76'] = g['fwd_delta']
    df['gamma_b76'] = g['gamma']
    df['vega_b76'] = g['vega']
    df['theta_daily_b76'] = g['theta_daily']
    return df


# ==============================================================================
# 3. SMILE & SKEW INTERPOLATION METRICS (SUPPORTS SPOT & BLACK-76 DELTAS)
# ==============================================================================
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


def skew_metrics(chain, spot=None, forward=None):
    put = delta_iv(chain, 'PE', .25)
    call = delta_iv(chain, 'CE', .25)
    proxy = (delta_iv(chain, 'PE', .5) + delta_iv(chain, 'CE', .5)) / 2
    ref_level = forward if (forward is not None and np.isfinite(forward) and forward > 0) else spot
    if ref_level is not None and 'strike' in chain.columns:
        valid = chain.copy()
        valid['iv'] = pd.to_numeric(valid.iv, errors='coerce').astype(float)
        valid['strike'] = pd.to_numeric(valid.strike, errors='coerce').astype(float)
        valid = valid[np.isfinite(valid.iv) & (valid.iv > 0) & valid.option_type.isin(['CE', 'PE'])]
        pairs = valid.groupby('strike').option_type.agg(set)
        paired = [float(k) for k, kinds in pairs.items() if kinds == {'CE', 'PE'}]
        if paired:
            atm_k = min(paired, key=lambda k: (abs(k - ref_level), k))
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


DEFAULT_PRICING_MODEL = "black76"

# Sensibull and unified model aliases
sensibull_greeks = black76_greeks
sensibull_greeks_vec = black76_greeks_vec
sensibull_iv = solve_black76_iv

def sensibull_dte_years(dte_days: float) -> float:
    """Convert DTE in calendar days to ACT/365 years."""
    return max(0.0, float(dte_days or 0.0)) / 365.0

def calculate_greeks(underlying, strike, years, rate, vol, kind, model=DEFAULT_PRICING_MODEL, dividend=0.0):
    """Calculate Greeks using specified model (default: Black-76 with a forward input; explicitly select 'bsm' for spot)."""
    if str(model).lower() in ("black76", "black-76", "sensibull", "forward"):
        return black76_greeks(underlying, strike, years, rate, vol, kind)
    if str(model).lower() != 'bsm':
        raise ValueError(f'Unknown pricing model: {model}')
    return bsm_greeks(underlying, strike, years, rate, vol, kind, dividend=dividend)

def calculate_iv(price, underlying, strike, years, rate, kind, model=DEFAULT_PRICING_MODEL, dividend=0.0, bounds=(1e-4, 5.0), tol=1e-8):
    """Calculate IV using specified model (default: Black-76 with a forward input; explicitly select 'bsm' for spot)."""
    if str(model).lower() in ("black76", "black-76", "sensibull", "forward"):
        return solve_black76_iv(price, underlying, strike, years, rate, kind, bounds=bounds, tol=tol)
    if str(model).lower() != 'bsm':
        raise ValueError(f'Unknown pricing model: {model}')
    return solve_iv(price, underlying, strike, years, rate, kind, dividend=dividend, bounds=bounds, tol=tol)

def compare_bsm_and_black76(spot, strike, years, rate, vol, kind, dividend=0.0, future_price=None, div_yield=None):
    """Side-by-side comparison between Spot BSM and Forward Black-76."""
    if div_yield is not None:
        dividend = div_yield
    bsm = bsm_greeks(spot, strike, years, rate, vol, kind, dividend=dividend)
    f_theo = forward_from_spot(spot, years, rate, div_yield=dividend)
    b76_theo = black76_greeks(f_theo, strike, years, rate, vol, kind)
    diff = abs(float(bsm["price"]) - float(b76_theo["price"]))
    res = {
        "bsm": bsm,
        "black76_theoretical": b76_theo,
        "price_diff_theo": diff,
        "price_diff": diff,
        "bsm_price": bsm["price"],
        "black76_price": b76_theo["price"],
        "bsm_vega": bsm["vega"],
        "black76_vega": b76_theo["vega"],
        "vega_diff": abs(float(bsm["vega"]) - float(b76_theo["vega"])),
        "theoretical_forward": f_theo,
    }
    if future_price is not None and float(future_price) > 0:
        b76_traded = black76_greeks(future_price, strike, years, rate, vol, kind)
        res["black76_traded"] = b76_traded
        res["market_basis"] = float(future_price) - f_theo
        res["price_diff_traded"] = float(b76_traded["price"]) - float(bsm["price"])
    return res
