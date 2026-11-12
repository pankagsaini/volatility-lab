"""Five-point spot-delta & Black-76 forward-delta smile, without extrapolation or invented quotes."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import numpy as np
import pandas as pd
from option_math import (
    black76_chain_greeks,
    delta_iv,
    delta_iv_details,
    parity_implied_forward,
)

VERSION = 'spot-delta-smile-v1'
BLACK76_VERSION = 'black76-delta-smile-v1'


def has_usable_points(result):
    if not isinstance(result, dict):
        return False
    for point in result.get('points', []):
        try:
            if point.get('IV') is not None and np.isfinite(float(point['IV'])):
                return True
        except (TypeError, ValueError):
            continue
    return False


def smile_points(chain, strikes, spot, forward=None, years=None, rate=0.0, use_black76=False):
    chain = chain.copy()
    if use_black76 and ('ltp' in chain.columns or 'Mid' in chain.columns or 'price' in chain.columns):
        t_years = float(years) if (years is not None and np.isfinite(years) and years > 0) else (7.0 / 365.0)
        enriched = black76_chain_greeks(chain, forward=forward, spot=spot, years=t_years, rate=float(rate or 0.0))
        if 'iv_b76' in enriched.columns and np.isfinite(enriched['iv_b76']).any():
            chain['iv'] = np.where(np.isfinite(enriched['iv_b76']) & (enriched['iv_b76'] > 0), enriched['iv_b76'], pd.to_numeric(chain.get('iv'), errors='coerce'))
            chain['delta'] = np.where(np.isfinite(enriched['delta_b76']), enriched['delta_b76'], pd.to_numeric(chain.get('delta'), errors='coerce'))
            if forward is None and 'forward' in enriched.columns and len(enriched):
                forward = float(enriched['forward'].iloc[0])

    chain['iv'] = pd.to_numeric(chain.iv, errors='coerce').astype(float)
    chain['delta'] = pd.to_numeric(chain.delta, errors='coerce').astype(float)
    if 'strike' in chain.columns:
        chain['strike'] = pd.to_numeric(chain.strike, errors='coerce').astype(float)

    ref_px = float(forward) if (forward is not None and np.isfinite(forward) and forward > 0) else spot
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
            if not valid.empty and ref_px is not None and np.isfinite(ref_px):
                pairs = valid.groupby('strike').option_type.agg(lambda kinds: set(kinds))
                paired = [float(strike) for strike, kinds in pairs.items() if kinds == {'CE', 'PE'}]
                if paired:
                    strike = min(paired, key=lambda k: (abs(k - ref_px), k))
                    contributors = valid[valid.strike == strike]
                    if len(contributors) >= 2:
                        _raw_atm = float(contributors.iv.mean())
                        value = _raw_atm if _raw_atm > 1.5 else _raw_atm * 100.0
                        method = 'Black-76 parity ATM call/put IV' if use_black76 else 'Same-strike call/put average'
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
        metrics[f'{delta} RR · put minus call'] = p - c if p is not None and c is not None else None
        metrics[f'{delta} BF · wings minus ATM'] = (p + c) / 2 - atm if all(v is not None for v in (p, c, atm)) else None
    out = {'version': VERSION, 'points': points, 'metrics': metrics}
    if forward is not None and np.isfinite(forward):
        out['forward'] = float(forward)
        out['pricing_model'] = 'Black-76' if use_black76 else 'Spot-BSM'
    return out


def black76_smile_points(chain, strikes=None, spot=None, forward=None, years=7.0 / 365.0, rate=0.066):
    """Compute 5-point smile using Fischer Black (1976) implied volatilities and discounted deltas off Forward F."""
    return smile_points(chain, strikes or [], spot, forward=forward, years=years, rate=rate, use_black76=True)


def save_smile(path, symbol, expiry, config, as_of, result, capture_source=None):
    if capture_source != 'Google Drive':
        return
    path = Path(path)
    with closing(sqlite3.connect(path, timeout=30)) as con, con:
        try:
            con.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError:
            con.execute('PRAGMA journal_mode=DELETE')
        con.execute('CREATE TABLE IF NOT EXISTS smile_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, version TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of,version))')
        con.execute('INSERT OR REPLACE INTO smile_observations VALUES (?,?,?,?,?,?)',
                    (symbol, str(expiry), str(config), str(as_of), VERSION, json.dumps(result, allow_nan=False)))


def load_smile(path, symbol, expiry, config, as_of):
    path = Path(path)
    if not path.exists() or not expiry or not as_of:
        return None
    with closing(sqlite3.connect(path, timeout=15)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smile_observations'").fetchone():
            return None
        row = con.execute(
            'SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND config=? AND as_of=? AND version=?',
            (symbol, str(expiry), str(config), str(as_of), VERSION),
        ).fetchone()
        if not row:
            # Fallback across config hash revisions for the exact same (symbol, expiry, as_of)
            row = con.execute(
                'SELECT payload FROM smile_observations WHERE symbol=? AND expiry=? AND as_of=? AND version=? ORDER BY rowid DESC LIMIT 1',
                (symbol, str(expiry), str(as_of), VERSION),
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
        if config:
            rows = con.execute(
                'SELECT as_of, payload FROM smile_observations WHERE symbol=? AND expiry=? AND config=? AND version=? ORDER BY as_of DESC LIMIT 25',
                (symbol, str(expiry), str(config), VERSION),
            ).fetchall()
        else:
            rows = []
        if not rows:
            rows = con.execute(
                'SELECT as_of, payload FROM smile_observations WHERE symbol=? AND expiry=? AND version=? ORDER BY as_of DESC LIMIT 25',
                (symbol, str(expiry), VERSION),
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
