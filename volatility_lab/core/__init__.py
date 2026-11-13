"""Core implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['market_calendar', 'market_data', 'corporate_event_calendar', 'quote_quality', 'quote_status', 'candle_validation', 'history_policy', 'refresh_policy', 'vol_lib']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.core." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
