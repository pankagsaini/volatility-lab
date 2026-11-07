"""Root Streamlit Cloud & Local Entrypoint for Volatility Lab (Memory-Bounded v11)."""
import gc
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "65536")

LAB_DIR = Path(__file__).resolve().parent / "Volatility Lab"
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

DASHBOARD_FILE = LAB_DIR / "dashboard.py"
_CACHE_KEY = "_vol_lab_dashboard_bytecode"
_SCOPE_KEY = "_vol_lab_dashboard_scope"


def _run_dashboard() -> None:
    mtime = DASHBOARD_FILE.stat().st_mtime_ns
    cached = getattr(sys, _CACHE_KEY, None)
    if cached is None or cached[0] != mtime:
        source = DASHBOARD_FILE.read_text(encoding="utf-8")
        code = compile(source, str(DASHBOARD_FILE), "exec")
        setattr(sys, _CACHE_KEY, (mtime, code))
        gc.collect()
    else:
        code = cached[1]
    scope = getattr(sys, _SCOPE_KEY, None)
    if scope is None:
        scope = {}
        setattr(sys, _SCOPE_KEY, scope)
    scope["__file__"] = str(DASHBOARD_FILE)
    scope["__name__"] = "__main__"
    scope["__doc__"] = None
    exec(code, scope)


_run_dashboard()
