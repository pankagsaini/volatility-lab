"""Quantitative pricing & Greeks modules: BSM (primary default) and Black-76 (second model)."""
import numpy as np
import option_math

DEFAULT_PRICING_MODEL = option_math.DEFAULT_PRICING_MODEL
bsm_greeks = option_math.bsm_greeks
bsm_greeks_vec = option_math.bsm_greeks_vec
solve_iv = option_math.solve_iv
forward_from_spot = option_math.forward_from_spot
parity_implied_forward = option_math.parity_implied_forward
compare_bsm_and_black76 = option_math.compare_bsm_and_black76
calculate_greeks = option_math.calculate_greeks
calculate_iv = option_math.calculate_iv
sensibull_greeks = option_math.sensibull_greeks
sensibull_greeks_vec = option_math.sensibull_greeks_vec
sensibull_iv = option_math.sensibull_iv
sensibull_dte_years = option_math.sensibull_dte_years

def black76_greeks(forward, strike, years, rate, vol, kind, *args, **kwargs):
    """Strict validation Black-76 pricing helper for tests requiring ValueError assertions."""
    if kind not in ("CE", "PE", "CALL", "PUT", "C", "P"):
        raise ValueError("Invalid option kind")
    try:
        f_val, k_val, t_val, r_val, v_val = float(forward), float(strike), float(years), float(rate), float(vol)
    except (TypeError, ValueError):
        raise ValueError("Invalid numeric input")
    if f_val <= 0 or k_val <= 0 or v_val < 0:
        raise ValueError("Invalid input values")
    res = option_math.black76_greeks(forward=f_val, strike=k_val, years=t_val, rate=r_val, vol=v_val, kind=kind, *args, **kwargs)
    return res

solve_black76_iv = option_math.solve_black76_iv
solve_black76_iv_vec = option_math.solve_black76_iv_vec
black76_greeks_vec = option_math.black76_greeks_vec

__all__ = [
    "DEFAULT_PRICING_MODEL",
    "bsm_greeks",
    "bsm_greeks_vec",
    "solve_iv",
    "black76_greeks",
    "black76_greeks_vec",
    "solve_black76_iv",
    "solve_black76_iv_vec",
    "sensibull_greeks",
    "sensibull_greeks_vec",
    "sensibull_iv",
    "sensibull_dte_years",
    "calculate_greeks",
    "calculate_iv",
    "compare_bsm_and_black76",
]
