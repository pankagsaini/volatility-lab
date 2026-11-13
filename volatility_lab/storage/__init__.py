"""Storage implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['collector_support', 'daily_capture', 'snapshot_history', 'input_provenance', 'archive_coverage', 'data_accuracy']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.storage." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
