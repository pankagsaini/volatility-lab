"""Test configuration; no data downloads or application startup on collection."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import volatility_lab  # Initializes supported legacy imports without importing model/UI modules.

# Every CI worker starts with only the versioned references and writes to its own store.
import os
import tempfile
from volatility_lab.storage.references import initialize_references
_test_store = tempfile.TemporaryDirectory(prefix='volatility-lab-tests-')
os.environ['VOL_LAB_DATA_DIR'] = _test_store.name
initialize_references(Path(_test_store.name))
