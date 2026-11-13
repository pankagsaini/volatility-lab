"""Resumable futures backtest downloader with complete 'data' payload caching and clean status handling."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import quote
import gzip
import hashlib
import json
try:
    import msvcrt
except ImportError:
    msvcrt = None
from collector_support import atomic_json

ROOT = runtime_data_dir() / 'futures_backtest'
ROOT.mkdir(parents=True, exist_ok=True)


def status(provider, state, **extra):
    path = ROOT / f'{provider}_status.json'
    payload = {'provider': provider, 'status': state, 'updated': datetime.now().astimezone().isoformat(), **extra}
    atomic_json(path, payload)
    return payload


def fetch(provider, api_fn, path, params, meta=None):
    ident = hashlib.sha256(json.dumps([provider, path, params], sort_keys=True).encode()).hexdigest()
    file = ROOT / f'{ident}.json.gz'
    if file.exists():
        with gzip.open(file, 'rt', encoding='utf-8') as f:
            cached = json.load(f)
            if 'data' in cached:
                return cached['data']
    data = api_fn(path, params)
    body = {
        'provider': provider,
        'path': path,
        'params': params,
        'meta': meta or {},
        'retrieved_at': datetime.now().astimezone().isoformat(),
        'data': data,
    }
    temp = file.with_suffix('.tmp')
    temp.write_bytes(gzip.compress(json.dumps(body).encode('utf-8')))
    temp.replace(file)
    return data


def upstox(start, end, collector=None):
    from upstox_archive import Collector, INDICES
    col = collector or Collector()
    status('upstox', 'running', start=str(start), end=str(end))
    try:
        results = {}
        for symbol, idx in INDICES.items():
            path = f'/v2/historical-candle/{quote(idx, safe="")}/day/{end.isoformat()}/{start.isoformat()}'
            results[symbol] = fetch('upstox', col.call, path, {}, meta={'symbol': symbol, 'start': str(start), 'end': str(end)})
        status('upstox', 'completed', start=str(start), end=str(end))
        return results
    except BaseException as err:
        final_state = 'stopped' if isinstance(err, (KeyboardInterrupt, SystemExit)) else 'error'
        status('upstox', final_state, error_type=type(err).__name__)
        raise
