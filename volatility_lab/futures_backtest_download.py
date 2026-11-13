"""Compatibility alias; implementation lives in volatility_lab.ingestion.futures_backtest_download."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from volatility_lab._compat import install_alias
install_alias(globals(), "volatility_lab.ingestion.futures_backtest_download")
