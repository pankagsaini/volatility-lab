#!/usr/bin/env python3
"""Validate the local Volatility Lab Python environment before launch."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IMPORTS = (
    "streamlit",
    "fastapi",
    "uvicorn",
    "numpy",
    "pandas",
    "scipy",
    "arch",
    "plotly",
    "requests",
    "pypdf",
)


def check_python_version() -> None:
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 11):
        raise SystemExit(
            "Volatility Lab requires Python 3.11+ to run. "
            f"Current version: {sys.version}."
        )
    print(f"Python OK: {sys.version.split()[0]}")


def ensure_import(name: str) -> None:
    try:
        importlib.import_module(name)
        print(f"Dependency OK: {name}")
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"Missing dependency '{name}'. Install with: python -m pip install -r requirements.txt"
        ) from exc


def ensure_paths() -> None:
    required = [
        ROOT / "Volatility Lab",
        ROOT / "Volatility Lab" / "dashboard.py",
        ROOT / "Volatility Lab" / "web_server.py",
        ROOT / "Volatility Lab" / "data",
        ROOT / "Volatility Lab" / "frontend",
    ]
    for path in required:
        if not path.exists():
            raise SystemExit(f"Missing required path: {path}")
    print("Repository paths OK")


def main() -> None:
    print("Validating Volatility Lab environment...\n")
    check_python_version()
    for name in REQUIRED_IMPORTS:
        ensure_import(name)
    ensure_paths()
    print("\nEnvironment looks ready. Use one of:")
    print("  python run_local.py --dashboard")
    print("  python run_local.py --web")
    print("  python run_local.py --both")


if __name__ == "__main__":
    main()
