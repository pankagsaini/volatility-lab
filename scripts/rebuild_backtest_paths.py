#!/usr/bin/env python3
"""Replay the recorded November 2026 backtest paths and enrich tradeArchive.

Every published row already records its entry and exit leg marks. This script
finds the archived Upstox snapshots whose captured LTPs reproduce those marks
exactly, replays every capture between them, and derives:

    entryAt / exitAt   captured entry and exit clock (IST)
    entryMin           whole minutes after the 09:15 session open
    holdMin            whole minutes from captured entry to captured exit
    maePct / mfePct    worst / best mark-to-market move, % of recorded capital,
                       before charges
    pathN / pathTotal  captures used / captures in the entry-to-exit window

It never interpolates a price: a capture is used only when every leg has a
positive traded LTP at that capture. It aborts unless the replayed exit marks
reproduce the row's published gross P&L to within one rupee.

Usage:
    python scripts/rebuild_backtest_paths.py [ARCHIVE_DIR]
"""

from __future__ import annotations

import argparse
import collections
import datetime
import gzip
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, 'index.html')
DOCS = os.path.join(ROOT, 'docs', 'index.html')
DEFAULT_ARCHIVE = r'G:\My Drive\Historical Data\Volatility Lab - private Upstox archive'
LEG_RE = re.compile(r'([SB])(\d*) (\d+)(CE|PE) \(([\d.]+)[^)]*?([\d.]+)\)')
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def legs_of(row):
    legs = []
    for side, ratio, strike, kind, entry, exit_ in LEG_RE.findall(row['legs']):
        legs.append({
            'side': 1 if side == 'B' else -1,
            'ratio': int(ratio or 1),
            'strike': int(strike),
            'kind': kind,
            'entry': float(entry),
            'exit': float(exit_),
        })
    return legs


def load_compact(path, needed):
    raw = gzip.decompress(open(path, 'rb').read())
    doc = json.loads(raw)
    values = {}
    for record in doc.get('records', []):
        label = record.get('label') or ''
        if '|' not in label or label not in needed:
            continue
        wanted = needed[label]
        for node in record.get('data') or []:
            strike = node.get('strike_price')
            if strike not in wanted:
                continue
            for kind, field in (('CE', 'call_options'), ('PE', 'put_options')):
                if kind not in wanted[strike]:
                    continue
                market = (node.get(field) or {}).get('market_data') or {}
                ltp = market.get('ltp')
                values[(label, strike, kind)] = ltp if isinstance(ltp, (int, float)) and ltp > 0 else None
    return doc.get('retrieved_at'), values


