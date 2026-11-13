"""Volatility Lab domain packages and support for legacy module imports."""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "18.0.0"

PACKAGE_ROOT = Path(__file__).resolve().parent
_PKG_DIR = PACKAGE_ROOT
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
