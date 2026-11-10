"""Streamlit Quant Desk – Local Server Launcher.

Launches the encapsulated Streamlit desktop application on localhost with explicit
port mapping and clean terminal feedback.

Usage:
    python streamlit_desk/run.py
    python streamlit_desk/run.py --port 8502
"""
import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

DESK_DIR = Path(__file__).resolve().parent
ROOT = DESK_DIR.parent
APP_ENTRY = DESK_DIR / "app.py"
PORTAL_URL = "https://volatilitylab.in"


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch Volatility Lab Streamlit Desk.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8501)), help="Port to bind (default: 8501)")
    args = parser.parse_args()

    lan_ip = detect_lan_ip()
    local_url = f"http://localhost:{args.port}"
    lan_url = f"http://{lan_ip}:{args.port}"

    print("=" * 72)
    print("  ❖ VOLATILITY LAB – STREAMLIT QUANT DESK")
    print("=" * 72)
    print(f"  • Primary Web Portal (Production):   {PORTAL_URL}")
    print(f"  • Local Desktop Browser:             {local_url}")
    print(f"  • Mobile / Tablet (Wi-Fi LAN):       {lan_url}")
    print("=" * 72)
    print("Starting Streamlit server... Press Ctrl+C to stop.\n")

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_ENTRY),
        "--server.headless",
        "true",
        "--server.address",
        "0.0.0.0",
        "--server.port",
        str(args.port),
    ]

    try:
        subprocess.run(cmd, cwd=str(ROOT))
    except KeyboardInterrupt:
        print("\nStreamlit server stopped.")


if __name__ == "__main__":
    main()