def clock(ts):
    dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(IST)
    return dt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', default=DEFAULT_ARCHIVE)
    parser.add_argument('--check', action='store_true', help='verify only, do not write')
    args = parser.parse_args()

    with open(INDEX, encoding='utf-8', newline='') as handle:
        html = handle.read()
    match = re.search(r'var tradeArchive = (\[.*?\]);\r?\n', html, re.S)
    if not match:
        sys.exit('tradeArchive not found in index.html')
    rows = json.loads(match.group(1))
    eol = '\r\n' if '\r\n' in html else '\n'
    if len(rows) != 72:
        sys.exit(f'expected 72 recorded rows, found {len(rows)}')

    files_by_day = collections.defaultdict(list)
    for name in os.listdir(args.archive):
        if not (name.endswith('_complete.json.gz') and '_snapshot_' in name):
            continue
        day = name[:10]
        if any(row['date'] == day for row in rows):
            files_by_day[day].append(os.path.join(args.archive, name))
    for day in files_by_day:
        files_by_day[day].sort()

    enriched = []
    for day in sorted(files_by_day):
        day_rows = [row for row in rows if row['date'] == day]
        needed = collections.defaultdict(lambda: collections.defaultdict(set))
        for row in day_rows:
            label = row['sym'] + '|' + row['exp']
            for leg in legs_of(row):
                needed[label][leg['strike']].add(leg['kind'])
        captures = []
        skipped = 0
        for path in files_by_day[day]:
            try:
                ts, values = load_compact(path, needed)
            except Exception:
                skipped += 1
                continue
            captures.append((os.path.basename(path)[20:26], ts, values))
        captures.sort(key=lambda item: item[1] or '')
        print(f'{day}: {len(captures)} archived captures ({skipped} unreadable skipped)', flush=True)

        for row in day_rows:
            legs = legs_of(row)
            label = row['sym'] + '|' + row['exp']
            lot = row['unitLegs'] / sum(abs(leg['ratio']) for leg in legs)

            def hits(field):
                found = []
                for capture in captures:
                    ok = True
                    for leg in legs:
                        value = capture[2].get((label, leg['strike'], leg['kind']))
                        if value is None or abs(value - leg[field]) > 0.06:
                            ok = False
                            break
                    if ok:
                        found.append(capture)
                return found

            entries, exits = hits('entry'), hits('exit')
            if len(entries) != 1 or len(exits) != 1:
                sys.exit(f"{day} {row['sym']} {row['strat']}: expected one entry and one exit capture, "
                         f"found {len(entries)} and {len(exits)}")
            entry, exit_ = entries[0], exits[0]
            points = 0.0
            for leg in legs:
                p0 = entry[2][(label, leg['strike'], leg['kind'])]
                p1 = exit_[2][(label, leg['strike'], leg['kind'])]
                points += leg['side'] * leg['ratio'] * (p1 - p0)
            gross = points * lot
            if abs(gross - row['gross']) > 1.0:
                sys.exit(f"{day} {row['sym']} {row['strat']}: replayed gross {gross:.2f} "
                         f"does not match recorded {row['gross']:.2f}")

            start = captures.index(entry)
            end = captures.index(exit_)
            low = high = None
            used = 0
            for _, _, values in captures[start:end + 1]:
                points = 0.0
                complete = True
                for leg in legs:
                    value = values.get((label, leg['strike'], leg['kind']))
                    if value is None:
                        complete = False
                        break
                    entry_value = entry[2][(label, leg['strike'], leg['kind'])]
                    points += leg['side'] * leg['ratio'] * (value - entry_value)
                if not complete:
                    continue
                used += 1
                rupees = points * lot
                low = rupees if low is None else min(low, rupees)
                high = rupees if high is None else max(high, rupees)
            if not used:
                sys.exit(f"{day} {row['sym']} {row['strat']}: no complete capture in the holding window")

            start_dt, end_dt = clock(entry[1]), clock(exit_[1])
            row['entryAt'] = start_dt.strftime('%H:%M:%S')
            row['exitAt'] = end_dt.strftime('%H:%M:%S')
            row['entryMin'] = (start_dt.hour * 60 + start_dt.minute) - (9 * 60 + 15)
            row['holdMin'] = round((end_dt - start_dt).total_seconds() / 60)
            row['maePct'] = round(abs(min(low, 0.0)) / row['capital'] * 100, 2)
            row['mfePct'] = round(max(high, 0.0) / row['capital'] * 100, 2)
            row['pathN'] = used
            row['pathTotal'] = end - start + 1
            enriched.append(row)

    if len(enriched) != len(rows):
        sys.exit('not every recorded row was replayed')

    stopped = sorted(enriched, key=lambda row: -row['maePct'])
    print(f"replayed {len(enriched)} rows; worst captured drawdown "
          f"{stopped[0]['maePct']:.2f}% of capital ({stopped[0]['date']} {stopped[0]['sym']} {stopped[0]['strat']})")
    held = collections.Counter((row['entryAt'], row['exitAt'], row['holdMin']) for row in enriched)
    for key, count in sorted(held.items()):
        print(f"  entry {key[0]} -> exit {key[1]} · {key[2]} min · {count} rows")

    if args.check:
        print('check only: index.html not modified')
        return

    payload = 'var tradeArchive = ' + json.dumps(enriched, ensure_ascii=False, separators=(',', ':')) + ';' + eol
    updated = html[:match.start()] + payload + html[match.end():]
    open(INDEX, 'w', encoding='utf-8', newline='').write(updated)
    os.makedirs(os.path.dirname(DOCS), exist_ok=True)
    open(DOCS, 'w', encoding='utf-8', newline='').write(updated)
    print(f'updated {INDEX}')
    print(f'updated {DOCS}')


if __name__ == '__main__':
    main()
