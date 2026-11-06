"""Background backtests with frozen settings; no Streamlit calls from workers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lab-backtest')
_fee_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='backtest-fees')
_fee_lock = Lock()
_inventory_lock = Lock()
_fee_future = None
_fee_attempt = float('-inf')
_inventory_future = None
_inventory_value = None
_inventory_attempt = float('-inf')
_inventory_error = None
SLOT = '_backtest_task'


@dataclass
class Task:
    kind: str
    settings: dict
    started: float = field(default_factory=monotonic)
    phase: str = 'Queued'
    future: object = None


def _options(task):
    from backtest_engine import load, ROOT
    from strategy_backtest import simulate
    from backtest_signals import load_signals
    from archive_coverage import daily_sessions, session_report
    from upstox_data import INDICES
    from collector_support import read_json

    if isinstance(task, dict):
        settings = task['settings']
        contracts = task['contracts']
        series = task['series']
        signals = task.get('signals') or ()
        symbol = settings.get('symbol', 'NIFTY')
        spotkey = INDICES.get(symbol, 'NSE_INDEX|Nifty 50')
        return simulate(contracts, series, spotkey, settings, signals)

    settings = task.settings
    symbol = settings['symbol']
    start = settings['start']
    end = settings['end']
    task.phase = 'Loading selected option archives'
    contracts, series, conflicts = load(symbol, start, end)
    settings['confirmed_sessions'] = daily_sessions(INDICES[symbol], start, end)
    task.phase = 'Simulating strategy'
    signals = load_signals(symbol) if settings.get('filters') else []
    trades, skips = simulate(contracts, series, INDICES[symbol], settings, signals)
    stamps = sorted(series.get(INDICES[symbol], {}))
    coverage = {
        'conflicts': conflicts,
        'underlying_first': stamps[0] if stamps else None,
        'underlying_last': stamps[-1] if stamps else None,
        'underlying_observed_sessions': len({x[:10] for x in stamps}),
        'download_state_at_run': read_json(ROOT / 'status.json').get('status', 'unknown')
    }
    coverage.update(session_report(series.get(INDICES[symbol], {}), start, end, settings['confirmed_sessions']))
    return trades, skips, settings, coverage


def _futures(task):
    from futures_backtest import load, simulate, contract_lot_sizes
    s = task.settings
    task.phase = 'Loading selected futures archives'
    bars, conflicts = load(s['symbol'], s['start'], s['end'])
    task.phase = 'Simulating carry strategy'
    pair = {'Near - Far': (0, 2), 'Near - Next': (0, 1), 'Next - Far': (1, 2)}[s['name'].removesuffix(' carry')]
    lots = s.get('lots')
    if lots:
        contracts = contract_lot_sizes(s['symbol'])
        trades, skips, position = simulate(
            bars, pair, s['entry_carry'], s['target_carry'], s.get('quantity', 75),
            1 if s['direction'].startswith('Buy') else -1, s['slippage_bps'], s['max_hold_days'], s['brokerage_per_order'],
            lots=lots, contracts=contracts
        )
    else:
        trades, skips, position = simulate(
            bars, pair, s['entry_carry'], s['target_carry'], s['quantity'],
            1 if s['direction'].startswith('Buy') else -1, s['slippage_bps'], s['max_hold_days'], s['brokerage_per_order']
        )
    coverage = dict(conflicts=conflicts, dated_contract_days=len(bars), open_position=bool(position), unfinished_position=position)
    return trades, skips, s, coverage


def _run(task):
    result = (_options if task.kind == 'Options' else _futures)(task)
    task.phase = 'Saving results'
    from backtest_report import save_run
    trades, skips, settings, coverage = result
    run_id = save_run(settings, trades, skips, coverage)
    return result, run_id


def pending(state):
    return SLOT in state


def submit(state, kind, settings):
    if kind not in ('Options', 'Futures'):
        raise ValueError('Unknown backtest market.')
    if pending(state):
        return False
    task = Task(kind, deepcopy(settings))
    task.future = _pool.submit(_run, task)
    state[SLOT] = task
    return True


def collect(state):
    task = state.get(SLOT)
    if task is None or not task.future.done():
        return None
    del state[SLOT]
    try:
        result, run_id = task.future.result()
    except (ValueError, KeyError, TypeError, OSError) as error:
        return False, str(error)
    except Exception:
        return False, 'Backtest failed; the previous saved report is unchanged.'
    target = 'builder_result' if task.kind == 'Options' else 'futures_bt_v2'
    state[target] = result
    state['_backtest_completed_kind'] = task.kind
    return True, run_id


def fee_status(refresh_fn=None):
    """Display the saved check immediately; official verification runs separately."""
    global _fee_future, _fee_attempt
    from broker_charges import ROOT, refresh
    from collector_support import read_json
    with _fee_lock:
        if refresh_fn is not None:
            return refresh_fn()
        if (_fee_future is None or _fee_future.done()) and monotonic() - _fee_attempt >= 3600:
            _fee_attempt = monotonic()
            _fee_future = _fee_pool.submit(refresh)
    return read_json(ROOT / 'check_status.json') or read_json(ROOT / 'current.json') or {'status': 'checking', 'checked': 'not checked yet'}


def inventory_status(inventory_fn=None):
    """Request inventory on demand; leave the page responsive during a large scan."""
    global _inventory_future, _inventory_value, _inventory_attempt, _inventory_error
    from archive_coverage import inventory
    with _inventory_lock:
        if inventory_fn is not None:
            return inventory_fn()
        if _inventory_future is not None and _inventory_future.done():
            try:
                _inventory_value = _inventory_future.result()
                _inventory_error = None
            except Exception:
                _inventory_error = 'Archive coverage could not be read. Saved coverage is retained.'
            _inventory_future = None
        if _inventory_future is None and monotonic() - _inventory_attempt >= 60:
            _inventory_attempt = monotonic()
            _inventory_future = _fee_pool.submit(inventory)
        return _inventory_value, _inventory_future is not None, _inventory_error
