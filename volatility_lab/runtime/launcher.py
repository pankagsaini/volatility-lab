"""Canonical launcher for an installed package or editable checkout."""
import argparse
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='Launch the Volatility Lab analytical desk.')
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--lan', action='store_true', help='Explicitly allow other devices on the local network')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    command = [sys.executable, '-m', 'streamlit', 'run', str(Path(__file__).with_name('streamlit_entry.py')),
               '--server.headless=true', '--server.address=' + ('0.0.0.0' if args.lan else '127.0.0.1'),
               '--server.port=' + str(args.port)]
    print(f'Volatility Lab: http://localhost:{args.port}')
    try:
        return subprocess.run(command).returncode
    except KeyboardInterrupt:
        return 130
