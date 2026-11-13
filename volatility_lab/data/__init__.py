"""Compatibility facade; data/ holds read-only bundled reference artifacts."""
from importlib import import_module
__all__ = ['archive_coverage', 'daily_capture', 'google_archive_sync', 'snapshot_history', 'upstox_adapter', 'upstox_data']

def __getattr__(name):
    if name in __all__:
        return import_module("volatility_lab." + name)
    raise AttributeError(name)
