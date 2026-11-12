"""Fast cached pricing inputs; official-source checks never block rendering (Black-76 Forward Model Edition)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json, math, threading, time
try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')

PRICING_MODEL = 'Black-76'
PRICING_MODEL_LABEL = 'Black-76 Forward Model · F = K + e^{rT}(C − P)'
DEFAULT_RISK_FREE_RATE = 0.066

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
    for cand in (_ROOT / name, _ROOT.parent / "Volatility Lab" / "data" / name, _ROOT.parent.parent / "Volatility Lab" / "data" / name):
        if cand.is_file():
            try:
                return json.loads(cand.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
    return {}


def get_rate():
    ensure_refresh()
    saved = _read('risk_free_rate.json')
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
                item['error'] = 'Factsheet older than 62 days; retaining last verified yield while background refresh runs.'
            else:
                item['model_yield'] = None
                item['error'] = 'No recent verified dividend yield; background check in progress.'
        else:
            item['stale'] = False
    return saved


def compute_parity_forward(strike: float, call_price: float, put_price: float, years: float, rate: float | None = None) -> float:
    """Compute Black-76 Put-Call Parity Implied Forward F = K + exp(r * T) * (C - P)."""
    r = float(rate) if (rate is not None and math.isfinite(float(rate))) else float(get_rate().get('model_rate') or DEFAULT_RISK_FREE_RATE)
    t = max(0.0, float(years))
    return float(strike) + math.exp(r * t) * (float(call_price) - float(put_price))


def get_pricing_model_config() -> dict:
    """Return active Black-76 options pricing configuration metadata."""
    rate_info = get_rate()
    return {
        'model': PRICING_MODEL,
        'label': PRICING_MODEL_LABEL,
        'rate': float(rate_info.get('model_rate') or DEFAULT_RISK_FREE_RATE),
        'as_of': rate_info.get('as_of'),
        'source': rate_info.get('source', 'FBIL / RBI 91-Day Treasury Bill Benchmark'),
    }
