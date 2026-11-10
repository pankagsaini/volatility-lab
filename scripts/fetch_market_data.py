#!/usr/bin/env python3
"""Market Data Snapshot Verification & Local Database Hydration Utility.

Purpose & Operational Promise:
This utility verifies local market data files, ensures the SQLite database schema
is initialized, and hydrates local `monitor.sqlite` tables from committed Upstox
snapshots (`expiry_latest.json` and `google_archive/`) without requiring multi-megabyte
binary databases in Git.

Usage:
    python scripts/fetch_market_data.py           # Verify and hydrate local SQLite databases
    python scripts/fetch_market_data.py --status  # Print status of local databases & snapshots
    python scripts/fetch_market_data.py --seed    # Initialize schema and seed committed rows
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_DATA = ROOT / "volatility_lab" / "data"
ROOT_DATA = ROOT / "data"


def check_status() -> None:
    print("=" * 65)
    print("  * Volatility Lab - Market Data & Snapshot Status")
    print("=" * 65)
    db_path = LAB_DATA / "monitor.sqlite"
    if db_path.exists():
        sz_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"  - Local monitor.sqlite: {sz_mb:.2f} MB (Present)")
        try:
            with sqlite3.connect(db_path) as con:
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                print(f"  - SQLite Tables:        {', '.join(tables)}")
                for tbl in tables:
                    count = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    print(f"    * {tbl:24s}: {count:,} rows")
        except Exception as e:
            print(f"  - SQLite Read Error:    {e}")
    else:
        print("  - Local monitor.sqlite: Missing (Run with --seed to hydrate)")

    exp_json = LAB_DATA / "expiry_latest.json"
    if not exp_json.exists():
        exp_json = ROOT_DATA / "expiry_latest.json"
    if exp_json.exists():
        try:
            obj = json.loads(exp_json.read_text(encoding="utf-8"))
            print(f"  - Active Snapshot Date: {obj.get('day', obj.get('timestamp', 'Unknown'))}")
            print(f"  - Expiry Rows:          {len(obj.get('rows', []))}")
        except Exception:
            pass

    archive_dir = ROOT_DATA / "google_archive"
    if archive_dir.exists():
        gz_files = list(archive_dir.glob("*.json.gz"))
        print(f"  - Google Archive GZ:    {len(gz_files)} intraday snapshots available")

    print("=" * 65)


def hydrate_database() -> None:
    """Ensure a working monitor.sqlite exists locally and hydrate with committed snapshots."""
    LAB_DATA.mkdir(parents=True, exist_ok=True)
    db_path = LAB_DATA / "monitor.sqlite"

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

        # Seed expiry_observations from expiry_latest.json if empty
        cur = con.cursor()
        exp_count = cur.execute("SELECT COUNT(*) FROM expiry_observations").fetchone()[0]
        if exp_count == 0:
            exp_json = LAB_DATA / "expiry_latest.json"
            if not exp_json.exists():
                exp_json = ROOT_DATA / "expiry_latest.json"
            if exp_json.exists():
                try:
                    payload = json.loads(exp_json.read_text(encoding="utf-8"))
                    rows = payload.get("rows", [])
                    p_day = payload.get("day", "2026-11-09")
                    for r in rows:
                        cur.execute(
                            "INSERT INTO expiry_observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                r.get("Instrument", "NIFTY"),
                                p_day,
                                r.get("Expiry"),
                                float(r.get("IV", 0.0) or 0.0),
                                float(r.get("IV-RV Spread", 0.0) or 0.0),
                                "Upstox Snapshot (Hydrated)",
                                "v1",
                            ),
                        )
                    con.commit()
                    print(f"Hydrated expiry_observations with {len(rows)} rows from {exp_json.name}.")
                except Exception as e:
                    print(f"Notice: could not seed expiry_observations: {e}")

        # Seed intraday_observations from committed google_archive if empty
        intra_count = cur.execute("SELECT COUNT(*) FROM intraday_observations").fetchone()[0]
        if intra_count == 0:
            archive_dir = ROOT_DATA / "google_archive"
            gz_files = list(archive_dir.glob("*_complete.json.gz")) if archive_dir.exists() else []
            if gz_files:
                seeded_snaps = 0
                for gz_p in gz_files[:5]:
                    try:
                        raw = json.loads(gzip.decompress(gz_p.read_bytes()).decode("utf-8"))
                        as_of = raw.get("retrieved_at", "15:30 IST")
                        cur.execute(
                            "INSERT INTO intraday_observations VALUES (?, ?, ?, ?, ?)",
                            ("NIFTY", "2026-11-17", "default", as_of, json.dumps(raw)),
                        )
                        seeded_snaps += 1
                    except Exception:
                        pass
                con.commit()
                if seeded_snaps:
                    print(f"Hydrated intraday_observations with {seeded_snaps} snapshots from {archive_dir.name}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Volatility Lab Market Data Snapshot Verification & Local Hydration Utility"
    )
    parser.add_argument("--status", action="store_true", help="Print local market data status and exit")
    parser.add_argument("--seed", action="store_true", help="Initialize schema and seed from committed snapshots")
    args = parser.parse_args()

    if args.status:
        check_status()
        return

    hydrate_database()
    check_status()


if __name__ == "__main__":
    main()
