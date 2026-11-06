"""Root Streamlit entrypoint with pre-compiled in-memory bytecode caching for sub-millisecond reruns."""
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

lab_dir = Path(__file__).resolve().parent / "Volatility Lab"
if str(lab_dir) not in sys.path:
    sys.path.insert(0, str(lab_dir))

dashboard_file = lab_dir / "dashboard.py"

# Cache compiled bytecode in sys to persist across Streamlit reruns
_CACHE_KEY = "_VOL_LAB_DASHBOARD_CODE"
cached = getattr(sys, _CACHE_KEY, None)

try:
    mtime = dashboard_file.stat().st_mtime
except OSError:
    mtime = 0

if cached is None or cached[0] != mtime:
    code = compile(dashboard_file.read_text(encoding="utf-8"), str(dashboard_file), "exec")
    setattr(sys, _CACHE_KEY, (mtime, code))
else:
    code = cached[1]

scope = {
    "__file__": str(dashboard_file),
    "__name__": "__main__",
    "__doc__": None,
}
exec(code, scope)
