"""Publish compact, recorded-only option chains for chronological browser replay."""
from pathlib import Path
import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.rebuild_backtest_archive import (  # noqa: E402
    DEFAULT_ARCHIVE, GEOMETRY, IST, LOT_SIZES, load_closes, prior_session,
    realised_vol, session_files, skew,
)
from volatility_lab.backtest.broker_charges import rates, CHARGE_MODEL_VERSION  # noqa: E402


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def build(archive, output):
    output.mkdir(parents=True, exist_ok=True)
    closes = {sym: load_closes(sym) for sym in LOT_SIZES}
    manifest = {'schema': 1, 'geometry': GEOMETRY, 'sessions': [], 'excluded': [],
                'chargeModel': CHARGE_MODEL_VERSION, 'source': 'Captured Upstox LTP; research marks, not execution fills'}
    for day, paths in sorted(session_files(str(archive), set()).items()):
        snapshots = {sym: [] for sym in LOT_SIZES}
        expiry = {}
        bad = 0
        for path in paths:
            try:
                doc = json.loads(gzip.decompress(Path(path).read_bytes()))
                stamp = dt.datetime.fromisoformat(doc['retrieved_at'].replace('Z', '+00:00')).astimezone(IST)
                if doc.get('provider') != 'Upstox' or doc.get('status') != 'complete' or stamp.date().isoformat() != day:
                    continue
                for rec in doc.get('records', []):
                    label = rec.get('label', '')
                    sym, _, exp = label.partition('|')
                    if sym not in snapshots or not isinstance(rec.get('data'), list):
                        continue
                    if (dt.date.fromisoformat(exp) - stamp.date()).days < 2:
                        continue
                    if sym not in expiry:
                        available = sorted(r['label'].split('|')[1] for r in doc['records']
                                           if r.get('label', '').startswith(sym+'|')
                                           and (dt.date.fromisoformat(r['label'].split('|')[1])-stamp.date()).days >= 2)
                        expiry[sym] = available[0]
                    if exp != expiry[sym]:
                        continue
                    rows = rec['data']
                    spot = number(rows[0].get('underlying_spot_price')) if rows else None
                    if not spot or spot <= 0:
                        continue
                    chain = []
                    full = {}
                    for row in rows:
                        k = number(row.get('strike_price'))
                        if not k or k <= 0:
                            continue
                        cell = [k]
                        for side in ('call_options', 'put_options'):
                            leg = row.get(side) or {}
                            md, greek = leg.get('market_data') or {}, leg.get('option_greeks') or {}
                            bid, ask = number(md.get('bid_price')), number(md.get('ask_price'))
                            spread = None
                            try:
                                quoted = dt.datetime.fromisoformat(str(md['quote_timestamp']).replace('Z', '+00:00'))
                                if bid and ask and 0 < bid <= ask and -30 <= (stamp-quoted).total_seconds() <= 120:
                                    spread = 200 * (ask-bid)/(ask+bid)
                            except (KeyError, TypeError, ValueError):
                                pass
                            cell += [number(md.get('ltp')), number(greek.get('iv')), number(greek.get('delta')),
                                     number(md.get('oi')), spread]
                        chain.append(cell)
                        full[k] = (cell[1], cell[2], cell[3], cell[6], cell[7], cell[8])
                    if not chain:
                        continue
                    atm = min(full, key=lambda k: (abs(k-spot), k))
                    rr, bf = skew(full, atm)
                    snapshots[sym].append({'at': stamp.isoformat(), 'spot': spot, 'atm': atm,
                                           'rr25': rr, 'bf25': bf, 'chain': chain,
                                           'source': Path(path).name})
            except (OSError, ValueError, KeyError, TypeError):
                bad += 1
        for sym, points in snapshots.items():
            if not points:
                continue
            # Preserve every strike needed by any of the six structures at any
            # observed entry. Missing later leg marks remain missing.
            strikes = {p['atm'] + offset for p in points for legs in GEOMETRY[sym].values()
                       for _, _, offset, _ in legs}
            unique = {p['at']: p for p in points}
            points = [unique[k] for k in sorted(unique)]
            for p in points:
                p['chain'] = [c for c in p['chain'] if c[0] in strikes]
            prior = prior_session(closes[sym], day)
            prev = next((c for d, c in closes[sym] if d == prior), None)
            payload = {'schema': 1, 'symbol': sym, 'date': day, 'expiry': expiry[sym], 'lot': LOT_SIZES[sym],
                       'hvAsOf': prior, 'hv10': realised_vol(closes[sym], prior, 10) if prior else None,
                       'hv30': realised_vol(closes[sym], prior, 30) if prior else None,
                       'previousClose': prev, 'rates': rates(day), 'points': points}
            content = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
            name = f'{sym}-{day}.json'
            (output/name).write_bytes(content)
            manifest['sessions'].append({'symbol': sym, 'date': day, 'file': name, 'points': len(points),
                                         'first': points[0]['at'], 'last': points[-1]['at'],
                                         'sha256': hashlib.sha256(content).hexdigest()})
        if bad:
            manifest['excluded'].append({'date': day, 'unreadableCaptures': bad})
        print(day, {s: len(p) for s, p in snapshots.items()}, flush=True)
    (output/'manifest.json').write_text(json.dumps(manifest, separators=(',', ':')), encoding='utf-8')
    print('Published', len(manifest['sessions']), 'symbol sessions', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path(DEFAULT_ARCHIVE))
    parser.add_argument('--output', type=Path, default=ROOT/'docs'/'replay')
    args = parser.parse_args()
    build(args.archive, args.output)
    if args.output.resolve() == (ROOT/'docs'/'replay').resolve():
        shutil.copytree(args.output, ROOT/'replay', dirs_exist_ok=True)
