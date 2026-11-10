#!/usr/bin/env python3
"""Market Data & Snapshot Hydration Utility.

Downloads or seeds local SQLite databases and active option quote JSONs
without requiring multi-megabyte binary database files in the Git repository.

Usage:
    python scripts/fetch_market_data.py
    python scripts/fetch_market_data.py --status
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_DATA = ROOT / "volatility_lab" / "data"
ROOT_DATA = ROOT / "data"


def check_status() -> None:
    print("=" * 60)
    print("  * Volatility Lab - Market Data Status")
    print("=" * 60)
    db_path = LAB_DATA / "monitor.sqlite"
    if db_path.exists():
        sz_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"  - Local monitor.sqlite: {sz_mb:.1f} MB (Present)")
        try:
            with sqlite3.connect(db_path) as con:
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                print(f"  - SQLite Tables:        {', '.join(tables)}")
        except Exception as e:
            print(f"  - SQLite Read Error:    {e}")
    else:
        print("  - Local monitor.sqlite: Missing (Can be seeded or fetched)")

    exp_json = LAB_DATA / "expiry_latest.json"
    if exp_json.exists():
        try:
            obj = json.loads(exp_json.read_text(encoding="utf-8"))
            print(f"  - Active Snapshot:      {obj.get('timestamp', 'Unknown')}")
            print(f"  - Expiry Rows:          {len(obj.get('rows', []))}")
        except Exception:
            pass
    print("=" * 60)


def ensure_seed_database() -> None:
    """Ensure a working monitor.sqlite exists locally with correct schema."""
    db_path = LAB_DATA / "monitor.sqlite"
    if db_path.exists():
        print(f"Local database exists: {db_path}")
        return

    LAB_DATA.mkdir(parents=True, exist_ok=True)
    print(f"Initializing empty schema for {db_path}...")
    with sqlite3.connect(db_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS intraday_observations (
                symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS expiry_observations (
                symbol TEXT, date TEXT, expiry TEXT, iv REAL, spread REAL, source TEXT, config TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS smile_observations (
                symbol TEXT, expiry TEXT, strike REAL, iv REAL, as_of TEXT
            )
        """)
        con.commit()
    print("Initialized empty monitor.sqlite schema successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Volatility Lab Data Hydration")
    parser.add_argument("--status", action="store_true", help="Print data status and exit")
    parser.add_argument("--seed", action="store_true", help="Ensure local SQLite schema exists")
    args = parser.parse_args()

    if args.status:
        check_status()
        return

    ensure_seed_database()
    check_status()


if __name__ == "__main__":
    main()
