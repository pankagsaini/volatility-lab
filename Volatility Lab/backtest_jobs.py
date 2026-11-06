"""Single-worker background job dispatcher with fixed strategy_backtest.simulate argument ordering and separate locks."""
from concurrent.futures import ThreadPoolExecutor
import threading
from upstox_archive import INDICES
from strategy_backtest import simulate as simulate_options

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='backtest-job')
_fee_lock = threading.Lock()
_inventory_lock = threading.Lock()


def _options(task):
    settings = task['settings']
    contracts = task['contracts']
    series = task['series']
    signals = task.get('signals')
    callback = task.get('callback')
    s = dict(settings)
    symbol = s.get('symbol', 'NIFTY')
    s.setdefault('spot_key', INDICES.get(symbol, 'NSE_INDEX|Nifty 50'))
    return simulate_options(s, contracts, series, signals, callback=callback)


def fee_status(refresh_fn=None):
    with _fee_lock:
        if refresh_fn is not None:
            return refresh_fn()
        return {'status': 'verified_static_regimes'}


def inventory_status(inventory_fn):
    with _inventory_lock:
        return inventory_fn()


def submit(state, key, fn, *args, **kwargs):
    slot = '_bt_job_' + key
    if slot in state and not state[slot].done():
        return False
    state[slot] = _executor.submit(fn, *args, **kwargs)
    return True


def active(state, key):
    fut = state.get('_bt_job_' + key)
    return fut is not None and not fut.done()


def cancel(state, key):
    fut = state.pop('_bt_job_' + key, None)
    if fut is not None:
        return fut.cancel()
    return False


def collect(state, key):
    slot = '_bt_job_' + key
    fut = state.get(slot)
    if fut is None or not fut.done():
        return None
    state.pop(slot, None)
    return fut.result()
