"""As-known-at-entry signal loader and point-in-time filter evaluator (strict AvailableAt <= DecisionTime)."""
from volatility_lab import PACKAGE_ROOT
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
import numpy as np
import pandas as pd
try:
    from market_calendar import last_trading_day_on_or_before
except ModuleNotFoundError:
    from .market_calendar import last_trading_day_on_or_before

ROOT = PACKAGE_ROOT
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
    out = []
    # Compute rolling 30-day HV lookup from {symbol}_one_year.csv strictly shifted by 1 session (prior completed close)
    hv_map = {}
    csv_path = p.parent / f'{str(symbol).lower()}_one_year.csv'
    if csv_path.exists():
        try:
            df_c = pd.read_csv(csv_path).sort_values('Date')
            closes = pd.to_numeric(df_c['Close'], errors='coerce')
            ret = np.log(closes / closes.shift(1))
            hv_s = (ret.rolling(30).std(ddof=1) * math.sqrt(245.0) * 100.0).shift(1)
            for d_val, hv_val in zip(df_c['Date'], hv_s):
                if pd.notna(hv_val) and math.isfinite(float(hv_val)):
                    hv_map[str(d_val)[:10]] = float(hv_val)
        except Exception:
            pass

    if not p.exists():
        return out
    try:
        with closing(sqlite3.connect(p, timeout=15)) as con:
            has_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'"
            ).fetchone()
            rows = []
            if has_table:
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
                day_key = str(t.date())
                if r.get('HV30') is None and day_key in hv_map:
                    r['HV30'] = hv_map[day_key]
                out.append((str(r.get('Expiry') or exp)[:10], t, r))

            # expiry_observations stores a session date but no AvailableAt timestamp.
            # Do not fabricate a 15:30 availability time; only timestamped snapshots qualify.
    except Exception:
        return out
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
    try:
        previous_session = last_trading_day_on_or_before(
            pd.Timestamp(day_str).date() - pd.Timedelta(days=1)
        )
    except ValueError:
        previous_session = None

    # Prefer a same-day observation; otherwise allow only the prior session's EOD record.
    candidates = [
        (t, r) for exp, t, r in rows
        if exp == exp_str and t <= at and (
            (t.date().isoformat() == day_str and 0 <= (at - t).total_seconds() <= 9600)
            or (previous_session is not None and t.date() == previous_session and t.time() >= pd.Timestamp('15:30').time())
        )
    ]
    if not candidates:
        return False, 'Missing signal known before entry'
    _, r = max(candidates, key=lambda item: item[0])
    for metric, low, high in filters:
        canon = _METRIC_ALIASES.get(metric, metric)
        v = r.get(metric, r.get(canon))
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False, 'Missing ' + metric
        if not pd.notna(v) or not math.isfinite(v):
            return False, 'Missing ' + metric
        if not low <= v <= high:
            return False, metric + ' outside entry range'
    return True, 'Signal conditions met'
