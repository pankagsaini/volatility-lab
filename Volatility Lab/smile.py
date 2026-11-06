"""Five-point spot-delta smile, without extrapolation or invented quotes."""
import json
import sqlite3
from contextlib import closing
import numpy as np
import pandas as pd
from option_math import delta_iv, delta_iv_details

VERSION = 'spot-delta-smile-v1'


def has_usable_points(result):
    for point in result.get('points', []):
        try:
            if point.get('IV') is not None and np.isfinite(float(point['IV'])):
                return True
        except (TypeError, ValueError):
            continue
    return False


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
    with closing(sqlite3.connect(path)) as con, con:
        con.execute('CREATE TABLE IF NOT EXISTS smile_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, version TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of,version))')
        con.execute('INSERT OR REPLACE INTO smile_observations VALUES (?,?,?,?,?,?)',
                    (symbol, str(expiry), config, str(as_of), VERSION, json.dumps(result, allow_nan=False)))


def load_smile(path, symbol, expiry, config, as_of):
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smile_observations'").fetchone():
            return None
        row = con.execute('SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=? AND version=?',
                          (symbol, str(expiry), config, str(as_of), VERSION)).fetchone()
        if not row:
            row = con.execute('SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND as_of=? AND version=?',
                              (symbol, str(expiry), str(as_of), VERSION)).fetchone()
    return json.loads(row[0]) if row else None


def load_latest_smile(path, symbol, expiry, config):
    """Load the newest usable Cloud smile for an expiry and include its timestamp.

    A smile uses broker IV/Greeks only, so it does not depend on pricing settings.
    """
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smile_observations'").fetchone():
            return None
        rows = con.execute('SELECT rowid, as_of, payload FROM smile_observations WHERE symbol=? AND expiry=? AND version=? ORDER BY rowid DESC',
                           (symbol, str(expiry), VERSION)).fetchall()
    valid = []
    for rowid, as_of, payload in rows:
        try:
            result = json.loads(payload)
            if not has_usable_points(result):
                continue
            stamp = pd.Timestamp(as_of)
            if pd.isna(stamp):
                continue
            valid.append((stamp.value, rowid, as_of, result))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    if not valid:
        return None
    _, _, as_of, result = max(valid, key=lambda item: (item[0], item[1]))
    return {**result, 'as_of': as_of}
