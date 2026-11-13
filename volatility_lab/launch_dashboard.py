"""Compatibility launcher for the canonical local application."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from volatility_lab.runtime.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
