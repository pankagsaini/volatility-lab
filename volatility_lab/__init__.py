"""Volatility Lab – Institutional Options Volatility Analytics & Backtesting Package."""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "18.0.0"

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
