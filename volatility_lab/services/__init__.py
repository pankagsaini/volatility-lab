"""Services implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['expiry_data', 'market_table', 'option_context', 'options_context_cache', 'futures_data', 'futures_rolling', 'futures_view_cache', 'web_data', 'background_refresh']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.services." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
