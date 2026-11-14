"""Publish dated, read-only desk references from the canonical local archive."""
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from volatility_lab.backtest.forward_test import sanitize_forward_ledger  # noqa: E402
from volatility_lab.core.vol_lib import rv_snapshot  # noqa: E402
from volatility_lab.forecasting.expiry_forecast import term_structure_forecast  # noqa: E402


def build(html, data):
    catalog = json.loads(re.search(r'var marketCatalog = (.*);', html)[1])
    ledger_path = data / 'forward_test_ledger.json'
    ledger = sanitize_forward_ledger(json.loads(ledger_path.read_bytes()))
    ledger['source_authority'] = 'dated_archive'
    if any(row.get('integrity_status') == 'UNVERIFIED_RECORD' for row in ledger['closed_trades'] + ledger['open_positions']):
        ledger['intraday_mtm_series'] = []
    for bucket in ['open_positions', 'closed_trades']:
        for index, record in enumerate(ledger[bucket]):
            if record.get('integrity_status') == 'UNVERIFIED_RECORD':
                fields = ['date', 'session_date', 'sym', 'exp', 'strat', 'integrity_status', 'provenance_status', 'frontScope']
                ledger[bucket][index] = {key: record[key] for key in fields if key in record}
    state = {'schema': 1, 'ledger': ledger, 'forecasts': {}, 'sources': {}}
    for symbol, rows in catalog.items():
        path = data / f'{symbol.lower()}_one_year.csv'
        frame = pd.read_csv(path).sort_values('Date')
        # Never include a close which was unavailable at the catalog's capture time.
        captured = min(pd.Timestamp(row['ivCapturedAt']).tz_convert('Asia/Kolkata') for row in rows)
        close_times = pd.to_datetime(frame['Date'] + ' 16:15').dt.tz_localize('Asia/Kolkata')
        frame = frame.loc[close_times <= captured].copy()
        through = str(frame['Date'].iloc[-1])
        anchor = pd.Timestamp(f'{through} 16:15', tz='Asia/Kolkata')
        risk = rv_snapshot(frame)
        series = {'training_through': through, 'yang_zhang20': 100 * risk['yang_zhang_rv'], 'expiries': []}
        for row in rows:
            point = {'expiry': row['expiry'], 'as_of': anchor.isoformat(), 'training_through': through}
            try:
                model = term_structure_forecast(frame, row['expiry'], anchor)
            except ValueError as exc:
                point['status'] = str(exc)
            else:
                point['as_of'] = model['Forecast as of']
                point['status'] = model['GARCH status']
                for key, label in [('garch', 'GARCH expiry RV'), ('ewma', 'EWMA expiry RV')]:
                    value = model[label]
                    point[key] = round(value, 7) if math.isfinite(value) else None
            series['expiries'].append(point)
        state['forecasts'][symbol] = series
        state['sources'][path.name] = hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
    state['sources'][ledger_path.name] = hashlib.sha256(ledger_path.read_text(encoding='utf-8').encode()).hexdigest()
    return state


def publish():
    page = ROOT / 'index.html'
    html = page.read_text(encoding='utf-8')
    state = build(html, ROOT / 'volatility_lab' / 'data')
    compact = json.dumps(state, ensure_ascii=True, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')
    pattern = r'(<script type="application/json" id="deskPublicState">).*?(</script>)'
    html, count = re.subn(pattern, lambda m: m[1] + compact + m[2], html, flags=re.S)
    if count != 1:
        raise ValueError('Expected exactly one embedded desk state.')
    (ROOT / 'docs' / 'live_state.json').write_text(compact + '\n', encoding='utf-8', newline='\n')
    for target in [page, ROOT / 'docs' / 'index.html']:
        target.write_text(html, encoding='utf-8', newline='\r\n')


if __name__ == '__main__':
    publish()
