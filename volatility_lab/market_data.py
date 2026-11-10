"""Provider-neutral helpers shared by read-only market-data clients."""
from datetime import datetime, time, timedelta, timezone
from urllib.request import HTTPRedirectHandler

import pandas as pd

from vol_lib import validate_ohlc

IST = timezone(timedelta(hours=5, minutes=30))


class MarketDataError(Exception):
    """Safe user-facing market-data errors without raw provider responses."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward authorization headers to a redirect destination.
        return None


def history_cutoff(now=None):
    """Admit a provider daily candle after a conservative 16:30 IST buffer."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    local = now.astimezone(IST)
    return local.date() if local.time() >= time(16, 30) else local.date() - timedelta(days=1)


def candles_to_frame(candles, last_allowed_date):
    """Validate provider daily candles and return the common OHLC frame."""
    if not candles:
        raise MarketDataError('Provider returned no daily candles.')
    rows = []
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 6:
            raise MarketDataError('Unexpected daily candle format.')
        stamp = pd.Timestamp(candle[0])
        if stamp.tzinfo is None:
            raise MarketDataError('Daily candle timestamps must specify a timezone.')
        day = stamp.tz_convert(IST).date()
        if day > last_allowed_date:
            continue
        rows.append([day.isoformat(), *candle[1:5]])
    frame = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close'])
    try:
        return validate_ohlc(frame).reset_index()
    except ValueError:
        raise MarketDataError('Historical OHLC/date validation failed.') from None
