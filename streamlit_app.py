"""Root Streamlit Entrypoint delegating to organized streamlit_desk package.

Maintains full backward compatibility for Streamlit Cloud and external callers
while keeping all Streamlit setup code neatly organized in streamlit_desk/.
"""
from pathlib import Path
import runpy
import sys

DESK_ENTRY = Path(__file__).resolve().parent / "streamlit_desk" / "app.py"

if not DESK_ENTRY.exists():
    raise FileNotFoundError(f"Streamlit desk entrypoint not found at: {DESK_ENTRY}")

runpy.run_path(str(DESK_ENTRY), run_name="__main__")
