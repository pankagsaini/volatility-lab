"""Shared lightweight helpers for backtest engines."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
BLACKLIST_PATH = ROOT / 'data' / 'audit' / 'contract_blacklist.json'
_BLACKLIST = None


def load_blacklist():
    global _BLACKLIST
    if _BLACKLIST is None:
        try:
            data = json.loads(BLACKLIST_PATH.read_text(encoding='utf-8'))
            keys = set(data.get('blacklisted_keys', []))
            futures = {(item['symbol'], item['expiry']) for item in data.get('blacklisted_futures', [])}
            _BLACKLIST = (keys, futures)
        except Exception:
            _BLACKLIST = ({'NSE_FO|58561|03-10-2024', 'NSE_FO|61471|23-11-2026'},
                          {('BANKNIFTY', '2024-11-27'), ('NIFTY', '2026-11-23')})
    return _BLACKLIST


def is_blacklisted_option(key, symbol=None, weekly=False):
    keys, _ = load_blacklist()
    if key in keys:
        return True
    if symbol == 'BANKNIFTY' and weekly:
        return True
    return False


def is_blacklisted_future(symbol, expiry):
    _, futures = load_blacklist()
    return (symbol, str(expiry)[:10]) in futures


def record_skip(skips, day, reason):
    skips.append({'Date': day, 'Reason': reason})
