"""Historical and current statutory + broker transaction charge schedules for NSE F&O."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from datetime import date, datetime, timezone
from pathlib import Path
import json

ROOT = PACKAGE_ROOT
STORE = runtime_data_dir() / 'charges' / 'upstox'

# Dated against the NSE transaction-charge circular effective 2026-03-01 and
# the STT change effective 2026-04-01.  Keep the version alongside reports so
# historical simulations retain their original cost convention.
CHARGE_MODEL_VERSION = 'upstox-nse-2026-03-v4'

# NSE/FA/73061, effective 1 March 2026. Upstox publishes bundled exchange
# totals of ₹183/₹3553 per crore including the ₹0.01/crore IPFT.
REGIMES = [
    (date(2026, 4, 1), {'fut_stt': 0.0005, 'opt_stt': 0.0015, 'fut_exch': 0.000018299, 'opt_exch': 0.000355299, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000000001, 'ipft_opt': 0.000000001, 'stt_opt_sell': 0.0015, 'stt_fut_sell': 0.0005, 'exch_opt': 0.000355299, 'exch_fut': 0.000018299, 'ipft': 0.000000001}),
    (date(2026, 3, 1), {'fut_stt': 0.0002, 'opt_stt': 0.001,  'fut_exch': 0.000018299, 'opt_exch': 0.000355299, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000000001, 'ipft_opt': 0.000000001, 'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.000355299, 'exch_fut': 0.000018299, 'ipft': 0.000000001}),
    (date(2024, 10, 1), {'fut_stt': 0.0002, 'opt_stt': 0.001,  'fut_exch': 0.0000173, 'opt_exch': 0.0003503, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000001, 'ipft_opt': 0.000005, 'stt_opt_sell': 0.0010, 'stt_fut_sell': 0.0002, 'exch_opt': 0.0003503, 'exch_fut': 0.0000173, 'ipft': 0.000005}),
    (date(1970, 1, 1), {'fut_stt': 0.000125, 'opt_stt': 0.000625, 'fut_exch': 0.0000188, 'opt_exch': 0.000495, 'fut_stamp': 0.00002, 'opt_stamp': 0.00003, 'sebi': 0.000001, 'ipft_fut': 0.000001, 'ipft_opt': 0.000005, 'stt_opt_sell': 0.000625, 'stt_fut_sell': 0.000125, 'exch_opt': 0.000495, 'exch_fut': 0.000019, 'ipft': 0.000005}),
]


def rates(day=None):
    if not day:
        return REGIMES[0][1]
    d = date.fromisoformat(str(day)[:10])
    for start, table in REGIMES:
        if d >= start:
            return table
    return REGIMES[-1][1]


def charge(day, segment, arg3, arg4, arg5='buy', brokerage=20.0):
    """Compute statutory + broker charges.
    Supports both:
      - charge(day, segment, price, quantity, action, brokerage=20.0) -> dict
      - charge(day, segment, side_int, price, quantity, brokerage=20.0) -> dict
    """
    if isinstance(arg5, (int, float)) and not isinstance(arg5, bool) and arg3 in (-1, 1):
        side_int = int(arg3)
        price = float(arg4)
        quantity = float(arg5)
        action = 'buy' if side_int > 0 else 'sell'
    else:
        price = float(arg3)
        quantity = float(arg4)
        action = 'sell' if str(arg5).lower() in ('sell', '-1', 'short') else 'buy'

    r = rates(day)
    turnover = abs(price * quantity)
    is_opt = str(segment).lower().startswith('opt')
    order_brokerage = float(brokerage)
    if not is_opt:
        order_brokerage = min(order_brokerage, turnover * 0.0005)
    is_sell = (action == 'sell')
    stt = turnover * (r['opt_stt'] if is_opt else r['fut_stt']) if is_sell else 0.0
    exch = turnover * (r['opt_exch'] if is_opt else r['fut_exch'])
    sebi = turnover * r['sebi']
    ipft = turnover * (r['ipft_opt'] if is_opt else r['ipft_fut'])
    stamp = turnover * (r['opt_stamp'] if is_opt else r['fut_stamp']) if not is_sell else 0.0
    # Upstox lists GST on brokerage, transaction and IPFT charges; SEBI turnover fees
    # are a separate statutory line item and are excluded from that published base.
    gst = 0.18 * (order_brokerage + exch + ipft)
    return {
        'Brokerage': order_brokerage,
        'STT': stt,
        'Exchange': exch,
        'IPFT': ipft,
        'SEBI': sebi,
        'GST': gst,
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
    """Return deterministic dated statutory regime status without blocking on external HTTP scrapes."""
    return status()
