import sys
import os
from pathlib import Path

# Add "Volatility Lab" directory to Python module search path
lab_dir = Path(__file__).resolve().parent / "Volatility Lab"
if str(lab_dir) not in sys.path:
    sys.path.insert(0, str(lab_dir))

dashboard_file = lab_dir / "dashboard.py"
scope = {
    "__file__": str(dashboard_file),
    "__name__": "__main__",
    "__doc__": None,
}
with open(dashboard_file, "r", encoding="utf-8") as f:
    code = compile(f.read(), str(dashboard_file), "exec")
    exec(code, scope)
