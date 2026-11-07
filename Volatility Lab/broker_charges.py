"""Historical and current statutory + broker transaction charge schedules for NSE F&O."""
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from collector_support import atomic_json, read_json

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'data' / 'charges' / 'upstox'

REGIMES = [
    (date(2026, 4, 1), {'fut_stt': 0.0005, 'opt_stt': 0.0015, 'fut_exch': 0.0000183, 'opt_exch': 0.0003553, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.0, 'ipft_opt': 0.0, 'stt_opt_sell': 0.0015, 'stt_fut_sell': 0.0005, 'exch_opt': 0.0003553, 'exch_fut': 0.0000183, 'ipft': 0.0}),
    (date(2026, 3, 1), {'fut_stt': 0.0002, 'opt_stt': 0.001,  'fut_exch': 0.0000183, 'opt_exch': 0.0003553, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.0, 'ipft_opt': 0.0, 'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.0003553, 'exch_fut': 0.0000183, 'ipft': 0.0}),
    (date(2024, 10, 1), {'fut_stt': 0.0002, 'opt_stt': 0.001,  'fut_exch': 0.0000173, 'opt_exch': 0.0003503, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000005, 'ipft_opt': 0.000005, 'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.0003503, 'exch_fut': 0.0000173, 'ipft': 0.000005}),
    (date(1970, 1, 1), {'fut_stt': 0.000125, 'opt_stt': 0.000625, 'fut_exch': 0.0000188, 'opt_exch': 0.000495, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000005, 'ipft_opt': 0.000005, 'stt_opt_sell': 0.000625, 'stt_fut_sell': 0.000125, 'exch_opt': 0.000495, 'exch_fut': 0.000019, 'ipft': 0.000005}),
]


def rates(day=None, kind=None, brokerage_per_order=20):
    day_str = str(day)[:10] if day else datetime.now(timezone.utc).date().isoformat()
    if kind is not None and not isinstance(kind, (int, float)) and day_str < '2024-04-01':
        raise ValueError('Upstox fee history is modeled from 2024-04-01.')

    d = date.fromisoformat(day_str)
    table = REGIMES[-1][1]
    for start, t in REGIMES:
        if d >= start:
            table = t
            break

    if kind is None or (isinstance(kind, (int, float)) and brokerage_per_order == 20):
        return dict(table)

    kind_norm = 'Futures' if str(kind).lower().startswith('fut') else 'Options'
    is_fut = (kind_norm == 'Futures')
    try:
        brokerage = float(brokerage_per_order)
    except (TypeError, ValueError):
        raise ValueError('Brokerage per order must be non-negative.')
    if brokerage < 0 or not math.isfinite(brokerage):
        raise ValueError('Brokerage per order must be non-negative.')

    stt = table['fut_stt'] if is_fut else table['opt_stt']
    exchange = table['fut_exch'] if is_fut else table['opt_exch']
    ipft = table['ipft_fut'] if is_fut else table['ipft_opt']
    stamp = table['fut_stamp'] if is_fut else table['opt_stamp']
    brokerage_rate = 0.0005 if is_fut else None

    return {
        **table,
        'stt': stt,
        'exchange': exchange,
        'ipft': ipft,
        'stamp': stamp,
        'sebi': table['sebi'],
        'gst': 0.18,
        'brokerage': brokerage,
        'brokerage_rate': brokerage_rate,
    }


def charge(day, segment, arg3, arg4, arg5='buy', brokerage=20.0):
    """Compute statutory + broker charges. Supports flexible argument positions."""
    if isinstance(arg5, (int, float)) and not isinstance(arg5, bool) and arg3 in (-1, 1):
        side_int = int(arg3)
        price = float(arg4)
        quantity = float(arg5)
        action = 'buy' if side_int > 0 else 'sell'
        actual_brokerage = float(brokerage)
    elif isinstance(arg3, (int, float)) and isinstance(arg4, (int, float)) and str(arg5).lower() in ('buy', 'sell', '1', '-1', 'short'):
        price = float(arg3)
        quantity = float(arg4)
        action = 'sell' if str(arg5).lower() in ('sell', '-1', 'short') else 'buy'
        actual_brokerage = float(brokerage)
    else:
        price = float(arg3)
        quantity = float(arg4)
        action = 'sell' if str(arg5).lower() in ('sell', '-1', 'short') else 'buy'
        actual_brokerage = float(brokerage)

    kind_norm = 'Futures' if str(segment).lower().startswith('fut') else 'Options'
    r = rates(day, kind_norm, actual_brokerage)
    turnover = abs(price * quantity)
    is_opt = (kind_norm == 'Options')
    is_sell = (action == 'sell')

    brok = min(r['brokerage'], turnover * r['brokerage_rate']) if r.get('brokerage_rate') is not None else r['brokerage']
    stt = turnover * (r['opt_stt'] if is_opt else r['fut_stt']) if is_sell else 0.0
    exch = turnover * (r['opt_exch'] if is_opt else r['fut_exch'])
    ipft = turnover * (r['ipft_opt'] if is_opt else r['ipft_fut'])
    exchange_ipft = exch + ipft
    sebi = turnover * r['sebi']
    stamp = turnover * (r['opt_stamp'] if is_opt else r['fut_stamp']) if not is_sell else 0.0
    gst = 0.18 * (brok + exchange_ipft)

    return {
        'Brokerage': float(brok),
        'STT': stt,
        'Exchange': exch,
        'IPFT': ipft,
        'Exchange/IPFT': exchange_ipft,
        'SEBI': sebi,
        'GST': gst,
        'Stamp': stamp,
        'Stamp duty': stamp,
    }


def current():
    path = STORE / 'current.json'
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'provider': 'Upstox', 'status': 'verified_static_regimes', 'rates': rates()}


def status():
    path = STORE / 'check_status.json'
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'status': 'verified_static_regimes', 'checked_at': datetime.now(timezone.utc).isoformat()}


def refresh():
    return status()
