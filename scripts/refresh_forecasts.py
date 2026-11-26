"""Refresh completed broker candles and publish the daily EGARCH artifact."""
from datetime import datetime, timedelta, time
import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publish_live_state import build, write_state  # noqa: E402
from scripts.public_artifact import fetch_json  # noqa: E402
from volatility_lab.core.market_data import IST, candles_to_frame  # noqa: E402
from volatility_lab.core.market_calendar import expiry_close, session_window  # noqa: E402
from volatility_lab.ingestion.google_archive_sync import validate_report  # noqa: E402
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
    request = Request(url + ('?' + urlencode(params) if params else ''), headers={
        'User-Agent': 'VolatilityLab/1.0 (daily candle publisher; +https://volatilitylab.in)',
        'Accept': 'application/json',
    })
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('Broker relay returned an error.')
    return payload


def refresh_daily_candles(data, expected, now=None):
    completed = completed_session(now or datetime.now(IST))
    if expected > completed:
        raise ValueError('Cannot publish an unfinished exchange session.')
    for symbol, key in KEYS.items():
        name = symbol.lower() + '_one_year.csv'
        existing = pd.read_csv(ROOT / 'volatility_lab/data' / name)
        start = pd.Timestamp(existing.Date.max()).date() - timedelta(days=7)
        instrument = quote(key, safe='')
        endpoint = f'https://api.upstox.com/v3/historical-candle/{instrument}/days/1/{expected}/{start}'
        response = fetch(endpoint, {})
        if response.get('status') != 'success':
            raise ValueError('Daily candle request failed')
        payload = response.get('data') or {}
        candles = list(payload.get('candles') or [])
        if expected == completed and not any(str(row[0]).startswith(str(expected)) for row in candles):
            # The historical feed and the current-session feed roll over independently.
            # Across midnight, accept only the daily candle for the completed exchange session.
            intraday = fetch(f'https://api.upstox.com/v3/historical-candle/intraday/{instrument}/days/1', {})
            if intraday.get('status') == 'success':
                bars = (intraday.get('data') or {}).get('candles') or []
                if len(bars) == 1:
                    stamp = pd.Timestamp(bars[0][0])
                    if stamp.tzinfo is not None and stamp.tz_convert(IST).date() == expected:
                        candles.append(bars[0])
        frame = merge_candles(existing, candles, expected)
        frame.to_csv(data / name, index=False, date_format='%Y-%m-%d')


def write_public_snapshot(snapshot, output, now):
    if snapshot.get('mode') != 'snapshot' or snapshot.get('pending') or snapshot.get('errors'):
        raise ValueError('No complete broker snapshot')
    fields = ('provider', 'schema', 'status', 'mode', 'nfo_session_validated', 'day', 'retrieved_at',
              'records', 'catalogue', 'risk_metrics', 'exchange_calendar', 'daily_history', 'closing_risk')
    saved = {key: snapshot[key] for key in fields if key in snapshot}
    validate_report(saved)
    captured = pd.Timestamp(saved['retrieved_at'])
    if captured > now + timedelta(seconds=30):
        raise ValueError('Snapshot timestamp is in the future')
    active = set()
    for record in saved['records']:
        symbol, _, expiry = record['label'].partition('|')
        if symbol not in KEYS or not expiry:
            continue
        # The desk rejects a snapshot if any option chain was stale when captured.
        times = (record['retrieved_at'], record.get('chain_retrieved_at') or record['retrieved_at'])
        if any(not -30 <= (captured - pd.Timestamp(value)).total_seconds() <= 120 for value in times):
            raise ValueError('Option chain outside the desk capture window')
        expiry_day = datetime.strptime(expiry, '%Y-%m-%d').date()
        if expiry_day > captured.astimezone(IST).date() or (
                expiry_day == captured.astimezone(IST).date() and captured < expiry_close(expiry_day)):
            active.add(symbol)
    if active != set(KEYS):
        raise ValueError('Snapshot lacks an active option chain for both indices')
    output.write_text(json.dumps(saved, separators=(',', ':'), allow_nan=False), encoding='utf-8')


def retain_snapshot(output):
    previous = fetch_json('latest-snapshot.json', 20_000_000)
    write_public_snapshot(previous, output, datetime.now(IST))
    print('Retained broker snapshot captured at', previous['retrieved_at'])


def refresh(now=None, paper_history=None):
    now = now or datetime.now(IST)
    expected = completed_session(now)
    config = json.loads((ROOT / 'volatility_lab/cloud/apps_script/sync_config.json').read_text())
    url = config['web_app_url']
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    # Stage both histories and model output before replacing any published files.
    with tempfile.TemporaryDirectory(prefix='vl-forecast-') as temp:
        data = Path(temp)
        # The paper worker can consume the same verified daily candles when the
        # independent live catalogue or forecast step is unavailable.
        candle_error = None
        try:
            refresh_daily_candles(data, expected, now)
            if paper_history is not None:
                from scripts.paper_risk_history import write_bundle
                write_bundle(data, paper_history)
        except Exception as exc:
            candle_error = exc
        snapshot = fetch(url, {})
        # Publish the same-origin snapshot even when the candle request failed.
        try:
            write_public_snapshot(snapshot, ROOT / 'docs/latest-snapshot.json', now)
        except (KeyError, TypeError, ValueError) as exc:
            print('Current response cannot supply the saved dashboard snapshot:', exc)
        if candle_error is not None:
            raise candle_error
        catalogue = snapshot.get('catalogue', {}).get('indices', {})
        shutil.copyfile(ROOT / 'volatility_lab/data/forward_test_ledger.json', data / 'forward_test_ledger.json')
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
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--paper-history', type=Path)
    mode.add_argument('--retain-snapshot', type=Path)
    args = parser.parse_args()
    if args.retain_snapshot:
        retain_snapshot(args.retain_snapshot)
    else:
        refresh(paper_history=args.paper_history)
