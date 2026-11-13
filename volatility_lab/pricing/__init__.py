"""Pricing implementations and a lazy, backwards-compatible public API."""
from importlib import import_module

_MODULES = ['option_math', 'pricing_policy', 'pricing_sources', 'pricing_inputs', 'synthetic_future', 'smile', 'dividend_yield', 'risk_free', 'vol_surface_3d']
_MATH = ['DEFAULT_PRICING_MODEL','bsm_greeks','bsm_greeks_vec','solve_iv','forward_from_spot',
         'parity_implied_forward','compare_bsm_and_black76','calculate_greeks','calculate_iv',
         'sensibull_greeks','sensibull_greeks_vec','sensibull_iv','sensibull_dte_years',
         'solve_black76_iv','solve_black76_iv_vec','black76_greeks_vec']
__all__ = _MODULES + _MATH + ['black76_greeks','select_model','price_observed_option']

def __getattr__(name):
    if name in _MODULES:
        value = import_module('volatility_lab.pricing.' + name)
    elif name in _MATH:
        value = getattr(import_module('volatility_lab.pricing.option_math'), name)
    elif name in ('select_model','price_observed_option'):
        value = getattr(import_module('volatility_lab.pricing.pricing_policy'), name)
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value

def black76_greeks(forward, strike, years, rate, vol, kind, *args, **kwargs):
    """Strict compatibility validation over the canonical numerical engine."""
    if kind not in ('CE','PE','CALL','PUT','C','P'):
        raise ValueError('Invalid option kind')
    try:
        f,k,t,r,v = map(float, (forward,strike,years,rate,vol))
    except (TypeError,ValueError):
        raise ValueError('Invalid numeric input') from None
    if f <= 0 or k <= 0 or v < 0:
        raise ValueError('Invalid input values')
    return import_module('volatility_lab.pricing.option_math').black76_greeks(f,k,t,r,v,kind,*args,**kwargs)
