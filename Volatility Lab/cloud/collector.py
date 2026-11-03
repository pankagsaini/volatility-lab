"""AWS Lambda: read-only raw Upstox archive. No trading or model calculations."""
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

IST = timezone(timedelta(hours=5, minutes=30))
INDICES = {'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank'}
CHUNK = 180_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def packed(value):
    return gzip.compress(json.dumps(value, separators=(',', ':'), allow_nan=False).encode(), mtime=0)


def unpacked(blob):
    return json.loads(gzip.decompress(blob))


class Archive:
    def __init__(self, table):
        self.table = table

    def read(self, day, key):
        item = self.table.get_item(Key={'pk': day, 'sk': key}, ConsistentRead=True).get('Item')
        if not item:
            return None
        parts = []
        for i in range(int(item['parts'])):
            part = self.table.get_item(Key={'pk': day, 'sk': f"blob|{item['sha256']}|{i:04d}"}, ConsistentRead=True)['Item']
            parts.append(bytes(part['payload']))
        blob = b''.join(parts)
        if hashlib.sha256(blob).hexdigest() != item['sha256']:
            raise ValueError('Archive integrity failure')
        return unpacked(blob)

    def save(self, day, key, value):
        blob = packed(value)
        digest = hashlib.sha256(blob).hexdigest()
        count = (len(blob) + CHUNK - 1) // CHUNK
        for i in range(count):
            self.table.put_item(Item={'pk': day, 'sk': f'blob|{digest}|{i:04d}', 'payload': blob[i * CHUNK:(i + 1) * CHUNK]})
        # Publish only after every chunk is durable. Readers never see half a capture.
        self.table.put_item(Item={'pk': day, 'sk': key, 'sha256': digest, 'parts': count, 'bytes': len(blob)})


