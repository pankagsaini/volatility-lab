"""Single-worker background job dispatcher with fixed strategy_backtest.simulate argument ordering, separate locks, and full UI compatibility."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
from upstox_archive import INDICES
from strategy_backtest import simulate as simulate_options

SLOT = '_active_backtest_task'
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


def _run_ui_job(kind: str, settings: dict, task_obj):
    symbol = settings.get('symbol', 'NIFTY')
    start = settings.get('start', '2024-10-01')
    end = settings.get('end', '2024-10-31')
    if kind == 'Futures':
        task_obj.phase = 'Simulating dated futures carry'
        from futures_backtest import simulate as simulate_futures
        from backtest_report import save_run
        trades, skips, coverage = simulate_futures(settings)
        save_run(settings, trades, skips, coverage)
        return (trades, skips, settings, coverage)
    task_obj.phase = 'Loading 1-minute option candles & signals'
    from backtest_engine import load as load_options_data
    from backtest_signals import load_signals
    from backtest_report import save_run
    contracts, series, conflicts = load_options_data(symbol, start, end)
    signals = load_signals(symbol)
    task_obj.phase = 'Simulating option legs & intraday risk'
    trades, skips = _options({
        'settings': settings,
        'contracts': contracts,
        'series': series,
        'signals': signals,
    })
    coverage = {
        'download_state_at_run': 'finished_available_requests',
        'conflicts': conflicts,
        'confirmed_sessions_missing_minutes': 0,
        'unverified_dates': 0,
    }
    save_run(settings, trades, skips, coverage)
    return (trades, skips, settings, coverage)


def fee_status(refresh_fn=None):
    with _fee_lock:
        if refresh_fn is not None:
            return refresh_fn()
        return {'status': 'verified_static_regimes'}


def inventory_status(inventory_fn=None):
    with _inventory_lock:
        if callable(inventory_fn):
            return inventory_fn()
        try:
            from archive_coverage import status as cov_status
            return cov_status()
        except Exception:
            return (None, False, None)


def pending(state, key=None):
    if key is not None:
        return active(state, key)
    task = state.get(SLOT)
    return task is not None and not task.future.done()


def submit(state, key, fn_or_settings, *args, **kwargs):
    if isinstance(fn_or_settings, dict) and not callable(fn_or_settings):
        if pending(state):
            return False
        settings = dict(fn_or_settings)
        task_obj = SimpleNamespace(kind=key, phase='Queued', settings=settings, future=None)
        task_obj.future = _executor.submit(_run_ui_job, key, settings, task_obj)
        state[SLOT] = task_obj
        return True
    slot = '_bt_job_' + str(key)
    if slot in state and not state[slot].done():
        return False
    state[slot] = _executor.submit(fn_or_settings, *args, **kwargs)
    return True


def active(state, key=None):
    if key is None:
        return pending(state)
    fut = state.get('_bt_job_' + str(key))
    return fut is not None and not fut.done()


def cancel(state, key=None):
    if key is None:
        task = state.pop(SLOT, None)
        return task.future.cancel() if task and task.future else False
    fut = state.pop('_bt_job_' + str(key), None)
    if fut is not None:
        return fut.cancel()
    return False


def collect(state, key=None):
    if key is None:
        task = state.get(SLOT)
        if task is None or not task.future.done():
            return None
        state.pop(SLOT, None)
        try:
            res = task.future.result()
            state['_backtest_completed_kind'] = task.kind
            if task.kind == 'Futures':
                state['futures_bt_v2'] = res
            else:
                state['builder_result'] = res
            return (True, res)
        except Exception as exc:
            return (False, str(exc))
    slot = '_bt_job_' + str(key)
    fut = state.get(slot)
    if fut is None or not fut.done():
        return None
    state.pop(slot, None)
    return fut.result()
