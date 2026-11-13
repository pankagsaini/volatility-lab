"""Ui implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['dashboard', 'backtest_ui', 'futures_ui', 'futures_backtest_ui', 'monitor_ui', 'research_ui', 'trade_review_ui', 'ui_theme', 'dashboard_cache', 'research_health', 'download_control', 'model_comparison', 'model_view', 'web_server']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.ui." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
