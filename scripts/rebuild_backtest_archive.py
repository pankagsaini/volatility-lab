#!/usr/bin/env python3
"""Rebuild the backtest archive from recorded Upstox snapshots.

The dashboard must never show a backtest row that was not marked by a real
captured quote.  This generator reads the private snapshot archive, replays the
six published structures for every session that has a complete 09:20-09:25
entry capture and a complete 15:15 exit capture, and writes the result into
``index.html`` (and its ``docs/`` mirror).

What it derives, and from where:

* entry/exit leg marks  - recorded ``market_data.ltp`` at the capture
* gross P&L             - replayed from those marks and the recorded lot size
* charges               - the same statutory model the Python backtester uses
* capital               - the documented scenario-margin proxy (never broker margin)
* ATM IV                - mean of recorded call/put IV at the strike nearest spot
* RR25 / BF25           - 25-delta interpolation over recorded greeks
* HV10 / HV30           - sample sigma of the last 10/30 log returns
* path MAE/MFE          - every complete capture between entry and exit

A session is excluded, never patched, when it has no 09:20-09:25 entry capture
or no 15:15 capture.  A structure is skipped, never re-priced, when its expiry
window has no listed expiry or any leg has no traded mark.

Usage:
    python scripts/rebuild_backtest_archive.py --days 2026-11-02,2026-11-09
    python scripts/rebuild_backtest_archive.py            # every usable session
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import glob
import gzip
import json
import math
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, 'index.html')
DOCS = os.path.join(ROOT, 'docs', 'index.html')
DATA = os.path.join(ROOT, 'volatility_lab', 'data')
DEFAULT_ARCHIVE = r'G:\My Drive\Historical Data\Volatility Lab - private Upstox archive'
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

LOT_SIZES = {'NIFTY': 65, 'BANKNIFTY': 30}
SPOT_TOKENS = {'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank'}
HV_SOURCE = {'NIFTY': 'nifty_one_year.csv', 'BANKNIFTY': 'banknifty_one_year.csv'}

# Entry clocks, DTE windows and recordable filters, mirrored from
# SCENARIO_PRESETS in volatility_lab/backtest/strategy_backtest.py.
PRESETS = {
    'S1': {'name': 'VRP Harvest Iron Condor', 'entry': '09:20', 'dte': (2, 10),
           'filters': [('vrp', 2.0, 15.0), ('hv30', 8.0, 16.0)]},
    'S2': {'name': 'Gamma Expansion Long Straddle', 'entry': '09:25', 'dte': (3, 7),
           'filters': [('vrp', -15.0, -1.5)]},
    'S3': {'name': 'Post-Event Front-Week Crush', 'entry': '09:30', 'dte': (1, 5),
           'filters': [('vrp', 3.0, 20.0)]},
    'S4': {'name': 'Steep Put Skew Ratio Spread', 'entry': '09:20', 'dte': (3, 14),
           'filters': [('rr25', 2.5, 12.0), ('vrp', 1.0, 15.0)]},
    'S5': {'name': 'Intraday Theta Decay Iron Butterfly', 'entry': '09:20', 'dte': (1, 6),
           'filters': [('vrp', 1.0, 15.0)]},
    'S6': {'name': 'Synthetic Basis Skewed Condor', 'entry': '09:25', 'dte': (2, 12),
           'filters': [('vrp', 1.5, 15.0), ('rr25', -6.0, 0.5)]},
}

# Leg geometry in index points, mirrored from buildStrategyLegs_() in index.html.
GEOMETRY = {
    'NIFTY': {
        'S1': [(-1, 'CE', 200, 1), (-1, 'PE', -200, 1), (1, 'CE', 500, 1), (1, 'PE', -500, 1)],
        'S2': [(1, 'CE', 0, 1), (1, 'PE', 0, 1)],
        'S3': [(-1, 'CE', 0, 1), (-1, 'PE', 0, 1), (1, 'CE', 350, 1), (1, 'PE', -350, 1)],
        'S4': [(-1, 'PE', -150, 2), (1, 'PE', -450, 2), (-1, 'CE', 250, 1), (1, 'CE', 550, 1)],
        'S5': [(-1, 'CE', 0, 1), (-1, 'PE', 0, 1), (1, 'CE', 250, 1), (1, 'PE', -250, 1)],
        'S6': [(-1, 'CE', 150, 2), (1, 'CE', 450, 2), (-1, 'PE', -250, 1), (1, 'PE', -550, 1)],
    },
    'BANKNIFTY': {
        'S1': [(-1, 'CE', 400, 1), (-1, 'PE', -400, 1), (1, 'CE', 1000, 1), (1, 'PE', -1000, 1)],
        'S2': [(1, 'CE', 0, 1), (1, 'PE', 0, 1)],
        'S3': [(-1, 'CE', 0, 1), (-1, 'PE', 0, 1), (1, 'CE', 700, 1), (1, 'PE', -700, 1)],
        'S4': [(-1, 'PE', -300, 2), (1, 'PE', -900, 2), (-1, 'CE', 500, 1), (1, 'CE', 1100, 1)],
        'S5': [(-1, 'CE', 0, 1), (-1, 'PE', 0, 1), (1, 'CE', 500, 1), (1, 'PE', -500, 1)],
        'S6': [(-1, 'CE', 300, 2), (1, 'CE', 900, 2), (-1, 'PE', -500, 1), (1, 'PE', -1100, 1)],
    },
}

ENTRY_WINDOW_MIN = 10      # a missing entry capture may not be chased beyond this
MIN_DTE = 2                # the recorded archive never trades a same/next-day expiry
ENTRY_OPEN = '092000'
STANDARD_OPEN_LIMIT = '110000'
EXIT_EARLIEST = '151000'
EXIT_LATEST = '151559'


def clock(ts):
    return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(IST)


def hhmmss(value):
    return value.strftime('%H:%M:%S')


def load_closes(symbol):
    path = os.path.join(DATA, HV_SOURCE[symbol])
    out = []
    with open(path, newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            try:
                out.append((row['Date'][:10], float(row['Close'])))
            except (TypeError, ValueError):
                continue
    out.sort()
    return out


def realised_vol(closes, as_of, returns_count):
    """Sample sigma of the last N log returns, annualised by sqrt(245)."""
    window = [close for day, close in closes if day <= as_of][-(returns_count + 1):]
    if len(window) < returns_count + 1 or any(close <= 0 for close in window):
        return None
    returns = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    return round(statistics.stdev(returns) * math.sqrt(245) * 100, 2)


def prior_session(closes, day):
    earlier = [d for d, _ in closes if d < day]
    return earlier[-1] if earlier else None


def estimate_margin(legs, spot, symbol):
    """Mirror of estimate_exchange_margin() (scenario-margin-proxy-v2)."""
    psr = 0.08 if symbol == 'NIFTY' else 0.10
    shifts = [-0.15, -psr, -psr / 2.0, 0.0, psr / 2.0, psr, 0.15]
    test_spots = {spot * (1.0 + shift) for shift in shifts} | {float(leg['strike']) for leg in legs}
    net_credit = sum(-leg['side'] * leg['entry'] * leg['quantity'] for leg in legs)
    worst_loss = 0.0
    for level in test_spots:
        payoff = 0.0
        for leg in legs:
            intrinsic = max(level - leg['strike'], 0.0) if leg['kind'] == 'CE' else max(leg['strike'] - level, 0.0)
            payoff += leg['side'] * intrinsic * leg['quantity']
        worst_loss = max(worst_loss, max(0.0, -(payoff + net_credit)))
    net_calls = sum(leg['side'] * leg['quantity'] for leg in legs if leg['kind'] == 'CE')
    net_puts = sum(leg['side'] * leg['quantity'] for leg in legs if leg['kind'] == 'PE')
    short_qty = max(
        sum(leg['quantity'] for leg in legs if leg['kind'] == 'CE' and leg['side'] < 0),
        sum(leg['quantity'] for leg in legs if leg['kind'] == 'PE' and leg['side'] < 0),
        0,
    )
    if net_calls < 0 or net_puts < 0:
        return round(max(worst_loss + 0.02 * spot * short_qty, 0.05 * spot * short_qty))
    if short_qty == 0:
        return round(max(0.0, -net_credit))
    return round(max(worst_loss, 0.015 * spot * short_qty))


def load_capture(path, labels=None):
    """Return one capture: spot per index and per-label strike greeks.

    ``labels`` limits chain parsing to the expiries actually being traded, which
    keeps the run fast across hundreds of captures without changing any value.
    """
    doc = json.loads(gzip.decompress(open(path, 'rb').read()))
    retrieved = doc.get('retrieved_at')
    spot = {}
    catalogue = {}
    for record in doc.get('records', []):
        label = record.get('label') or ''
        if label == 'quotes':
            data = record.get('data')
            items = data.values() if isinstance(data, dict) else (data or [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                token = item.get('instrument_token')
                for symbol, wanted in SPOT_TOKENS.items():
                    if token == wanted:
                        price = item.get('last_price')
                        if isinstance(price, (int, float)) and price > 0:
                            spot[symbol] = price
        elif '|' in label and (labels is None or label in labels):
            for node in record.get('data') or []:
                strike = node.get('strike_price')
                if not isinstance(strike, (int, float)):
                    continue
                call = node.get('call_options') or {}
                put = node.get('put_options') or {}
                cg = call.get('option_greeks') or {}
                pg = put.get('option_greeks') or {}
                catalogue.setdefault(label, {})[strike] = (
                    (call.get('market_data') or {}).get('ltp'),
                    cg.get('iv'),
                    cg.get('delta'),
                    (put.get('market_data') or {}).get('ltp'),
                    pg.get('iv'),
                    pg.get('delta'),
                )
    return {'at': retrieved, 'spot': spot, 'chains': catalogue}


def strikes_of(chain):
    return sorted(k for k in chain if isinstance(k, (int, float)))


def nearest_strike(strikes, target):
    return min(strikes, key=lambda k: (abs(k - target), k))


def leg_marks(chain, strike, kind):
    cell = chain.get(strike)
    if not cell:
        return None
    return cell[0] if kind == 'CE' else cell[3]


def atm_iv(chain, strike):
    cell = chain.get(strike)
    if not cell:
        return None
    call_iv, put_iv = cell[1], cell[4]
    if call_iv is None or put_iv is None:
        return None
    return (call_iv + put_iv) / 2.0


def skew(chain, atm):
    """LLinearly interpolated 25-delta RR25 and BF25 in percentage points."""
    calls, puts = [], []
    for strike, (_, civ, cdelta, _, piv, pdelta) in chain.items():
        if civ is not None and cdelta is not None and cdelta > 0:
            calls.append((cdelta, civ))
        if piv is not None and pdelta is not None and pdelta < 0:
            puts.append((-pdelta, piv))
    calls.sort()
    puts.sort()

    def interp(points, target):
        for (a, iv_a), (b, iv_b) in zip(points, points[1:]):
            if a <= target <= b and b > a:
                weight = (target - a) / (b - a)
                return iv_a + weight * (iv_b - iv_a)
        return None

    put_25 = interp(puts, 0.25)
    call_25 = interp(calls, 0.25)
    level = atm_iv(chain, atm)
    if put_25 is None or call_25 is None or level is None:
        return None, None
    return round(put_25 - call_25, 2), round((put_25 + call_25) / 2.0 - level, 2)


def build_legs(symbol, strategy, atm):
    layout = GEOMETRY[symbol][strategy]
    return [{'side': side, 'kind': kind, 'strike': atm + offset, 'ratio': ratio}
            for side, kind, offset, ratio in layout]


def evaluate_rule(row):
    failures = []
    for key, low, high in PRESETS[row['strat']]['filters']:
        value = row.get(key)
        if value is None:
            failures.append(f"{key.upper()} not recorded")
        elif not (low <= value <= high):
            failures.append(f"{key.upper()} {value:+.2f} outside {low:+.2f}..{high:+.2f}")
    if row.get('dteInBand') is False:
        low, high = PRESETS[row['strat']]['dte']
        failures.append(f"DTE {row['dte']} outside {low}-{high}")
    return (not failures), '; '.join(failures) if failures else ''


def fmt_signal(row):
    bits = []
    if row['strat'] in ('S4',):
        if row.get('rr25') is not None:
            bits.append(f"RR25 {row['rr25']:+.2f} pp")
    if row['strat'] in ('S6',):
        if row.get('bf25') is not None:
            bits.append(f"BF25 {row['bf25']:+.2f} pp")
    bits.append(f"IV {row['iv']:.2f}%" if row.get('iv') is not None else 'IV not recorded')
    bits.append(f"HV30 {row['hv30']:.2f}%" if row.get('hv30') is not None else 'HV30 not recorded')
    bits.append(f"VRP {row['vrp']:+.2f} pp" if row.get('vrp') is not None else 'VRP not recorded')
    return ' · '.join(bits)


def fmt_legs(legs, entry_marks, exit_marks):
    parts = []
    for leg in legs:
        side = 'S' if leg['side'] < 0 else 'B'
        ratio = str(leg['ratio']) if leg['ratio'] > 1 else ''
        entry = entry_marks[leg['strike'], leg['kind']]
        exit_ = exit_marks[leg['strike'], leg['kind']]
        parts.append(f"{side}{ratio} {leg['strike']}{leg['kind']} ({entry:.1f}→{exit_:.1f})")
    return ' / '.join(parts)


def session_files(archive, days):
    grouped = collections.defaultdict(list)
    for path in glob.glob(os.path.join(archive, '*_snapshot_*_complete.json.gz')):
        day = os.path.basename(path)[:10]
        if days and day not in days:
            continue
        grouped[day].append(path)
    for day in grouped:
        grouped[day].sort(key=lambda p: os.path.basename(p)[20:26])
    return grouped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', default=DEFAULT_ARCHIVE)
    parser.add_argument('--days', default='', help='comma separated session dates to process')
    parser.add_argument('--out', default='', help='write JSON here instead of index.html')
    parser.add_argument('--check', action='store_true', help='verify only, do not write')
    args = parser.parse_args()

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from volatility_lab.backtest.broker_charges import CHARGE_MODEL_VERSION, charge

    days = {d.strip() for d in args.days.split(',') if d.strip()}
    grouped = session_files(args.archive, days)
    if not grouped:
        sys.exit('no snapshot sessions found')

    closes = {symbol: load_closes(symbol) for symbol in LOT_SIZES}
    rows, sessions, excluded, skips, unreadable_files = [], [], [], [], []

    for day in sorted(grouped):
        files = grouped[day]
        # Session-level clocks, independent of any structure.
        entry_candidates = [p for p in files if ENTRY_OPEN <= os.path.basename(p)[20:26] <= STANDARD_OPEN_LIMIT]
        exit_candidates = [p for p in files if EXIT_EARLIEST <= os.path.basename(p)[20:26] <= EXIT_LATEST]
        first_clock = os.path.basename(files[0])[20:26]
        if not entry_candidates or first_clock[:8] > STANDARD_OPEN_LIMIT:
            excluded.append({'date': day, 'reason': f'special session; first capture {first_clock[:2]}:{first_clock[2:4]}, no 09:20 entry'})
            continue
        if not exit_candidates:
            last = os.path.basename(files[-1])[20:26]
            excluded.append({'date': day, 'reason': f'no 15:15 exit capture; last capture {last[:2]}:{last[2:4]}'})
            continue

        unreadable = []

        def read(paths):
            got = []
            for path in paths:
                try:
                    got.append((os.path.basename(path)[20:26], load_capture(path)))
                except Exception as error:  # unreadable capture is skipped, never guessed
                    unreadable.append({'date': day, 'file': os.path.basename(path),
                                       'error': type(error).__name__})
            got.sort(key=lambda item: item[0])
            return got

        head = read([files[0]])
        if not head:
            excluded.append({'date': day, 'reason': 'first capture unreadable'})
            continue

        available = {}
        for label in head[0][1]['chains']:
            symbol, _, expiry = label.partition('|')
            if symbol in LOT_SIZES and expiry:
                available.setdefault(symbol, []).append(expiry)

        expiry_kind, expiry_choice = {}, {}
        for symbol, expiries in available.items():
            try:
                listed = sorted({datetime.date.fromisoformat(e) for e in expiries})
            except ValueError:
                continue
            gaps = [(b - a).days for a, b in zip(listed, listed[1:])]
            expiry_kind[symbol] = ('weekly' if gaps and statistics.median(gaps) <= 10
                                   else 'monthly')
            session_date = datetime.date.fromisoformat(day)
            tradable = [item for item in listed if (item - session_date).days >= MIN_DTE]
            if tradable:
                expiry_choice[symbol] = (tradable[0].isoformat(),
                                         (tradable[0] - session_date).days)

        wanted = {f'{symbol}|{choice[0]}' for symbol, choice in expiry_choice.items()}
        captures = []
        for path in files:
            try:
                captures.append((os.path.basename(path)[20:26], load_capture(path, wanted)))
            except Exception as error:  # unreadable capture is skipped, never guessed
                unreadable.append({'date': day, 'file': os.path.basename(path),
                                   'error': type(error).__name__})
        captures.sort(key=lambda item: item[0])
        unreadable_files.extend(unreadable)

        for symbol in ('NIFTY', 'BANKNIFTY'):
            lot = LOT_SIZES[symbol]
            day_had_structure = False
            for code, preset in PRESETS.items():
                entry_clock = preset['entry'].replace(':', '') + '00'
                entry_ceiling = (datetime.datetime.strptime(preset['entry'], '%H:%M')
                                 + datetime.timedelta(minutes=ENTRY_WINDOW_MIN)).strftime('%H%M%S')
                choice = expiry_choice.get(symbol)
                if not choice:
                    skips.append({'date': day, 'sym': symbol, 'strat': code,
                                  'reason': f'no {symbol} expiry with DTE >= {MIN_DTE}'})
                    continue
                expiry_date, dte = choice
                label = f'{symbol}|{expiry_date}'
                dte_in_band = preset['dte'][0] <= dte <= preset['dte'][1]

                def marks_for(legs, cap):
                    chain = cap['chains'].get(label)
                    if not chain:
                        return None
                    marks = {}
                    for leg in legs:
                        value = leg_marks(chain, leg['strike'], leg['kind'])
                        if not isinstance(value, (int, float)) or value <= 0:
                            return None
                        marks[leg['strike'], leg['kind']] = value
                    return marks

                def complete(cap):
                    """Entry state: ATM from this capture's spot, all legs marked."""
                    chain = cap['chains'].get(label)
                    if not chain or symbol not in cap['spot']:
                        return None
                    spot = cap['spot'][symbol]
                    atm = nearest_strike(strikes_of(chain), spot)
                    legs = build_legs(symbol, code, atm)
                    marks = marks_for(legs, cap)
                    if marks is None:
                        return None
                    return {'atm': atm, 'spot': spot, 'chain': chain, 'legs': legs, 'marks': marks}

                entry = None
                for stamp, capture in captures:
                    if stamp < entry_clock:
                        continue
                    if stamp > entry_ceiling:
                        break
                    state = complete(capture)
                    if state:
                        entry = (stamp, capture, state)
                        break
                if not entry:
                    skips.append({'date': day, 'sym': symbol, 'strat': code,
                                  'reason': f'no complete capture within {ENTRY_WINDOW_MIN} min of {preset["entry"]}'})
                    continue

                exit_ = None
                for stamp, capture in reversed(captures):
                    if stamp > EXIT_LATEST:
                        continue
                    if stamp < EXIT_EARLIEST:
                        break
                    marks = marks_for(entry[2]['legs'], capture)
                    if marks and stamp >= entry[0]:
                        exit_ = (stamp, capture, marks)
                        break
                if not exit_:
                    skips.append({'date': day, 'sym': symbol, 'strat': code,
                                  'reason': 'no complete 15:15 exit capture'})
                    continue

                entry_state = entry[2]
                exit_marks = exit_[2]
                legs = [{**leg, 'quantity': lot * leg['ratio']} for leg in entry_state['legs']]
                points = sum(leg['side'] * leg['ratio']
                             * (exit_marks[leg['strike'], leg['kind']]
                                - entry_state['marks'][leg['strike'], leg['kind']])
                             for leg in entry_state['legs'])
                gross = round(points * lot, 2)

                charges = 0.0
                for leg in legs:
                    charges += sum(charge(day, 'options', entry_state['marks'][leg['strike'], leg['kind']],
                                          leg['quantity'], 'sell' if leg['side'] < 0 else 'buy').values())
                    charges += sum(charge(day, 'options', exit_marks[leg['strike'], leg['kind']],
                                          leg['quantity'], 'buy' if leg['side'] < 0 else 'sell').values())
                charges = round(charges, 2)

                capital = estimate_margin(
                    [{**leg, 'entry': entry_state['marks'][leg['strike'], leg['kind']]} for leg in legs],
                    entry_state['spot'], symbol)

                iv = atm_iv(entry_state['chain'], entry_state['atm'])
                rr25, bf25 = skew(entry_state['chain'], entry_state['atm'])
                as_of = prior_session(closes[symbol], day)
                hv10 = realised_vol(closes[symbol], as_of, 10) if as_of else None
                hv30 = realised_vol(closes[symbol], as_of, 30) if as_of else None

                # Path: entry capture through exit capture, complete marks only.
                first = next(i for i, (stamp, _) in enumerate(captures) if stamp == entry[0])
                last = next(i for i, (stamp, _) in enumerate(captures) if stamp == exit_[0])
                low = high = None
                used = 0
                for _, capture in captures[first:last + 1]:
                    marks = marks_for(entry_state['legs'], capture)
                    if marks is None:
                        continue
                    move = sum(leg['side'] * leg['ratio']
                               * (marks[leg['strike'], leg['kind']]
                                  - entry_state['marks'][leg['strike'], leg['kind']])
                               for leg in entry_state['legs']) * lot
                    used += 1
                    low = move if low is None else min(low, move)
                    high = move if high is None else max(high, move)

                entry_dt, exit_dt = clock(entry[1]['at']), clock(exit_[1]['at'])
                row = {
                    'sym': symbol, 'date': day, 'exp': expiry_date, 'strat': code,
                    'capital': capital, 'gross': gross, 'charges': charges,
                    'unitLegs': lot * sum(leg['ratio'] for leg in entry_state['legs']),
                    'iv': round(iv, 2) if iv is not None else None,
                    'hv10': hv10, 'hv30': hv30,
                    'vrp': round(iv - hv30, 2) if iv is not None and hv30 is not None else None,
                    'ivhv10': round(iv - hv10, 2) if iv is not None and hv10 is not None else None,
                    'dte': dte, 'rr25': rr25, 'bf25': bf25, 'hvAsOf': as_of,
                    'dteInBand': dte_in_band,
                    'dteBand': list(preset['dte']),
                    'entryAt': hhmmss(entry_dt), 'exitAt': hhmmss(exit_dt),
                    'entryMin': (entry_dt.hour * 60 + entry_dt.minute) - (9 * 60 + 15),
                    'holdMin': round((exit_dt - entry_dt).total_seconds() / 60),
                    'maePct': round(abs(min(low, 0.0)) / capital * 100, 2),
                    'mfePct': round(max(high, 0.0) / capital * 100, 2),
                    'pathN': used, 'pathTotal': last - first + 1,
                    'entrySpot': round(entry_state['spot'], 2),
                    'expiryKind': expiry_kind.get(symbol, 'unknown'),
                }
                row['legs'] = fmt_legs(entry_state['legs'], entry_state['marks'], exit_marks)
                row['sig'] = fmt_signal(row)
                row['sigCalc'] = row['sig']
                row['rulePass'], row['ruleFail'] = evaluate_rule(row)
                rows.append(row)
                day_had_structure = True

            if day_had_structure:
                sessions.append({'date': day, 'sym': symbol,
                                 'captures': len(captures),
                                 'entryAt': hhmmss(clock(next(c[1]['at'] for c in captures if c[0] >= '092000'))),
                                 'exitAt': hhmmss(clock(next(c[1]['at'] for c in reversed(captures) if c[0] <= EXIT_LATEST)))})

    rows.sort(key=lambda r: (r['date'], r['sym'], r['strat']))
    passed = sum(1 for r in rows if r['rulePass'])
    print(f'{len(rows)} structures from {len({r["date"] for r in rows})} sessions '
          f'({len({r["date"] for r in rows if r["sym"] == "NIFTY"})} NIFTY, '
          f'{len({r["date"] for r in rows if r["sym"] == "BANKNIFTY"})} BANKNIFTY)')
    print(f'rule passes: {passed}/{len(rows)}')
    for code in PRESETS:
        subset = [r for r in rows if r['strat'] == code]
        print(f"  {code}: {sum(1 for r in subset if r['rulePass'])}/{len(subset)} "
              f"sessions={len({r['date'] for r in subset})}")
    print(f'excluded sessions: {len(excluded)}; skipped structures: {len(skips)}')
    for item in excluded:
        print('  exclude', item['date'], '-', item['reason'])

    meta = {
        'schema': 1,
        'lotSizes': LOT_SIZES,
        'chargeModel': CHARGE_MODEL_VERSION,
        'capitalModel': 'scenario-margin-proxy-v2 (documented proxy, not broker margin)',
        'hvModel': 'sample sigma of the last 10/30 log returns x sqrt(245)',
        'presets': {code: {'name': p['name'], 'entry': p['entry'], 'dte': p['dte'],
                           'filters': p['filters']} for code, p in PRESETS.items()},
        'sessions': sessions,
        'excluded': excluded,
        'skips': skips,
        'unreadable': unreadable_files,
        'counts': {'structures': len(rows), 'rulePass': passed,
                   'sessions': len({r['date'] for r in rows})},
    }

    if args.out:
        with open(args.out, 'w', encoding='utf-8', newline='') as handle:
            json.dump({'rows': rows, 'meta': meta}, handle, indent=1)
        print('wrote', args.out)
        return
    if args.check:
        print('check only: index.html not modified')
        return

    with open(INDEX, encoding='utf-8', newline='') as handle:
        html = handle.read()
    eol = '\r\n' if '\r\n' in html else '\n'
    payload = 'var tradeArchive = ' + json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + ';' + eol
    meta_payload = 'var backtestMeta = ' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ';' + eol
    updated, replaced = re.subn(r'var tradeArchive = \[.*?\];\r?\n', payload, html, count=1, flags=re.S)
    if not replaced:
        sys.exit('tradeArchive literal not found')
    updated, replaced = re.subn(r'var backtestMeta = \{.*?\};\r?\n', meta_payload, updated, count=1, flags=re.S)
    if not replaced:
        updated = updated.replace(payload, payload + meta_payload, 1)
    with open(INDEX, 'w', encoding='utf-8', newline='') as handle:
        handle.write(updated)
    with open(DOCS, 'w', encoding='utf-8', newline='') as handle:
        handle.write(updated)
    print('updated', INDEX)
    print('updated', DOCS)


if __name__ == '__main__':
    main()
