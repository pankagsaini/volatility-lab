"""Research implementation modules; imports are lazy."""
from importlib import import_module

__all__ = ['experimental_models', 'forecast_validation', 'relative_value', 'research_extensions', 'restored_lab', 'run_experiments', 'run_model_validation']

def __getattr__(name):
    if name in __all__:
        value = import_module("volatility_lab.research." + name)
        globals()[name] = value
        return value
    raise AttributeError(name)
