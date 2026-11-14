"""Local scheduled collection; session secrets stay Windows-encrypted."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from datetime import datetime, time, timedelta
from pathlib import Path
import math
import os
import sqlite3
import sys
import json
import msvcrt
from contextlib import contextmanager

from collector_support import atomic_json, read_json, STATE
from market_data import IST, MarketDataError, history_cutoff
from market_calendar import CalendarHorizonError, last_trading_day_on_or_before, session_window

ROOT=PACKAGE_ROOT
STATUS=STATE/'status.json'


def expected_day(now):
    return last_trading_day_on_or_before(history_cutoff(now), include_special=True)


def capture_stage(now):
    """Use the sourced session window, including special weekend sessions."""
    local = now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    try:
        window = session_window(local.date())
    except CalendarHorizonError:
        return None
    if window is None or local < window[0]:
        return None
    # Preserve the provider's conservative daily-candle availability buffer.
    eod_start = max(datetime.combine(local.date(), time(16, 30), tzinfo=IST),
                    window[1] + timedelta(minutes=50))
    if local >= eod_start:
        return 'eod'
    return 'closing_quotes' if local >= window[1] - timedelta(minutes=10) else 'history_catchup'


def verify_history(root,day):
    errors=[]
    with sqlite3.connect(root/'futures.sqlite') as con:
        for symbol in ('NIFTY','BANKNIFTY'):
            tails=dict(con.execute('SELECT rank,MAX(day) FROM continuous WHERE symbol=? GROUP BY rank',(symbol,)))
            if any(tails.get(rank,'')<str(day) for rank in (1,2,3)):
                errors.append(symbol+': completed futures candles missing')
    return errors


@contextmanager
def collector_lock():
    """Do not let a manual invocation overlap the scheduled collector."""
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    with (STATUS.parent/'collector.lock').open('a+b') as handle:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            yield False
            return
        try:yield True
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def record_run(state):
    # Preserve stage/outcome history without copying payloads, URLs or exception text.
    item={key:state[key] for key in ('attempt','finished','stage','status','step','provider') if key in state}
    item['error_count']=len(state.get('errors',[]))
    item['quality_gap_count']=len(state.get('quality_gaps',[]))
    if state.get('error_type'):item['error_type']=state['error_type']
    with (STATUS.parent/'runs.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(item)+'\n')


def run(now=None):
    with collector_lock() as acquired:
        if not acquired:return 0  # Existing worker owns capture and its status file.
        return _run(now)


def _run(now=None):
    now=now or datetime.now(IST)
    now=now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    stage=capture_stage(now)
    if stage is None:return 0
    key=str(now.date())+'_'+stage
    previous=read_json(STATUS)
    completed=previous.get('completed',{})
    if completed.get(key):return 0
    state={'attempt':now.isoformat(),'stage':stage,'status':'running','completed':completed}
    atomic_json(STATUS,state)
    errors=[]
    try:
        from upstox_adapter import market_client
        client=market_client()
        state['provider']='Upstox'
        state['step']='Futures history';atomic_json(STATUS,state)
        from futures_sync import refresh_all
        from futures_data import FuturesMonitor
        errors.extend(refresh_all(FuturesMonitor(),client,runtime_data_dir()/'futures',history_only=stage=='history_catchup'))
        if stage!='closing_quotes':
            errors.extend(verify_history(runtime_data_dir()/'futures',expected_day(now)))
            state['step']='Index minute history and option-chain archive';atomic_json(STATUS,state)
            from upstox_archive import incremental_capture
            try:incremental_capture()
            except MarketDataError as error:errors.append(str(error))
        # Closing calculations follow the date's actual session window.
        # EOD IV is captured autonomously via Google Cloud under IV DATA POLICY.md.
        if stage == 'closing_quotes':
            state['step'] = 'Options calculations'; atomic_json(STATUS, state)
            from risk_free import get_rate
            from dividend_yield import get_yields
            from expiry_data import ExpiryMonitor
            rate = get_rate().get('model_rate')
            yields = get_yields()
            dividend = {symbol: yields.get(symbol, {}).get('model_yield') for symbol in ('NIFTY', 'BANKNIFTY')}
            result = ExpiryMonitor().refresh_all(client, rate, dividend, 3.)
            rows = result.get('rows', [])
            for symbol in ('NIFTY', 'BANKNIFTY'):
                selected = [r for r in rows if r.get('Instrument') == symbol]
                if not selected: errors.append(symbol + ': option observations missing')
                usable = [r for r in selected if isinstance(r.get('IV'), (int, float)) and math.isfinite(r['IV']) and str(r.get('As of', ''))[:10] == str(now.date())]
                if not usable: errors.append(symbol + ': no current accepted IV observations')
                for row in selected:
                    valid = lambda value: isinstance(value, (int, float)) and math.isfinite(value)
                    if (not valid(row.get('IV')) or str(row.get('As of', ''))[:10] != str(now.date())
                            or row.get('Source') != 'Upstox supplied IV'):
                        state.setdefault('quality_gaps', []).append(symbol + ' ' + str(row.get('Expiry')) + ': no accepted closing IV')
        if not errors:completed[key]=now.isoformat()
        state.update(status='partial' if errors else 'complete',errors=errors,completed=completed)
    except Exception as exc:
        # Do not write raw API errors, request URLs or tokens to logs.
        state.update(status='failed',error_type=type(exc).__name__,errors=[str(exc) if isinstance(exc,MarketDataError) else 'Collection failed: '+type(exc).__name__])
    state['finished']=datetime.now(IST).isoformat()
    atomic_json(STATUS,state)
    record_run(state)
    return 0 if state['status']=='complete' else 1


if __name__=='__main__':sys.exit(run())
