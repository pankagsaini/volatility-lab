"""Secondary Streamlit entrypoint delegating to streamlit_app.py."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "streamlit_app.py"), run_name="__main__")
