VERSION = 'rv-v2-point-in-time'
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
    if len(arr) == 0 or current is None or not np.isfinite(float(current)):
        return np.nan
    cur = float(current)
    return float(100.0 * ((arr < cur).sum() + 0.5 * (arr == cur).sum()) / len(arr))


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


def load_history(path, symbol=None, max_rows=2500):
    from datetime import date as _date
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    with closing(connection(path)) as con:
        if symbol:
            rows = con.execute(
                'SELECT expiry, config, MAX(as_of) AS as_of, payload '
                'FROM intraday_observations WHERE symbol=? '
                'GROUP BY expiry, substr(as_of, 1, 10) '
                'ORDER BY as_of DESC LIMIT ?',
                (symbol, int(max_rows)),
            ).fetchall()
        else:
            rows = con.execute(
                'SELECT expiry, config, MAX(as_of) AS as_of, payload '
                'FROM intraday_observations '
                'GROUP BY symbol, expiry, substr(as_of, 1, 10) '
                'ORDER BY as_of DESC LIMIT ?',
                (int(max_rows),),
            ).fetchall()
    if not rows:
        return pd.DataFrame()
    rows.reverse()
    records = []
    for exp, cfg, as_of, payload_str in rows:
        try:
            item = json.loads(payload_str)
            item.setdefault('Expiry', exp)
            item.setdefault('Config', cfg)
            item.setdefault('As of', as_of)
            if 'DTE' not in item or item.get('DTE') is None:
                try:
                    d_exp = _date.fromisoformat(str(item['Expiry'])[:10])
                    d_asof = _date.fromisoformat(str(item['As of'])[:10])
                    item['DTE'] = (d_exp - d_asof).days
                except Exception:
                    pass
            records.append(item)
        except Exception:
            continue
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    return eligible_history(frame)


def history(path, symbol=None, config=None, comparable=True):
    """Load intraday observations for `symbol` (compatible with monitor_ui.py and trade_review_ui.py)."""
    db_path = Path(path) / 'monitor.sqlite' if Path(path).is_dir() else Path(path)
    frame = load_history(db_path, symbol=symbol)
    if frame.empty:
        return frame
    if comparable and 'Source' in frame.columns:
        src = frame['Source'].fillna('').astype(str)
        filtered = frame[src.str.startswith('Upstox supplied IV')].copy()
        if not filtered.empty:
            frame = filtered
    return frame.reset_index(drop=True)


def capture(root_or_path, snapshot):
    """Persist rows from a cloud/live snapshot into monitor.sqlite intraday_observations."""
    if not isinstance(snapshot, dict):
        return 0
    base = Path(root_or_path)
    db_path = base / 'monitor.sqlite' if base.is_dir() or base.suffix != '.sqlite' else base
    cfg = str(snapshot.get('config', 'default'))
    rows = snapshot.get('rows', [])
    saved_count = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get('Instrument') or not row.get('Expiry') or not row.get('As of'):
            continue
        rec = dict(row)
        rec.setdefault('Config', cfg)
        try:
            save(db_path, rec)
            saved_count += 1
        except Exception:
            continue
    return saved_count


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


def comparable_stats(history_df, current, dte_tol=3.0, min_obs=20):
    if history_df.empty or 'IV' not in history_df or 'DTE' not in history_df:
        return {'status': 'Insufficient history', 'percentile': np.nan, 'z_score': np.nan, 'count': 0}
    cur_as_of = pd.Timestamp(current['As of'])
    cur_dte = float(current['DTE'])
    cur_iv = float(current['IV'])
    df = history_df.copy()
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


def maturity_stats(history_df, current, bounds=(0, 7, 15, 35, 65, 120, 366), min_expiries=12):
    """Dual-mode maturity ranking supporting both UI metric name (`'IV'`, `'IV-RV Spread'`, `'25 Delta reversal'`)
    and explicit maturity bucket tuple `bounds=(0, 7, 15, ...)`."""
    metric = 'IV'
    if isinstance(bounds, str):
        metric = bounds
        bounds = (0, 7, 15, 35, 65, 120, 366)

    empty_res = {
        'status': 'Insufficient history',
        'percentile': np.nan,
        'count': 0,
        'Percentile': None,
        'Prior days': 0,
        'Expiry cycles': 0,
        'Status': 'Insufficient history',
    }
    if history_df is None or history_df.empty or metric not in history_df or 'DTE' not in history_df:
        return empty_res

    try:
        cur_dte = float(current.get('DTE', 7.0))
        cur_val = float(current.get(metric))
    except (TypeError, ValueError):
        return empty_res
    if not (np.isfinite(cur_dte) and np.isfinite(cur_val)):
        return empty_res

    data = history_df.copy()
    data['numeric_dte'] = pd.to_numeric(data['DTE'], errors='coerce')
    data['numeric_val'] = pd.to_numeric(data[metric], errors='coerce')
    data['bucket'] = pd.cut(data['numeric_dte'], bins=bounds, right=False, labels=False)
    cur_bucket = pd.cut(pd.Series([cur_dte]), bins=bounds, right=False, labels=False).iloc[0]
    if pd.isna(cur_bucket):
        return {**empty_res, 'status': 'DTE outside maturity buckets', 'Status': 'DTE outside maturity buckets'}

    sub = data[data['bucket'] == cur_bucket].dropna(subset=['numeric_val'])
    exp_cycles = int(sub['Expiry'].nunique()) if 'Expiry' in sub.columns else len(sub)
    prior_days = int(pd.to_datetime(sub['As of'], errors='coerce').dt.date.nunique()) if 'As of' in sub.columns else len(sub)
    vals = sub['numeric_val'].to_numpy()

    if exp_cycles < min_expiries:
        # Also compute DTE ±3 matched percentile if available so UI shows useful context when <12 cycles exist
        dte_match = data[(data['numeric_dte'].sub(cur_dte).abs() <= 3.0)].dropna(subset=['numeric_val'])
        dte_vals = dte_match['numeric_val'].to_numpy()
        pct_fallback = _mid_rank_percentile(dte_vals, cur_val) if len(dte_vals) >= 5 else np.nan
        msg = f'Need at least {min_expiries} distinct expiries in bucket'
        return {
            'status': msg,
            'percentile': np.nan,
            'count': len(sub),
            'Percentile': float(pct_fallback) if np.isfinite(pct_fallback) else None,
            'Prior days': prior_days,
            'Expiry cycles': exp_cycles,
            'Status': 'Ready (±3 DTE matched)' if np.isfinite(pct_fallback) else msg,
        }

    pct = _mid_rank_percentile(vals, cur_val)
    return {
        'status': 'Ready',
        'percentile': pct,
        'count': int(len(vals)),
        'Percentile': float(pct) if np.isfinite(pct) else None,
        'Prior days': prior_days,
        'Expiry cycles': exp_cycles,
        'Status': 'Ready',
    }


def compute_relative_value_spread(iv, forecast_rv):
    try:
        iv_f = float(iv)
        rv_f = float(forecast_rv)
        if np.isfinite(iv_f) and np.isfinite(rv_f) and iv_f > 0 and rv_f > 0:
            return iv_f - rv_f
    except (TypeError, ValueError):
        pass
    return np.nan