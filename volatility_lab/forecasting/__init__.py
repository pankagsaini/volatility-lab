"""Forecasting implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['expiry_forecast', 'forecast', 'har_research', 'term_structure', 'volatility_regime']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.forecasting." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
