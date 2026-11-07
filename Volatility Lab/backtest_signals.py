"""As-known-at-entry signal loader and point-in-time filter evaluator (strict AvailableAt <= DecisionTime)."""
from contextlib import closing
import json
import math
import sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'monitor.sqlite'
PATH = DB

_METRIC_ALIASES = {
    'iv_rv_spread': 'IV-RV Spread',
    'IV-RV Spread': 'IV-RV Spread',
    'skew_25d': '25 Delta reversal',
    '25 Delta reversal': '25 Delta reversal',
    'hv30': 'HV30',
    'HV30': 'HV30',
    'iv': 'IV',
    'IV': 'IV',
    'iv_percentile': 'IV Percentile',
    'term_slope': 'Next IV - IV',
    'straddle_vwap': 'Straddle VWAP',
}


def load_signals(symbol, path=None, start=None, end=None):
    p = Path(path or PATH)
    if not p.exists():
        return {}
    try:
        with closing(sqlite3.connect(p, timeout=15)) as con:
            has_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'"
            ).fetchone()
            if not has_table:
                return {}
            if start and end:
                rows = con.execute(
                    'SELECT expiry, as_of, payload FROM intraday_observations '
                    'WHERE symbol=? AND substr(as_of, 1, 10) >= ? AND substr(as_of, 1, 10) <= ? ORDER BY as_of',
                    (symbol, str(start)[:10], str(end)[:10]),
                ).fetchall()
            else:
                rows = con.execute(
                    'SELECT expiry, as_of, payload FROM intraday_observations WHERE symbol=? ORDER BY as_of',
                    (symbol,),
                ).fetchall()
    except Exception:
        return {}
    out = []
    for exp, as_of, payload in rows:
        try:
            r = json.loads(payload)
        except Exception:
            continue
        t1 = pd.to_datetime(r.get('As of') or as_of, errors='coerce', utc=True)
        t2 = pd.to_datetime(r.get('Retrieved at') or as_of, errors='coerce', utc=True)
        if pd.isna(t1) and pd.isna(t2):
            continue
        t = max(x for x in (t1, t2) if not pd.isna(x)).tz_convert('Asia/Kolkata')
        out.append((str(r.get('Expiry') or exp)[:10], t, r))
    out.sort(key=lambda x: x[1])
    return out


def decide(rows, expiry, day, entry, filters):
    if not filters:
        return True, 'No signal filter required'
    if not rows:
        return False, 'Missing signal known before entry'
    exp_str = str(expiry)[:10]
    day_str = str(day)[:10]
    at = pd.Timestamp(day_str + ' ' + str(entry), tz='Asia/Kolkata')
    # Strict point-in-time guard: t <= at on the same trading day within 160 minutes before entry
    candidates = [
        r for exp, t, r in rows
        if exp == exp_str and t <= at and str(t.date()) == day_str and 0 <= (at - t).total_seconds() <= 9600
    ]
    if not candidates:
        return False, 'Missing signal known before entry'
    r = candidates[-1]
    for metric, low, high in filters:
        canon = _METRIC_ALIASES.get(metric, metric)
        v = r.get(metric, r.get(canon))
        if v is None and canon == 'IV-RV Spread':
            iv_v = r.get('IV')
            hv_v = r.get('HV30')
            if iv_v is not None and hv_v is not None:
                try:
                    v = float(iv_v) - float(hv_v)
                except (TypeError, ValueError):
                    v = None
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False, 'Missing ' + metric
        if not math.isfinite(v):
            return False, 'Missing ' + metric
        if not (float(low) <= v <= float(high)):
            return False, metric + ' outside entry range'
    return True, 'Signal conditions met'
