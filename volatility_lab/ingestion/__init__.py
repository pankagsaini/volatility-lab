"""Ingestion implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['catalogue_fetch', 'upstox_adapter', 'upstox_data', 'upstox_rate_limit', 'upstox_archive', 'upstox_rolling', 'upstox_research', 'google_archive_sync', 'backtest_download', 'futures_backtest_download', 'manual_download_session', 'futures_sync']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.ingestion." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
