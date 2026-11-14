"""Fast cached pricing inputs; official-source checks never block rendering (Black-76 Forward Model Edition)."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json, math, threading, time
try:
    from collector_support import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')

PRICING_MODEL = 'Black-76'
PRICING_MODEL_LABEL = 'Black-76 Forward Model · F = K + e^{rT}(C − P)'
DEFAULT_RISK_FREE_RATE = float('nan')  # Missing official inputs must remain unavailable.

_ROOT = runtime_data_dir()
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
    from volatility_lab.runtime.settings import load_settings
    if load_settings().offline:
        return None
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
    saved = _read('risk_free_rate.json')
    try:
        age = (datetime.now(IST) - datetime.fromisoformat(saved['checked_at'])).total_seconds()
        valid = (saved.get('source') == 'https://www.rbi.org.in/' and saved.get('sha256')
                 and 0 <= age <= 7 * 86400 and 0 <= float(saved['model_rate']) < .30)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        saved['model_rate'] = None
        saved['error'] = 'No recent verified RBI rate; background check in progress.'
    if saved:
        saved.setdefault('pricing_model', PRICING_MODEL)
        saved.setdefault('pricing_model_label', PRICING_MODEL_LABEL)
    return saved


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
                item['model_yield'] = None
                item['error'] = 'Factsheet outside its valid date window; model yield withheld pending refresh.'
            else:
                item['model_yield'] = None
                item['error'] = 'No recent verified dividend yield; background check in progress.'
        else:
            item['stale'] = False
        expected = 'https://www.niftyindices.com/Factsheet/' + ('ind_nifty50.pdf' if symbol == 'NIFTY' else 'ind_nifty_bank.pdf')
        try:
            valid_yield = 0 <= float(item['model_yield']) <= .15
            checked = datetime.fromisoformat(item['checked_at'])
            valid_yield = valid_yield and checked.tzinfo is not None and checked <= datetime.now(IST)
        except (ValueError, TypeError, KeyError):
            valid_yield = False
        if item.get('source') != expected or not item.get('sha256') or not valid_yield:
            item['model_yield'] = None
    return saved


def compute_parity_forward(strike: float, call_price: float, put_price: float, years: float, rate: float | None = None) -> float:
    """Compute Black-76 Put-Call Parity Implied Forward F = K + exp(r * T) * (C - P)."""
    from volatility_lab.pricing.option_math import parity_implied_forward
    r = get_rate().get('model_rate') if rate is None else rate
    return parity_implied_forward(strike, call_price, put_price, years, r)


def get_pricing_model_config() -> dict:
    """Return active Black-76 options pricing configuration metadata."""
    rate_info = get_rate()
    return {
        'model': PRICING_MODEL,
        'label': PRICING_MODEL_LABEL,
        'rate': rate_info.get('model_rate'),
        'as_of': rate_info.get('checked_at'),
        'source': rate_info.get('source'),
    }
