#!/usr/bin/env python3
"""Convenient local launcher for Volatility Lab.

Examples:
  python run_local.py --dashboard
  python run_local.py --web
  python run_local.py --both
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "Volatility Lab" / "dashboard.py"
WEB_SERVER = ROOT / "Volatility Lab" / "web_server.py"


def run_dashboard() -> int:
    cmd = [sys.executable, "-m", "streamlit", "run", str(DASHBOARD)]
    print(f"Starting dashboard: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT))


def run_web() -> int:
    cmd = [sys.executable, str(WEB_SERVER)]
    print(f"Starting web server: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT))


def run_both() -> int:
    dashboard = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(DASHBOARD)],
        cwd=str(ROOT),
    )
    web = subprocess.Popen(
        [sys.executable, str(WEB_SERVER)],
        cwd=str(ROOT),
    )

    def _stop(_signum, _frame):
        for proc in (dashboard, web):
            try:
                proc.terminate()
            except Exception:
                pass
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        dashboard.wait()
        web.wait()
        return 0
    except KeyboardInterrupt:
        print("\nStopping Volatility Lab processes...")
        for proc in (dashboard, web):
            try:
                proc.terminate()
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Volatility Lab locally.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dashboard", action="store_true", help="Run the Streamlit dashboard")
    group.add_argument("--web", action="store_true", help="Run the FastAPI web workstation")
    group.add_argument("--both", action="store_true", help="Run both dashboard and web server")
    args = parser.parse_args()

    if args.dashboard:
        raise SystemExit(run_dashboard())
    if args.web:
        raise SystemExit(run_web())
    raise SystemExit(run_both())
