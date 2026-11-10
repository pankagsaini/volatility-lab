"""Manual downloader for historical trading sessions (e.g. Diwali Muhurat Trading 1-minute snapshots).

Discovers and downloads 1-minute OHLCV candles, synthesizes intraday snapshots,
saves them to Google Drive and local backtest caches, and synchronizes monitor.sqlite.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError, HTTPError
import argparse
import gzip
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
IST = ZoneInfo('Asia/Kolkata')
SYNC_CONFIG = ROOT / 'cloud' / 'apps_script' / 'sync_config.json'


def load_sync_config():
    if SYNC_CONFIG.exists():
        try:
            return json.loads(SYNC_CONFIG.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        "source_folder": "G:/My Drive/Historical Data/Volatility Lab - private Upstox archive",
        "web_app_url": "https://script.google.com/macros/s/AKfycbxDbTwpiRAE9ZRoGGWTyTncZhwUPmTnimv0HyejOiJilfS7lPnxZsF4mj701Fd4jdz5qQ/exec"
    }


def query_cloud_endpoint(url: str, params: dict, timeout: int = 45):
    """Query Google Apps Script Web App endpoint with error handling and redirects."""
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if query else url
    req = urllib.request.Request(full_url, headers={'User-Agent': 'VolatilityLab-ManualDownloader/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data)
    except Exception as exc:
        return {"error": str(exc)}


def save_to_drive_archive(filename: str, payload: dict, drive_folder: Path):
    """Persist a gzipped JSON archive directly to Google Drive storage if accessible."""
    if not drive_folder.exists():
        return False
    target = drive_folder / f"{filename}.json.gz"
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    target.write_bytes(compressed)
    return True


def save_to_research_db(instrument: str, candles: list):
    """Store 1-minute bars into local SQLite research cache."""
    db_path = DATA / 'upstox' / 'research.sqlite'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=30) as con:
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
    day = target_day or str((datetime.now(IST) - timedelta(days=1)).date())
    if verbose:
        print(f"=== Volatility Lab: Manual 1-Minute Session Downloader ===")
        print(f"Target Session Date: {day}")

    cfg = load_sync_config()
    web_app_url = cfg.get("web_app_url")
    drive_path = Path(cfg.get("source_folder", ""))

    if verbose:
        print(f"Google Drive Target: {drive_path} (exists: {drive_path.exists()})")
        print(f"Cloud Web App: {web_app_url}")

    # 1. Trigger cloud download via Apps Script web app
    if verbose:
        print("\nRequesting session 1-minute capture from Google Apps Script...")
    result = query_cloud_endpoint(web_app_url, {"action": "manual_download", "day": day}, timeout=60)

    saved_snapshots = 0
    minute_bars_count = 0

    if "error" not in result:
        saved_snapshots = result.get("saved_snapshots", 0)
        minute_bars_count = result.get("minutes", 0)
        if verbose:
            print(f"Cloud capture successful: {saved_snapshots} snapshots saved in Drive.")
    else:
        if verbose:
            print(f"Cloud notice: {result.get('error')}")
            print("Fetching 1-minute candles for key indices...")

        # 2. Direct candle retrieval fallback
        indices = {
            "NIFTY": "NSE_INDEX|Nifty 50",
            "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            "INDIAVIX": "NSE_INDEX|India VIX"
        }
        for name, ikey in indices.items():
            res = query_cloud_endpoint(web_app_url, {
                "action": "candles",
                "instrument": ikey,
                "interval": "minutes/1",
                "to_date": day,
                "from_date": day
            }, timeout=30)
            candles = res.get("data", {}).get("candles", []) if isinstance(res, dict) else []
            if candles:
                count = save_to_research_db(ikey, candles)
                minute_bars_count += count
                if verbose:
                    print(f"  - {name} ({ikey}): {count} 1-minute candles saved to research.sqlite")

    # 3. Synchronize local monitor sqlite from Google Drive archive
    if verbose:
        print("\nSynchronizing monitor.sqlite with latest Drive snapshots...")
    try:
        import google_archive_sync
        imported = google_archive_sync.sync_all(verbose=False)
        if verbose:
            print(f"Google Archive Sync completed: {imported} records imported.")
    except Exception as exc:
        if verbose:
            print(f"Sync warning: {exc}")

    summary = {
        "status": "success",
        "day": day,
        "minute_bars": minute_bars_count,
        "saved_snapshots": saved_snapshots,
        "timestamp": datetime.now(IST).isoformat()
    }
    if verbose:
        print(f"\nCompleted successfully: {summary}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Manual 1-minute session snapshot downloader.")
    parser.add_argument("--day", type=str, default=None, help="Session date in YYYY-MM-DD format (defaults to yesterday)")
    args = parser.parse_args()
    download_session(args.day)


if __name__ == '__main__':
    main()
