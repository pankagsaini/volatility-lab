"""Test configuration; no data downloads or application startup on collection."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import volatility_lab  # Initializes supported legacy imports without importing model/UI modules.
