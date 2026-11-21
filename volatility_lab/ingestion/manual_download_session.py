"""Download broker historical minute candles without reconstructing live captures."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab.ingestion.upstox_data import saved_client, UpstoxDataError as MarketDataError
from datetime import date, datetime, timedelta
from contextlib import closing
from pathlib import Path
from urllib.error import URLError, HTTPError
import argparse
import gzip
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

DATA = runtime_data_dir()
IST = ZoneInfo('Asia/Kolkata')


def save_to_research_db(instrument: str, candles: list):
    """Store 1-minute bars into local SQLite research cache."""
    db_path = DATA / 'upstox' / 'research.sqlite'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as con, con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute(
            'CREATE TABLE IF NOT EXISTS candles(instrument TEXT, interval TEXT, stamp TEXT, '
            'open REAL, high REAL, low REAL, close REAL, volume REAL, oi REAL, '
            'PRIMARY KEY(instrument, interval, stamp))'
        )
        rows = []
        for c in candles:
            if len(c) >= 6:
                stamp = str(c[0])
                vals = [float(x) for x in c[1:6]]
                oi = float(c[6]) if len(c) > 6 and c[6] is not None else None
                rows.append((instrument, '1minute', stamp, *vals, oi))
        con.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?)', rows)
        con.commit()
    return len(rows)


def download_session(target_day: str | None = None, verbose: bool = True):
    """Execute manual 1-minute download and synchronization for a specified session date."""
    day = date.fromisoformat(target_day or str((datetime.now(IST) - timedelta(days=1)).date())).isoformat()
    if verbose:
        print(f"=== Volatility Lab: Manual 1-Minute Session Downloader ===")
        print(f"Target Session Date: {day}")

    minute_bars_count = 0
    downloaded_status = 'error'
    try:
        broker = saved_client()
        successes = 0
        for instrument in ('NSE_INDEX|Nifty 50', 'NSE_INDEX|Nifty Bank', 'NSE_INDEX|India VIX'):
            try:
                rows = broker.minute_candles(instrument, day).get('candles', [])
                if rows:
                    minute_bars_count += save_to_research_db(instrument, rows)
                successes += 1
            except (MarketDataError, ValueError, OSError):
                continue
        if minute_bars_count:
            downloaded_status = 'partial'  # Index candles only.
        elif successes == 3:
            downloaded_status = 'empty'
    except (MarketDataError, OSError, ValueError):
        pass

    # 3. Synchronize local monitor sqlite from Google Drive archive
    if verbose:
        print("\nSynchronizing monitor.sqlite with latest Drive snapshots...")
    sync_status, imported = 'error', 0
    try:
        import google_archive_sync
        imported = google_archive_sync.sync_once(None, None, 3.0)
        sync_status = 'completed'
        if verbose:
            print(f"Google Archive Sync completed: {imported} records imported.")
    except Exception as exc:
        if verbose:
            print(f"Sync warning: {type(exc).__name__}")

    summary = {
        "status": downloaded_status,
        "day": day,
        "minute_bars": minute_bars_count,
        "saved_snapshots": 0,
        "archive_sync": {"status": sync_status, "imported": imported},
        "timestamp": datetime.now(IST).isoformat()
    }
    if verbose:
        print(f"\nDownload result: {summary}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Broker historical minute-candle downloader.")
    parser.add_argument("--day", type=str, default=None, help="Session date in YYYY-MM-DD format (defaults to yesterday)")
    args = parser.parse_args()
    result = download_session(args.day)
    return 0 if result['status'] == 'available' and result['archive_sync']['status'] == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
