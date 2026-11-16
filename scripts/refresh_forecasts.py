"""Refresh completed broker candles and publish the daily EGARCH artifact."""
from datetime import datetime, timedelta, time
import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publish_live_state import build, write_state  # noqa: E402
from volatility_lab.core.market_data import IST, candles_to_frame  # noqa: E402
from volatility_lab.core.market_calendar import session_window  # noqa: E402
from volatility_lab.core.vol_lib import validate_ohlc  # noqa: E402
from volatility_lab.storage.references import text_digest  # noqa: E402
from volatility_lab.forecasting.expiry_forecast import sessions  # noqa: E402

KEYS = {'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank'}


def completed_session(now):
    if now.tzinfo is None:
        raise ValueError('Refresh requires a timezone-aware timestamp.')
    now = now.astimezone(IST)
    for offset in range(30):
        day = now.date() - timedelta(days=offset)
        window = session_window(day)
        if window:
            available = max(window[1] + timedelta(minutes=30), datetime.combine(day, time(16, 30), IST))
            if now >= available:
                return day
    raise ValueError('No completed exchange session found.')


def merge_candles(existing, candles, expected):
    existing = validate_ohlc(existing)
    incoming = candles_to_frame(candles, expected)
    merged = pd.concat([existing.reset_index(), incoming]).drop_duplicates('Date', keep='last')
    merged = validate_ohlc(merged)
    merged = merged.loc[merged.index <= pd.Timestamp(expected)]
    required = sessions(existing.index[-1].date() + timedelta(days=1), expected)
    if merged.index[-1].date() != expected or any(pd.Timestamp(day) not in merged.index for day in required):
        raise ValueError('Broker history is missing a completed session; retaining dated forecasts.')
    return merged.reset_index()


def fetch(url, params):
    with urlopen(url + '?' + urlencode(params), timeout=45) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('Broker relay returned an error.')
    return payload


def refresh(now=None):
    now = now or datetime.now(IST)
    expected = completed_session(now)
    config = json.loads((ROOT / 'volatility_lab/cloud/apps_script/sync_config.json').read_text())
    url = config['web_app_url']
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    snapshot = fetch(url, {})
    catalogue = snapshot.get('catalogue', {}).get('indices', {})
    # Stage both histories and model output before replacing any published files.
    with tempfile.TemporaryDirectory(prefix='vl-forecast-') as temp:
        data = Path(temp)
        shutil.copyfile(ROOT / 'volatility_lab/data/forward_test_ledger.json', data / 'forward_test_ledger.json')
        for symbol, key in KEYS.items():
            name = symbol.lower() + '_one_year.csv'
            existing = pd.read_csv(ROOT / 'volatility_lab/data' / name)
            start = pd.Timestamp(existing.Date.max()).date() - timedelta(days=7)
            payload = fetch(url, {'action': 'candles', 'instrument': key, 'interval': 'days/1',
                                  'from_date': str(start), 'to_date': str(expected)})
            frame = merge_candles(existing, payload.get('candles', []), expected)
            frame.to_csv(data / name, index=False, date_format='%Y-%m-%d')
        state = build(html, data, as_of=now, catalogue=catalogue)
        for series in state['forecasts'].values():
            if series['training_through'] != str(expected):
                raise ValueError('Forecast training date does not match the latest completed session.')
            if not any(row.get('egarch') for row in series['expiries']):
                raise ValueError('No eligible EGARCH forecasts produced.')
        state['forecast_update'] = {'completed_session': str(expected), 'generated_at': now.isoformat(),
                                    'provider': 'Upstox daily candles', 'status': 'complete'}
        for symbol in KEYS:
            name = symbol.lower() + '_one_year.csv'
            shutil.copyfile(data / name, ROOT / 'volatility_lab/data' / name)
        manifest_path = ROOT / 'volatility_lab/data/reference-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for symbol in KEYS:
            name = symbol.lower() + '_one_year.csv'
            manifest['files'][name] = text_digest((data / name).read_bytes())
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        write_state(html, state)
    print('Published EGARCH trained through', expected)


if __name__ == '__main__':
    refresh()
