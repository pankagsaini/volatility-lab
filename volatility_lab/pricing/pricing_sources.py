"""Dated pricing inputs fetched exclusively from RBI and NSE Indices."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from math import log1p
from volatility_lab.runtime.external import read_url

from dividend_yield import SOURCES, parse_pdf
from risk_free import URL as RBI_URL, parse_rate


def fetch_official_inputs(now=None):
    from volatility_lab.runtime.settings import load_settings
    if load_settings().offline:
        raise ValueError('Official source refresh disabled in offline mode')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Pricing input check time must include timezone")
    urls = [RBI_URL, SOURCES['NIFTY'], SOURCES['BANKNIFTY']]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(read_url, host, url)
                for host, url in zip(('rbi','nse_nifty','nse_bank'), urls)]
        raw_rate, raw_nifty, raw_bank = [job.result() for job in jobs]
    yield_percent = parse_rate(raw_rate.decode('utf-8'))
    rate = {
        'yield_percent': yield_percent,
        'model_rate': log1p(yield_percent / 100 * 91 / 365) * 365 / 91,
        'basis': 'RBI 91-day T-bill latest auction cutoff; simple ACT/365 converted to continuous ACT/365',
        'source': RBI_URL,
        'checked_at': now.isoformat(),
        'sha256': sha256(raw_rate).hexdigest(),
    }
    dividends = {}
    for symbol, raw in [('NIFTY', raw_nifty), ('BANKNIFTY', raw_bank)]:
        dividends[symbol] = {
            **parse_pdf(raw, symbol, now.date()),
            'source': SOURCES[symbol], 'sha256': sha256(raw).hexdigest(),
            'checked_at': now.isoformat(),
        }
    return {'schema': 1, 'checked_at': now.isoformat(), 'rate': rate, 'dividends': dividends}
