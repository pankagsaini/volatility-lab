"""SQLite WAL store and strictly prior DTE-matched & maturity-bucketed relative value diagnostics."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import numpy as np
import pandas as pd
from expiry_forecast import expiry_forecast, eligible_history


def _mid_rank_percentile(values, current):
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0 or not np.isfinite(current):
        return np.nan
    return float(100.0 * ((arr < current).sum() + 0.5 * (arr == current).sum()) / len(arr))


def connection(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30.0)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.execute(
        'CREATE TABLE IF NOT EXISTS intraday_observations ('
        'symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT, '
        'PRIMARY KEY(symbol, expiry, config, as_of))'
    )
    return con


def save(path, record):
    with closing(connection(path)) as con, con:
        con.execute(
            'INSERT OR REPLACE INTO intraday_observations VALUES (?, ?, ?, ?, ?)',
            (
                str(record['Instrument']),
                str(record['Expiry']),
                str(record.get('Config', 'default')),
                str(record['As of']),
                json.dumps(record, allow_nan=True),
            ),
        )


def load_history(path, symbol=None):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    with closing(connection(path)) as con:
        if symbol:
            rows = con.execute('SELECT payload FROM intraday_observations WHERE symbol=? ORDER BY as_of', (symbol,)).fetchall()
        else:
            rows = con.execute('SELECT payload FROM intraday_observations ORDER BY as_of').fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([json.loads(r[0]) for r in rows])
    return eligible_history(frame)


def observe(quote, history_frame=None):
    """Build an observation record and attach matched expiry RV forecasts when eligible."""
    record = dict(quote)
    if history_frame is not None and not getattr(history_frame, 'empty', True):
        try:
            matched = expiry_forecast(
                history_frame,
                quote['Expiry'],
                quote['As of'],
                quote.get('IV'),
            )
            record['GARCH expiry RV'] = matched.get('GARCH expiry RV')
            record['EWMA expiry RV'] = matched.get('EWMA expiry RV')
            record['IV - GARCH'] = matched.get('IV - GARCH')
            record['IV - EWMA'] = matched.get('IV - EWMA')
            record['Forecast'] = matched.get('GARCH expiry RV')
            record['Forecast status'] = matched.get('GARCH status', 'Ready')
        except (OSError, ValueError, KeyError, TypeError) as err:
            record['Forecast status'] = str(err)
    return record


def comparable_stats(history, current, dte_tol=3.0, min_obs=20):
    if history.empty or 'IV' not in history or 'DTE' not in history:
        return {'status': 'Insufficient history', 'percentile': np.nan, 'z_score': np.nan, 'count': 0}
    cur_as_of = pd.Timestamp(current['As of'])
    cur_dte = float(current['DTE'])
    cur_iv = float(current['IV'])
    df = history.copy()
    df['ts'] = pd.to_datetime(df['As of'], format='ISO8601', utc=True)
    df['numeric_dte'] = pd.to_numeric(df['DTE'], errors='coerce')
    df['numeric_iv'] = pd.to_numeric(df['IV'], errors='coerce')
    start = cur_as_of - pd.Timedelta(days=365)
    prior = df[(df['ts'] < cur_as_of) & (df['ts'] >= start) & (df['numeric_dte'].sub(cur_dte).abs() <= dte_tol)]
    vals = prior['numeric_iv'].dropna().to_numpy()
    if len(vals) < min_obs:
        return {'status': 'Insufficient comparable prior observations', 'percentile': np.nan, 'z_score': np.nan, 'count': len(vals)}
    std = float(np.std(vals, ddof=1))
    z = float((cur_iv - np.mean(vals)) / std) if std > 1e-8 else 0.0
    return {
        'status': 'Ready',
        'percentile': _mid_rank_percentile(vals, cur_iv),
        'z_score': z,
        'count': int(len(vals)),
    }


def maturity_stats(history, current, bounds=(0, 7, 15, 35, 65, 120, 366), min_expiries=12):
    if history.empty or 'IV' not in history or 'DTE' not in history:
        return {'status': 'Insufficient history', 'percentile': np.nan, 'count': 0}
    cur_dte = float(current['DTE'])
    cur_iv = float(current['IV'])
    data = history.copy()
    data['numeric_dte'] = pd.to_numeric(data['DTE'], errors='coerce')
    data['numeric_iv'] = pd.to_numeric(data['IV'], errors='coerce')
    data['bucket'] = pd.cut(data['numeric_dte'], bins=bounds, right=False, labels=False)
    cur_bucket = pd.cut(pd.Series([cur_dte]), bins=bounds, right=False, labels=False).iloc[0]
    if pd.isna(cur_bucket):
        return {'status': 'DTE outside maturity buckets', 'percentile': np.nan, 'count': 0}
    sub = data[data['bucket'] == cur_bucket].dropna(subset=['numeric_iv'])
    if sub['Expiry'].nunique() < min_expiries:
        return {'status': f'Need at least {min_expiries} distinct expiries in bucket', 'percentile': np.nan, 'count': len(sub)}
    vals = sub['numeric_iv'].to_numpy()
    return {
        'status': 'Ready',
        'percentile': _mid_rank_percentile(vals, cur_iv),
        'count': int(len(vals)),
    }
