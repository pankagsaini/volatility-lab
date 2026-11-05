"""Convenient cross-platform local launcher for Volatility Lab.

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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "Volatility Lab" / "dashboard.py"
WEB_SERVER = ROOT / "Volatility Lab" / "web_server.py"
VALIDATE = ROOT / "scripts" / "validate_setup.py"


def ensure_ready() -> None:
    if not VALIDATE.is_file():
        raise SystemExit(f"Setup validator is missing: {VALIDATE}")
    subprocess.run([sys.executable, str(VALIDATE)], cwd=ROOT, check=True)


def command_for(target: str) -> list[str]:
    if target == "dashboard":
        entry = ROOT / "Volatility Lab" / "dashboard.py"
        return [sys.executable, "-m", "streamlit", "run", str(entry)]
    if target == "web":
        return [sys.executable, str(WEB_SERVER)]
    raise ValueError(f"Unknown app target: {target}")


def run_one(target: str) -> int:
    ensure_ready()
    return subprocess.call(command_for(target), cwd=ROOT)


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_both() -> int:
    ensure_ready()
    processes: list[subprocess.Popen[bytes]] = []
    try:
        # Start web first so a startup error is reported promptly and dashboard
        # does not continue running alone if the backend cannot start.
        processes.append(subprocess.Popen(command_for("web"), cwd=ROOT))
        time.sleep(0.5)
        if processes[0].poll() is not None:
            return int(processes[0].returncode or 1)
        processes.append(subprocess.Popen(command_for("dashboard"), cwd=ROOT))
        while True:
            for proc in processes:
                code = proc.poll()
                if code is not None:
                    return int(code)
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in reversed(processes):
            stop_process(proc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Volatility Lab locally.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dashboard", action="store_true", help="Run the Streamlit dashboard")
    group.add_argument("--web", action="store_true", help="Run the FastAPI web workstation")
    group.add_argument("--both", action="store_true", help="Run both entry points; stop both when either exits")
    args = parser.parse_args()

    if args.dashboard:
        return run_one("dashboard")
    if args.web:
        return run_one("web")
    return run_both()


if __name__ == "__main__":
    raise SystemExit(main())
