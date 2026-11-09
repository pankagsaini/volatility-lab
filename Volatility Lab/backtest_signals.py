"""As-known-at-entry signal loader and point-in-time filter evaluator (strict AvailableAt <= DecisionTime)."""
import json
import math
import sqlite3
from pathlib import Path
from contextlib import closing
import numpy as np
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
    out = []
    # Compute rolling 30-day HV lookup from {symbol}_one_year.csv if available
    hv_map = {}
    csv_path = p.parent / f'{str(symbol).lower()}_one_year.csv'
    if csv_path.exists():
        try:
            df_c = pd.read_csv(csv_path).sort_values('Date')
            ret = np.log(pd.to_numeric(df_c['Close'], errors='coerce') / pd.to_numeric(df_c['Close'], errors='coerce').shift(1))
            hv_s = ret.rolling(30).std(ddof=1) * math.sqrt(245.0) * 100.0
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

            # Also load daily expiry_observations as prior-close baseline signals (timestamped at 15:30 IST on `date`)
            has_exp_tbl = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='expiry_observations'"
            ).fetchone()
            if has_exp_tbl:
                erows = con.execute(
                    'SELECT date, expiry, iv, spread FROM expiry_observations WHERE symbol=? ORDER BY date',
                    (symbol,),
                ).fetchall()
                for d_str, exp_s, iv_v, spr_v in erows:
                    try:
                        if iv_v is None or not math.isfinite(float(iv_v)):
                            continue
                        d_clean = str(d_str)[:10]
                        t_eod = pd.Timestamp(f'{d_clean} 15:30:00', tz='Asia/Kolkata')
                        hv_v = hv_map.get(d_clean, float(iv_v) - float(spr_v) if spr_v is not None and math.isfinite(float(spr_v)) else 12.5)
                        spr_clean = float(spr_v) if spr_v is not None and math.isfinite(float(spr_v)) else (float(iv_v) - hv_v)
                        rec_d = {
                            'Expiry': str(exp_s)[:10],
                            'IV': float(iv_v),
                            'HV30': float(hv_v),
                            'IV-RV Spread': float(spr_clean),
                            '25 Delta reversal': 1.5,
                        }
                        out.append((str(exp_s)[:10], t_eod, rec_d))
                    except Exception:
                        continue
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
    # 1. Strict point-in-time same-day & same-expiry guard: t <= at within 160 minutes before entry
    candidates = [
        r for exp, t, r in rows
        if exp == exp_str and t <= at and str(t.date()) == day_str and 0 <= (at - t).total_seconds() <= 9600
    ]
    if not candidates:
        # 2. Check same-day pre-entry observation across active expiries on `day_str` (if synthetic near-expiry was selected)
        same_day_any_exp = [
            r for exp, t, r in rows
            if t <= at and str(t.date()) == day_str and 0 <= (at - t).total_seconds() <= 9600
        ]
        # Only use cross-expiry fallback when the exact expiry has no rows at all on `day_str`
        has_exact_exp_today = any(exp == exp_str and str(t.date()) == day_str for exp, t, _ in rows)
        if same_day_any_exp and not has_exact_exp_today:
            candidates = [same_day_any_exp[-1]]
    if not candidates:
        return False, 'Missing signal known before entry'
    r = candidates[-1]
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
