"""Dated Upstox NSE fee estimates for backtests; expiry exercise is excluded."""
from datetime import datetime, date
from html.parser import HTMLParser
from pathlib import Path
import json, math
import requests
from collector_support import atomic_json, read_json

ROOT = Path(__file__).resolve().parent / 'data' / 'charges' / 'upstox'
URL = 'https://upstox.com/brokerage-charges/'


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def refresh():
    today = datetime.now().astimezone().date().isoformat()
    old = read_json(ROOT / 'current.json')
    if old.get('checked') == today:
        return old
    try:
        response = requests.get(URL, timeout=12)
        response.raise_for_status()
        parser = PageText()
        parser.feed(response.text)
        page = ' '.join(' '.join(parser.parts).split())
        expected = ('₹20', '0.15%', '0.05%', '0.03553%', '0.00183%')
        if any(value not in page for value in expected):
            raise ValueError('Published rates differ from the saved fee schedule.')
        result = {
            'checked': today,
            'rates': {kind: rates(today, kind) for kind in ('Futures', 'Options')},
            'source': URL,
            'status': 'checked',
            'plan': 'Upstox standard; per-order brokerage is configurable',
        }
        ROOT.mkdir(parents=True, exist_ok=True)
        atomic_json(ROOT / (today + '.json'), result)
        atomic_json(ROOT / 'current.json', result)
        atomic_json(ROOT / 'check_status.json', result)
        return result
    except Exception:
        result = {
            **old,
            'status': 'check_failed',
            'attempted': today,
            'warning': 'Upstox fee schedule could not be verified. The saved dated rates remain in use.',
        }
        atomic_json(ROOT / 'check_status.json', result)
        return result


def rates(day, kind=None, brokerage_per_order=20):
    day_str = str(day)[:10]
    if day_str < '2024-04-01':
        raise ValueError('Upstox fee history is modeled from 2024-04-01.')

    if kind is None or (isinstance(kind, (int, float)) and brokerage_per_order == 20):
        d = date.fromisoformat(day_str)
        if d >= date(2026, 4, 1):
            return {'stt_opt_sell': 0.0015, 'stt_fut_sell': 0.0005, 'exch_opt': 0.0003553, 'exch_fut': 0.0000183, 'ipft': 0.0}
        if d >= date(2026, 3, 1):
            return {'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.0003553, 'exch_fut': 0.0000183, 'ipft': 0.0}
        if d >= date(2024, 10, 1):
            return {'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.0003503, 'exch_fut': 0.0000173, 'ipft': 0.000005}
        return {'stt_opt_sell': 0.000625, 'stt_fut_sell': 0.000125, 'exch_opt': 0.000495, 'exch_fut': 0.000019, 'ipft': 0.000005}

    kind_norm = 'Futures' if str(kind).lower().startswith('fut') else ('Options' if str(kind).lower().startswith('opt') else kind)
    if kind_norm not in ('Futures', 'Options'):
        raise ValueError('Unsupported contract type.')
    try:
        brokerage = float(brokerage_per_order)
    except (TypeError, ValueError):
        raise ValueError('Brokerage per order must be non-negative.')
    if brokerage < 0 or not math.isfinite(brokerage):
        raise ValueError('Brokerage per order must be non-negative.')

    if kind_norm == 'Futures':
        stt = 0.000125
        exchange = 0.0000188
        ipft = 0.10 / 1e7
        stamp = 0.00002
        brokerage_rate = 0.0005
    else:
        stt = 0.000625
        exchange = 0.000495
        ipft = 0.50 / 1e7
        stamp = 0.00003
        brokerage_rate = None
    if day_str >= '2024-10-01':
        stt = 0.0002 if kind_norm == 'Futures' else 0.001
        exchange = 0.0000173 if kind_norm == 'Futures' else 0.0003503
    if day_str >= '2026-03-01':
        exchange = 0.0000183 if kind_norm == 'Futures' else 0.0003553
        ipft = 0.0
    if day_str >= '2026-04-01':
        stt = 0.0005 if kind_norm == 'Futures' else 0.0015
    return {
        'stt': stt,
        'exchange': exchange,
        'ipft': ipft,
        'stamp': stamp,
        'sebi': 0.000001,
        'gst': 0.18,
        'brokerage': brokerage,
        'brokerage_rate': brokerage_rate,
        'stt_opt_sell': 0.0015 if day_str >= '2026-04-01' else (0.0010 if day_str >= '2024-10-01' else 0.000625),
        'stt_fut_sell': 0.0005 if day_str >= '2026-04-01' else (0.0002 if day_str >= '2024-10-01' else 0.000125),
        'exch_opt': exchange if kind_norm == 'Options' else 0.0003553,
        'exch_fut': exchange if kind_norm == 'Futures' else 0.0000183,
    }


def charge(day, kind, price, qty, side=None, brokerage_per_order=20):
    if isinstance(price, str) and price.lower() in ('buy', 'sell'):
        actual_side = price.lower()
        actual_price = float(qty)
        actual_qty = float(side) if side is not None else 1.0
    else:
        actual_side = str(side).lower() if side is not None else 'buy'
        actual_price = float(price)
        actual_qty = float(qty)

    if actual_side not in ('buy', 'sell'):
        raise ValueError('Side must be buy or sell.')

    kind_norm = 'Futures' if str(kind).lower().startswith('fut') else 'Options'
    r = rates(day, kind_norm, brokerage_per_order)
    value = actual_price * actual_qty
    if value < 0 or not math.isfinite(value):
        raise ValueError('Invalid traded value.')

    brokerage = min(r['brokerage'], value * r['brokerage_rate']) if r.get('brokerage_rate') is not None else r['brokerage']
    exchange_cost = value * r['exchange']
    ipft_cost = value * r['ipft']
    exchange_ipft = exchange_cost + ipft_cost
    stt_cost = value * r['stt'] if actual_side == 'sell' else 0.0
    stamp_cost = value * r['stamp'] if actual_side == 'buy' else 0.0
    sebi_cost = value * r['sebi']
    gst_cost = (brokerage + exchange_ipft) * r['gst']

    return {
        'Brokerage': brokerage,
        'STT': stt_cost,
        'Exchange': exchange_cost,
        'IPFT': ipft_cost,
        'Exchange/IPFT': exchange_ipft,
        'SEBI': sebi_cost,
        'GST': gst_cost,
        'Stamp': stamp_cost,
        'Stamp duty': stamp_cost,
    }


if __name__ == '__main__':
    print(json.dumps(refresh()))
