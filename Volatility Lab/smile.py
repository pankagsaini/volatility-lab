"""Five-point spot-delta smile, without extrapolation or invented quotes."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import numpy as np
import pandas as pd
from option_math import delta_iv, delta_iv_details

VERSION = 'spot-delta-smile-v1'


def has_usable_points(result):
    if not result or not isinstance(result, dict):
        return False
    for point in result.get('points', []):
        try:
            if point.get('IV') is not None and np.isfinite(float(point['IV'])):
                return True
        except (TypeError, ValueError):
            continue
    return False


def smile_points(chain, strikes, spot):
    chain = chain.copy()
    chain['iv'] = pd.to_numeric(chain.iv, errors='coerce').astype(float)
    chain['delta'] = pd.to_numeric(chain.delta, errors='coerce').astype(float)
    if 'strike' in chain.columns:
        chain['strike'] = pd.to_numeric(chain.strike, errors='coerce').astype(float)
    points = []
    for label, kind, target in [('10P', 'PE', .10), ('25P', 'PE', .25), ('ATM', None, None),
                                ('25C', 'CE', .25), ('10C', 'CE', .10)]:
        value = np.nan
        contributors = chain.iloc[:0]
        method = 'Unavailable'
        if kind:
            iv_dec, method, contributors = delta_iv_details(chain, kind, target)
            value = iv_dec * 100.0 if np.isfinite(iv_dec) else np.nan
        else:
            valid = chain[np.isfinite(chain.iv) & (chain.iv > 0) & chain.option_type.isin(['CE', 'PE'])]
            if not valid.empty and spot is not None and np.isfinite(spot):
                pairs = valid.groupby('strike').option_type.agg(lambda kinds: set(kinds))
                paired = [float(strike) for strike, kinds in pairs.items() if kinds == {'CE', 'PE'}]
                if paired:
                    strike = min(paired, key=lambda k: (abs(k - spot), k))
                    contributors = valid[valid.strike == strike]
                    if len(contributors) >= 2:
                        value = float(contributors.iv.mean() * 100)
                        method = 'Same-strike call/put average'
        sources = sorted(set(contributors.source)) if len(contributors) and 'source' in contributors.columns else []
        strike_list = [str(v) for v in sorted(set(contributors.strike))] if len(contributors) and 'strike' in contributors.columns else []
        points.append({
            'Point': label,
            'IV': float(value) if np.isfinite(value) else None,
            'Source': ', '.join(sources) if sources else 'Unavailable',
            'Method': method,
            'Strikes': ', '.join(strike_list) if strike_list else 'Unavailable',
        })
    vals = {p['Point']: p['IV'] for p in points}
    metrics = {}
    for delta in (10, 25):
        p, c, atm = vals[f'{delta}P'], vals[f'{delta}C'], vals['ATM']
        diff = p - c if p is not None and c is not None else None
        fly = (p + c) / 2 - atm if all(v is not None for v in (p, c, atm)) else None
        metrics[f'{delta} RR · put minus call'] = diff
        metrics[f'RR{delta} · put minus call'] = diff
        metrics[f'{delta} BF · wings minus ATM'] = fly
        metrics[f'BF{delta} · wings minus ATM'] = fly
    return {'version': VERSION, 'points': points, 'metrics': metrics}


def save_smile(path, symbol, expiry, config, as_of, result, capture_source=None):
    if capture_source != 'Google Drive':
        return
    path = Path(path)
    config_str = json.dumps(config, sort_keys=True) if isinstance(config, (dict, list)) else str(config or '')
    with closing(sqlite3.connect(path, timeout=30)) as con, con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS smile_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, version TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of,version))')
        con.execute('INSERT OR REPLACE INTO smile_observations VALUES (?,?,?,?,?,?)',
                    (symbol, str(expiry), config_str, str(as_of), VERSION, json.dumps(result, allow_nan=False)))


def load_smile(path, symbol, expiry, config, as_of):
    path = Path(path)
    if not path.exists() or not expiry or not as_of:
        return None
    with closing(sqlite3.connect(path, timeout=15)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smile_observations'").fetchone():
            return None
        row = None
        if isinstance(config, str) and config:
            try:
                row = con.execute(
                    'SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=?',
                    (symbol, str(expiry), config, str(as_of)),
                ).fetchone()
            except sqlite3.ProgrammingError:
                row = None
        if not row:
            # Fallback across config hash revisions for the exact same (symbol, expiry, as_of)
            row = con.execute(
                'SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND as_of=? ORDER BY rowid DESC LIMIT 1',
                (symbol, str(expiry), str(as_of)),
            ).fetchone()
    return json.loads(row[0]) if row else None


def load_latest_smile(path, symbol, expiry, config=None):
    """Return the most recent usable smile capture for (symbol, expiry), attaching its `as_of` timestamp."""
    path = Path(path)
    if not path.exists() or not expiry:
        return None
    with closing(sqlite3.connect(path, timeout=15)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smile_observations'").fetchone():
            return None
        if isinstance(config, str) and config:
            rows = con.execute(
                'SELECT as_of, payload FROM smile_observations WHERE symbol=? AND expiry=? AND config=? ORDER BY as_of DESC LIMIT 25',
                (symbol, str(expiry), config),
            ).fetchall()
        else:
            rows = []
        if not rows:
            rows = con.execute(
                'SELECT as_of, payload FROM smile_observations WHERE symbol=? AND expiry=? ORDER BY as_of DESC LIMIT 25',
                (symbol, str(expiry)),
            ).fetchall()
    for as_of_val, payload_str in rows:
        try:
            parsed = json.loads(payload_str)
            if has_usable_points(parsed):
                parsed['as_of'] = as_of_val
                return parsed
        except Exception:
            continue
    return None
