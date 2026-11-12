"""Root Streamlit entrypoint supporting both volatility_lab/ and Volatility Lab/ package layouts."""
from __future__ import annotations

import gc
import importlib.util
import os
import sys
from pathlib import Path

os.environ.setdefault("MALLOC_ARENA_MAX", "2")
os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "65536")

REPO_ROOT = Path(__file__).resolve().parent
VL_DIR = (REPO_ROOT / "volatility_lab") if (REPO_ROOT / "volatility_lab").is_dir() else (REPO_ROOT / "Volatility Lab")
if str(VL_DIR) not in sys.path:
    sys.path.insert(0, str(VL_DIR))

DASHBOARD_PATH = VL_DIR / "dashboard.py"
spec = importlib.util.spec_from_file_location("vl_dashboard_main", DASHBOARD_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to locate canonical dashboard entrypoint at {DASHBOARD_PATH}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
gc.collect()