class Broker:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())
        self.last = 0

    def get(self, path, params=None):
        allowed = path in ('/v2/option/chain', '/v2/option/contract', '/v2/market-quote/quotes') or path.startswith('/v3/historical-candle/')
        if not allowed or any(c in path for c in ('..', '?', '#')):
            raise ValueError('Unapproved endpoint')
        url = 'https://api.upstox.com' + path + ('?' + urlencode(params) if params else '')
        for attempt in range(3):
            time.sleep(max(0, 0.3 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            req = Request(url, headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json', 'User-Agent': 'VolatilityLab/1.0 (read-only market-data client)'})
            try:
                with self.opener.open(req, timeout=12) as response:
                    result = json.loads(response.read(20_000_000))
                if result.get('status') != 'success' or not result.get('data'):
                    raise ValueError('Empty or unsuccessful broker response')
                return result['data']
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f'Broker HTTP {exc.code}') from None
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError('Broker network failure') from None
            time.sleep(2 ** attempt)

    def master(self):
        req = Request('https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz', headers={'User-Agent': 'VolatilityLab/1.0'})
        with self.opener.open(req, timeout=30) as response:
            rows = json.loads(gzip.decompress(response.read(40_000_000)))
        return [r for r in rows if r.get('underlying_key') in INDICES.values() and r.get('instrument_type') == 'FUT']


def expiry_date(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, IST).date().isoformat()
    return str(value)[:10]


def catalogue(broker, day):
    master = broker.master()
    result = {}
    for symbol, key in INDICES.items():
        futures = sorted((dict(r, expiry=expiry_date(r['expiry'])) for r in master if r['underlying_key'] == key and expiry_date(r['expiry']) >= day), key=lambda r: r['expiry'])[:3]
        if len(futures) < 3:
            raise ValueError('Three listed futures not available: ' + symbol)
        contracts = broker.get('/v2/option/contract', {'instrument_key': key})
        dates = sorted({r['expiry'] for r in contracts if day <= r['expiry'] <= futures[-1]['expiry']})
        if symbol == 'BANKNIFTY':
            dates = [d for d in dates if d in {r['expiry'] for r in futures}]
        if not dates:
            raise ValueError('No listed options: ' + symbol)
        result[symbol] = {'futures': futures, 'option_expiries': dates}
    return result


def collect(broker, archive, now, context, mode='snapshot'):
    day = now.date().isoformat()
    slot = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0).isoformat()
    key = f'capture|{slot}|{mode}'
    previous = archive.read(day, key)
    if previous and previous.get('status') == 'complete':
        return {'status': 'already_saved', 'slot': slot}
    cat = archive.read(day, 'catalogue')
    if not cat:
        cat = catalogue(broker, day)
        archive.save(day, 'catalogue', cat)
    report = {'schema': 1, 'provider': 'Upstox', 'retrieved_at': now.isoformat(), 'slot': slot, 'mode': mode, 'status': 'complete', 'records': [], 'errors': [], 'pending': []}

    def fetch(label, path, params=None):
        if context and context.get_remaining_time_in_millis() < 45000:
            report['errors'].append({'item': label, 'reason': 'Runtime budget exhausted'})
            return None
        try:
            raw = broker.get(path, params)
            objkey = f'data|{slot}|{mode}|{label}'
            archive.save(day, objkey, {'provider': 'Upstox', 'retrieved_at': datetime.now(IST).isoformat(), 'data': raw})
            report['records'].append(objkey)
            return raw
        except Exception as exc:
            # Never log broker response bodies, headers, or credential strings.
            reason = str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith('Broker ') else type(exc).__name__
            report['errors'].append({'item': label, 'reason': reason})
            return None

    allkeys = list(INDICES.values()) + ['NSE_INDEX|India VIX']
    for row in cat.values():
        allkeys.extend(r['instrument_key'] for r in row['futures'])
    fetch('quotes', '/v2/market-quote/quotes', {'instrument_key': ','.join(allkeys)})
    if mode == 'snapshot':
        for symbol, row in cat.items():
            for expiry in row['option_expiries']:
                fetch(symbol + '|' + expiry, '/v2/option/chain', {'instrument_key': INDICES[symbol], 'expiry_date': expiry})
    else:
        for instrument in allkeys:
            # Overlap revisits pending EOD candles; no fabricated IV history.
            start = (now.date() - timedelta(days=10)).isoformat()
            data = fetch(instrument, '/v3/historical-candle/' + quote(instrument, safe='') + '/days/1/' + day + '/' + start)
            if data and not any(str(r[0])[:10] == day for r in data.get('candles', [])):
                report['pending'].append(instrument)
    report['status'] = 'partial' if report['errors'] or report['pending'] else 'complete'
    archive.save(day, key, report)
    archive.save('status', 'latest|' + mode, report)
    return report


def handler(event, context):
    import boto3
    from botocore.config import Config
    now = datetime.now(IST)
    if now.date().isoformat() >= os.environ['STOP_DATE']:
        return {'status': 'stopped_before_free_plan_expiry'}
    if now.weekday() >= 5:
        return {'status': 'weekend'}
    mode = event.get('mode', 'snapshot')
    if mode not in ('snapshot', 'eod'):
        raise ValueError('Invalid capture mode')
    if mode == 'snapshot' and not (9 * 60 <= now.hour * 60 + now.minute <= 16 * 60):
        return {'status': 'outside_capture_window'}
    config = Config(retries={'mode': 'adaptive', 'max_attempts': 8})
    token = boto3.client('ssm', config=config).get_parameter(Name=os.environ['TOKEN_PARAMETER'], WithDecryption=True)['Parameter']['Value']
    table = boto3.resource('dynamodb', config=config).Table(os.environ['TABLE_NAME'])
    result = collect(Broker(token), Archive(table), now, context, mode)
    print(json.dumps({'status': result['status'], 'mode': mode, 'errors': len(result.get('errors', [])), 'pending': len(result.get('pending', []))}))
    return {'status': result['status'], 'mode': mode}
