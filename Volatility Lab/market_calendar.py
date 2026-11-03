"""Shared NSE trading-day helpers for the local history and pricing flows."""
from datetime import date, datetime, timedelta


HOLIDAYS = {
    date.fromisoformat(value)
    for value in '''
    2023-10-02 2023-10-24 2023-11-14 2023-11-27 2023-12-25
    2024-01-26 2024-03-08 2024-03-25 2024-03-29 2024-04-11 2024-04-17 2024-05-01
    2024-06-17 2024-07-17 2024-08-15 2024-10-02 2024-11-01 2024-11-15 2024-12-25
    2025-02-26 2025-03-14 2025-03-31 2025-04-10 2025-04-14 2025-04-18 2025-05-01
    2025-08-15 2025-08-27 2025-10-02 2025-10-21 2025-10-22 2025-11-05 2025-12-25
    2026-01-15 2026-01-26 2026-03-03 2026-03-26 2026-03-31 2026-04-03 2026-04-14 2026-05-01
    2026-05-28 2026-06-26 2026-09-14 2026-10-02 2026-10-20 2026-11-10 2026-11-24 2026-12-25
    '''.split()
}


def last_trading_day_on_or_before(value, holidays=HOLIDAYS):
    """Return the latest weekday on or before a date that is not an NSE holiday."""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        raise TypeError('Expected a date or datetime.')
    while value.weekday() > 4 or value in holidays:
        value -= timedelta(days=1)
    return value
