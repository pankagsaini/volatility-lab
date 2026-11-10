"""Quantitative pricing & Greeks modules: Sensibull-standard Black-76 (default) and BSM."""
try:
    from .. import dividend_yield, option_math, risk_free, smile, synthetic_future, vol_lib, vol_surface_3d
    from ..option_math import (
        DEFAULT_PRICING_MODEL,
        black76_greeks,
        black76_greeks_vec,
        solve_black76_iv,
        sensibull_greeks,
        sensibull_greeks_vec,
        sensibull_iv,
        sensibull_dte_years,
        calculate_greeks,
        calculate_iv,
        bsm_greeks,
        bsm_greeks_vec,
        solve_iv,
    )
    from ..synthetic_future import determine_forward
except (ImportError, ValueError):
    import dividend_yield, option_math, risk_free, smile, synthetic_future, vol_lib, vol_surface_3d
    from option_math import (
        DEFAULT_PRICING_MODEL,
        black76_greeks,
        black76_greeks_vec,
        solve_black76_iv,
        sensibull_greeks,
        sensibull_greeks_vec,
        sensibull_iv,
        sensibull_dte_years,
        calculate_greeks,
        calculate_iv,
        bsm_greeks,
        bsm_greeks_vec,
        solve_iv,
    )
    from synthetic_future import determine_forward

__all__ = [
    "DEFAULT_PRICING_MODEL",
    "dividend_yield",
    "option_math",
    "risk_free",
    "smile",
    "synthetic_future",
    "determine_forward",
    "vol_lib",
    "vol_surface_3d",
    "black76_greeks",
    "black76_greeks_vec",
    "solve_black76_iv",
    "sensibull_greeks",
    "sensibull_greeks_vec",
    "sensibull_iv",
    "sensibull_dte_years",
    "calculate_greeks",
    "calculate_iv",
    "bsm_greeks",
    "bsm_greeks_vec",
    "solve_iv",
]
