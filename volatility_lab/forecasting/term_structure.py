"""Spot-ATM term structure from one instrument's dated expiry snapshots."""
from datetime import datetime, time
import numpy as np
import pandas as pd
try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')


def compute_forward_vol(iv1, dte1, iv2, dte2):
    """Compute annualized forward implied volatility (%) and calendar-arbitrage flag between two expiries."""
    if not all(np.isfinite([iv1, dte1, iv2, dte2])) or dte2 <= dte1 or dte1 <= 0 or iv1 <= 0 or iv2 <= 0:
        return np.nan, False
    t1, t2 = dte1 / 365.0, dte2 / 365.0
    var1 = (iv1 / 100.0) ** 2 * t1
    var2 = (iv2 / 100.0) ** 2 * t2
    fwd_var = (var2 - var1) / (t2 - t1)
    if fwd_var < 0:
        return np.nan, True
    return float(100.0 * np.sqrt(fwd_var)), False


def term_structure(rows, symbol, deduplicate=False):
    selected = [row for row in rows if row.get('Instrument') == symbol]
    records = []
    seen = set()
    for row in sorted(selected, key=lambda r: (r['Expiry'], str(r.get('As of') or ''))):
        expiry = pd.Timestamp(row['Expiry']).date()
        if expiry in seen:
            if deduplicate:
                records = [r for r in records if r['Expiry'] != str(expiry)]
            else:
                raise ValueError('Duplicate expiry in snapshot.')
        seen.add(expiry)
        stamp = pd.Timestamp(row.get('As of'))
        valid_time = not pd.isna(stamp) and stamp.tzinfo is not None
        if valid_time:
            stamp = stamp.tz_convert(IST)
        end = pd.Timestamp(datetime.combine(expiry, time(15, 30), tzinfo=IST))
        dte = (end - stamp).total_seconds() / 86400 if valid_time else np.nan
        try:
            iv = float(row.get('IV'))
        except (TypeError, ValueError):
            iv = np.nan
        status = 'Ready'
        if not valid_time:
            status = 'Missing valuation timestamp'
        elif dte <= 0:
            status = 'Expired at valuation'
        elif not np.isfinite(iv) or iv <= 0:
            status = 'IV unavailable'
        if status != 'Ready':
            iv = np.nan
        records.append({
            'Expiry': str(expiry),
            'Type': row.get('Type', ''),
            'DTE': dte,
            'IV': iv,
            'Next expiry': None,
            'Next IV - IV': np.nan,
            'Forward IV': np.nan,
            'Calendar Arbitrage': False,
            'Source': row.get('Source', 'Unknown'),
            'As of': str(stamp) if valid_time else None,
            'Status': status,
            'Comparison': 'No next expiry',
        })
    for current, following in zip(records, records[1:]):
        current['Next expiry'] = following['Expiry']
        if not np.isfinite([current['IV'], following['IV']]).all():
            current['Comparison'] = 'Unavailable leg'
        elif current['As of'] != following['As of']:
            current['Comparison'] = 'Different valuation times'
        elif current['Source'] != following['Source']:
            current['Comparison'] = 'Different price sources'
        else:
            current['Next IV - IV'] = following['IV'] - current['IV']
            fwd_iv, cal_arb = compute_forward_vol(current['IV'], current['DTE'], following['IV'], following['DTE'])
            current['Forward IV'] = fwd_iv
            current['Calendar Arbitrage'] = cal_arb
            current['Comparison'] = 'Comparable snapshots'
    return pd.DataFrame(records, columns=[
        'Expiry', 'Type', 'DTE', 'IV', 'Next expiry',
        'Next IV - IV', 'Forward IV', 'Calendar Arbitrage', 'Source', 'As of', 'Status', 'Comparison'
    ])
