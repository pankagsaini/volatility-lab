"""Ui implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['download_control', 'model_comparison', 'model_view', 'web_server']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.ui." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
