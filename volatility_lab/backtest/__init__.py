"""Backtest implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['backtest_common', 'backtest_engine', 'backtest_jobs', 'backtest_report', 'backtest_signals', 'backtest_index', 'broker_charges', 'broker_options', 'forward_test', 'futures_backtest', 'strategy_backtest', 'trade_review']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.backtest." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
