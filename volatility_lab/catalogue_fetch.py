"""Public catalogue fetch with bounded retries and credential-free diagnostics."""
import gzip
import json
import time
from datetime import datetime, timezone
from urllib.request import Request
from collector_support import atomic_json
from market_data import MarketDataError

URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz'

def refresh(opener, path, day, indices):
    attempts = []
    for attempt in range(3):
        phase = 'download'
        try:
            with opener.open(Request(URL, headers={'User-Agent': 'VolatilityLab/1.0'}), timeout=40) as response:
                raw = response.read(40_000_001)
            phase = 'decode'
            if len(raw) > 40_000_000:
                raise ValueError('Oversized catalogue')
            rows = json.loads(gzip.decompress(raw))
            phase = 'validate'
            if not isinstance(rows, list):
                raise ValueError('Invalid catalogue shape')
            keys = set(indices.values())
            rows = [r for r in rows if r.get('underlying_key') in keys or r.get('instrument_key') in keys | {'NSE_INDEX|India VIX'}]
            if not keys.issubset({r.get('instrument_key') for r in rows}):
                raise ValueError('Missing indices')
            data = {'date': day, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                    'provider': 'Upstox', 'instruments': rows}
            phase = 'save'
            atomic_json(path, data)
            record(path, {'status': 'complete', 'attempts': attempts, 'records': len(rows)})
            return data
        except Exception as exc:
            # Never persist exception text, headers, URLs or broker response bodies.
            item = {'attempt': attempt + 1, 'phase': phase, 'error_type': type(exc).__name__}
            code = getattr(exc, 'code', None)
            if isinstance(code, int):
                item['http_status'] = code
            attempts.append(item)
            record(path, {'status': 'retrying' if attempt < 2 else 'failed', 'attempts': attempts})
            if attempt < 2:
                time.sleep(2 ** attempt)
    last = attempts[-1]
    detail = last['phase'] + ': ' + last['error_type']
    if 'http_status' in last:
        detail += ' HTTP ' + str(last['http_status'])
    raise MarketDataError('Upstox instrument catalogue refresh failed after 3 attempts (' + detail + '). Previous cache retained.')

def record(path, event):
    event = dict(event, updated=datetime.now(timezone.utc).isoformat())
    try:
        # Append preserves failures even when a subsequent refresh succeeds.
        with (path.parent / 'catalogue_refresh_events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
    except OSError:
        pass
