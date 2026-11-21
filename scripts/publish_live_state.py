"""Publish dated, read-only desk references from the canonical local archive."""
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
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
from volatility_lab.ingestion.snapshot_risk import prior_close_metrics  # noqa: E402
from volatility_lab.core.vol_lib import rv_snapshot  # noqa: E402
from volatility_lab.core.market_calendar import session_window  # noqa: E402
from volatility_lab.forecasting.expiry_forecast import term_structure_forecast  # noqa: E402


def build(html, data, as_of=None, catalogue=None):
    catalog = json.loads(re.search(r'var marketCatalog = (.*);', html)[1])
    if catalogue:
        for symbol, entry in catalogue.items():
            if symbol in catalog:
                known = {row['expiry'] for row in catalog[symbol]}
                catalog[symbol].extend({'expiry': day} for day in entry.get('option_expiries', []) if day not in known)
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
        # Forecasts have their own as-of date. Browser comparisons independently
        # require IV and forecast timestamps to align.
        captured = pd.Timestamp(as_of or datetime.now(ZoneInfo('Asia/Kolkata'))).tz_convert('Asia/Kolkata')
        window = session_window(captured.date())
        available = pd.Timestamp(f'{captured.date()} 16:30', tz='Asia/Kolkata')
        if window:
            available = max(available, pd.Timestamp(window[1]) + pd.Timedelta(minutes=30))
        cutoff = captured.date() if window and captured >= available else (captured - pd.Timedelta(days=1)).date()
        frame = frame.loc[frame.Date <= str(cutoff)].copy()
        through = str(frame['Date'].iloc[-1])
        closing_window = session_window(through)
        if closing_window is None:
            raise ValueError('No verified exchange close for latest price history.')
        anchor = pd.Timestamp(closing_window[1])
        risk = rv_snapshot(frame)
        series = {'training_through': through, 'yang_zhang20': 100 * risk['yang_zhang_rv'], 'expiries': []}
        series['closing_risk'] = prior_close_metrics(frame, date.fromisoformat(through) + timedelta(days=1))
        for row in sorted(rows, key=lambda entry: entry['expiry']):
            if row['expiry'] <= through:
                continue
            point = {'expiry': row['expiry'], 'as_of': anchor.isoformat(), 'training_through': through}
            try:
                model = term_structure_forecast(frame, row['expiry'], anchor)
            except ValueError as exc:
                point['status'] = str(exc)
            else:
                point['as_of'] = model['Forecast as of']
                point['status'] = model['EGARCH status']
                point.update(model=model['Model'], sessions=model['Trading sessions'], training_returns=model['Training returns'], calendar_status=model['Calendar status'], simulations=model['Simulations'], seed=model['Simulation seed'])
                for key, label in [('egarch', 'EGARCH expiry RV')]:
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
    write_state(html, state)


def write_state(html, state):
    page = ROOT / 'index.html'
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
