"""Recorded provenance and Python/browser charge-model parity."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import build_replay_data as exporter
from volatility_lab.backtest.broker_charges import charge, rates


def test_export_keeps_missing_iv_and_prior_close_only(tmp_path, monkeypatch):
    day = '2026-11-18'
    stamp = day + 'T09:20:00+05:30'
    record = {'label': 'NIFTY|2026-11-24', 'data': [{
        'strike_price': 25000, 'underlying_spot_price': 25001,
        'call_options': {'market_data': {'ltp': 100, 'bid_price': 99, 'ask_price': 101,
                                        'quote_timestamp': stamp, 'oi': 10}},
        'put_options': {'market_data': {'ltp': 90, 'bid_price': 89, 'ask_price': 91,
                                       'quote_timestamp': day+'T09:10:00+05:30'}},
    }]}
    document = {'provider': 'Upstox', 'status': 'complete', 'retrieved_at': stamp, 'records': [record]}
    source = tmp_path / '2026-11-18_snapshot_092000_complete.json.gz'
    source.write_bytes(gzip.compress(json.dumps(document).encode()))
    broken = tmp_path / 'broken.json.gz'
    broken.write_bytes(b'not a capture')
    monkeypatch.setattr(exporter, 'session_files', lambda *_: {day: [str(source), str(source), str(broken)]})
    monkeypatch.setattr(exporter, 'load_closes', lambda _: [('2026-11-17', 24900), (day, 99999)])
    output = tmp_path/'public'
    exporter.build(tmp_path, output)
    manifest = json.loads((output/'manifest.json').read_text())
    assert len(manifest['sessions']) == 1
    item = manifest['sessions'][0]
    raw = (output/item['file']).read_bytes()
    assert item['sha256'] == hashlib.sha256(raw).hexdigest()
    saved = json.loads(raw)
    assert saved['hvAsOf'] == '2026-11-17'
    assert saved['previousClose'] == 24900
    assert saved['hv10'] is None and saved['hv30'] is None
    assert len(saved['points']) == 1  # duplicate timestamps do not duplicate trades
    point = saved['points'][0]
    assert point['source'] == source.name
    assert point['rr25'] is None and point['bf25'] is None
    assert point['chain'][0][2] is None  # never infer historical IV
    assert point['chain'][0][5] == 2  # verified fresh call spread
    assert point['chain'][0][10] is None  # stale put book cannot pass a spread bound
    assert manifest['excluded'] == [{'date': day, 'unreadableCaptures': 1}]


@pytest.mark.skipif(not shutil.which('node'), reason='Node is needed for browser/Python parity')
def test_browser_charges_match_dated_python_model():
    root = Path(__file__).resolve().parents[2]
    cases = [{'day': day, 'price': price, 'quantity': quantity, 'buy': buy, 'rates': rates(day)}
             for day in ['2025-01-02', '2026-03-10', '2026-11-18']
             for price in [12.5, 320] for quantity in [65, 130] for buy in [True, False]]
    script = """
const fs=require('fs'), vm=require('vm'), ctx={};
vm.runInNewContext(fs.readFileSync('scripts/backtest-replay.js','utf8'),ctx);
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(cases.map(c=>ctx.RecordedReplay.fees(c.price,c.quantity,c.buy,c.rates))));
"""
    result = subprocess.run(['node', '-e', script], cwd=root, input=json.dumps(cases),
                            capture_output=True, text=True, check=True, timeout=20)
    actual = json.loads(result.stdout)
    for case, value in zip(cases, actual):
        expected = sum(charge(case['day'], 'options', case['price'], case['quantity'],
                             'buy' if case['buy'] else 'sell').values())
        assert value == pytest.approx(expected)
