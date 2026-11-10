"""Root Streamlit Entrypoint delegating to organized streamlit_desk package.

Maintains full backward compatibility for Streamlit Cloud and local CLI (`streamlit run streamlit_app.py`)
while keeping all Streamlit setup code neatly organized in `streamlit_desk/` and `Volatility Lab/`.
"""
from pathlib import Path
import os
import runpy
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "65536")

ROOT = Path(__file__).resolve().parent
LAB_DIR = ROOT / "Volatility Lab"
if str(LAB_DIR) in sys.path:
    sys.path.remove(str(LAB_DIR))
sys.path.insert(0, str(LAB_DIR))

DESK_ENTRY = ROOT / "streamlit_desk" / "app.py"
if DESK_ENTRY.exists():
    runpy.run_path(str(DESK_ENTRY), run_name="__main__")
else:
    runpy.run_path(str(LAB_DIR / "dashboard.py"), run_name="__main__")
