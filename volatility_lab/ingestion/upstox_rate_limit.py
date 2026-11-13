"""Shared cross-process pacing for read-only Upstox API requests."""
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
import sqlite3
import time

ROOT = PACKAGE_ROOT / 'data' / 'upstox'
DB = ROOT / 'request_pacing.sqlite'
MIN_INTERVAL = 1.05  # ~1,715 requests per 30 minutes; below Upstox's 2,000 cap.


def wait_for_slot():
    """Reserve exactly one globally spaced slot, wait for it, then send the request."""
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30, isolation_level=None)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS pacing(id INTEGER PRIMARY KEY CHECK(id=1), last_slot REAL NOT NULL)')
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT last_slot FROM pacing WHERE id=1').fetchone()
        now = time.time()
        slot = max(now, (row[0] + MIN_INTERVAL) if row else now)
        con.execute('INSERT OR REPLACE INTO pacing(id,last_slot) VALUES(1,?)', (slot,))
        con.commit()
    finally:
        con.close()
    delay = slot - time.time()
    if delay > 0:
        time.sleep(delay)
    # Do not reserve again after sleeping: concurrent callers would keep moving
    # the deadline forward and never reach the broker.
