#!/usr/bin/env python3
"""Universal Any-Device Launcher for Volatility Lab (Local Wi-Fi LAN + Cloud URL).

Usage:
  python LAUNCH_ANY_DEVICE.py              # Starts Streamlit on 0.0.0.0:8501 and prints LAN URL + Cloud URL
  python LAUNCH_ANY_DEVICE.py --port 8501  # Custom port
"""
import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_ENTRY = ROOT / "streamlit_app.py" if (ROOT / "streamlit_app.py").exists() else ROOT / "Volatility Lab" / "dashboard.py"
DOMAIN_URL = "https://volatilitylab.in"
CLOUD_URL = "https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/"


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
    parser = argparse.ArgumentParser(description="Launch Volatility Lab accessible from any device.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8501)), help="Port to bind (default: 8501)")
    args = parser.parse_args()

    lan_ip = detect_lan_ip()
    local_url = f"http://localhost:{args.port}"
    lan_url = f"http://{lan_ip}:{args.port}"

    print("=" * 74)
    print("  ❖ VOLATILITY LAB — UNIVERSAL ANY-DEVICE SERVER")
    print("=" * 74)
    print(f"  • Official Domain & Web Portal:          {DOMAIN_URL}")
    print(f"  • Always-On Streamlit Cloud (24/7):      {CLOUD_URL}")
    print(f"  • Local PC Browser URL:                  {local_url}")
    print(f"  • Wi-Fi / LAN URL (Phones & Tablets):    {lan_url}")
    print("=" * 74)

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP_ENTRY),
        "--server.address=0.0.0.0",
        f"--server.port={args.port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    subprocess.run(cmd, cwd=str(ROOT), check=True)


if __name__ == "__main__":
    main()
