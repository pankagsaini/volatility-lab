"""Volatility Lab – Institutional Options Volatility Analytics & Backtesting Package.

Package Architecture & Modular Facade:
--------------------------------------
This package is organized around a dual-import facade architecture:
1. Root Package Namespace (`volatility_lab.*`):
   Core implementation modules reside directly under `volatility_lab/` to maintain 100%
   backwards compatibility with existing scripts, notebooks, and Streamlit entrypoints.
2. Semantic Subpackages (`volatility_lab.backtest`, `volatility_lab.pricing`, etc.):
   Subpackages provide clean, modern domain namespaces (`backtest`, `core`, `forecasting`,
   `pricing`, `ui`) that re-export audited modules for external consumers and new components.
"""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "18.0.0"

PACKAGE_ROOT = Path(__file__).resolve().parent
_PKG_DIR = PACKAGE_ROOT
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
