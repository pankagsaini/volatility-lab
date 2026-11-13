"""Shared helpers, contract blacklists, and dated NSE lot size schedule for Options and Futures backtests."""
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
import json

ROOT = PACKAGE_ROOT
BLACKLIST_PATH = ROOT / 'data' / 'backtest_blacklist.json'
BLACKLISTED_OPTIONS = set()
BLACKLISTED_FUTURES = set()


def load_blacklist(path=None):
    """Load option and future contract blacklists from disk if present."""
    target = Path(path) if path is not None else BLACKLIST_PATH
    if not target.exists():
        return {'options': set(), 'futures': set()}
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
        opts = {tuple(x) if isinstance(x, list) else str(x) for x in raw.get('options', [])}
        futs = {tuple(x) if isinstance(x, list) else str(x) for x in raw.get('futures', [])}
        BLACKLISTED_OPTIONS.update(opts)
        BLACKLISTED_FUTURES.update(futs)
        return {'options': opts, 'futures': futs}
    except Exception:
        return {'options': set(), 'futures': set()}


def record_skip(skips, day, reason):
    skips.append({'Date': str(day), 'Reason': str(reason)})


def is_blacklisted_option(symbol: str, expiry: str = '', strike: float = 0.0, option_type: str = '') -> bool:
    if str(symbol) in BLACKLISTED_OPTIONS:
        return True
    return (str(symbol), str(expiry), float(strike or 0.0), str(option_type)) in BLACKLISTED_OPTIONS


def is_blacklisted_future(symbol: str, expiry: str = '', key: str = '') -> bool:
    if str(key) in BLACKLISTED_FUTURES or str(symbol) in BLACKLISTED_FUTURES:
        return True
    return (str(symbol), str(expiry)) in BLACKLISTED_FUTURES


def dated_lot_size(symbol: str, day_or_expiry: str = '') -> int:
    """Return historical NSE index derivative lot size for NIFTY and BANKNIFTY."""
    d = str(day_or_expiry or '')[:10]
    sym = str(symbol or 'NIFTY').upper()
    if sym == 'BANKNIFTY':
        if d and d < '2023-07-01':
            return 25
        if d and d < '2024-11-20':
            return 15
        return 30
    if d and d < '2024-04-26':
        return 50
    if d and d < '2024-11-20':
        return 25
    if d and d < '2025-12-31':
        return 75
    return 65
