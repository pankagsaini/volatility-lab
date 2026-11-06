"""Fast cached pricing inputs; official-source checks never block rendering."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json, threading, time
from market_data import IST

_ROOT = Path(__file__).resolve().parent / 'data'
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pricing-inputs')
_lock = threading.Lock()
_future = None
_started = 0.0


def _refresh():
    from risk_free import get_rate as refresh_rate
    from dividend_yield import get_yields as refresh_dividends
    errors = []
    for call in (refresh_rate, refresh_dividends):
        try:
            call()
        except Exception:
            errors.append('Pricing-input update failed; cached inputs retained.')
    return errors


def ensure_refresh():
    global _future, _started
    with _lock:
        if _future is not None and (not _future.done() or time.monotonic() - _started < 3600):
            return _future
        _started = time.monotonic()
        _future = _pool.submit(_refresh)
        return _future


def _read(name):
    try:
        return json.loads((_ROOT / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def get_rate():
    ensure_refresh()
    return _read('risk_free_rate.json')


def get_yields():
    ensure_refresh()
    saved = _read('dividend_yields.json')
    today = datetime.now(IST).date()
    for symbol in ('NIFTY', 'BANKNIFTY'):
        item = dict(saved.get(symbol, {}))
        saved[symbol] = item
        try:
            age = (today - datetime.fromisoformat(item.get('as_of', '')).date()).days
        except (ValueError, TypeError):
            age = 999
        if not 0 <= age <= 62:
            if item.get('model_yield') is not None:
                item['stale'] = True
                item['error'] = 'Factsheet older than 62 days; retaining last verified yield while background refresh runs.'
            else:
                item['model_yield'] = None
                item['error'] = 'No recent verified dividend yield; background check in progress.'
        else:
            item['stale'] = False
    return saved
