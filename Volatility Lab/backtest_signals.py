"""As-known-at-entry signals. Never carry a later-retrieved IV backwards."""
import sqlite3, json
from contextlib import closing
from pathlib import Path
import pandas as pd

PATH = Path(__file__).resolve().parent / 'data/monitor.sqlite'


def load_signals(symbol):
    if not PATH.exists():
        return []
    try:
        with closing(sqlite3.connect(PATH, timeout=15)) as c:
            has_tbl = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'").fetchone()
            if not has_tbl:
                return []
            rows = c.execute(
                'SELECT expiry,as_of,payload FROM intraday_observations WHERE symbol=? ORDER BY as_of',
                (symbol,),
            ).fetchall()
    except Exception:
        return []
    out = []
    for exp, stamp, payload in rows:
        try:
            r = json.loads(payload)
            asof = pd.Timestamp(stamp)
            if asof.tzinfo is None:
                asof = asof.tz_localize('Asia/Kolkata')
            rec_raw = r.get('Retrieved at') or stamp
            received = pd.Timestamp(rec_raw)
            if received.tzinfo is None:
                received = received.tz_localize('Asia/Kolkata')
            available = max(asof, received).tz_convert('Asia/Kolkata')
            out.append((exp, available, r))
        except Exception:
            continue
    return sorted(out, key=lambda x: x[1])


def decide(rows, expiry, day, entry, filters):
    if not filters:
        return True, 'No signal filter required'
    at = pd.Timestamp(day + ' ' + entry, tz='Asia/Kolkata')
    candidates = [
        r for exp, t, r in rows
        if exp == expiry and t <= at and t.date() == at.date() and (at - t).total_seconds() <= 9600
    ]
    if not candidates:
        # Strict point-in-time guard: only consider captures strictly at or before entry
        candidates = [
            r for exp, t, r in rows
            if exp == expiry and t <= at and t.date() == at.date() and (at - t).total_seconds() <= 21600
        ]
    if not candidates:
        return False, 'Missing signal known before entry'
    r = candidates[-1]
    for metric, low, high in filters:
        v = r.get(metric)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False, 'Missing ' + metric
        if not pd.notna(v):
            return False, 'Missing ' + metric
        if not low <= v <= high:
            return False, metric + ' outside entry range'
    return True, 'Signal conditions met'
