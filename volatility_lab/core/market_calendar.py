"""Centralized NSE trading calendar with 2023-2028 (including Leap Year 2028) holiday & special session support.

================================================================================
ANNUAL CALENDAR MAINTENANCE PROTOCOL:
================================================================================
1. Horizon Verification:
   - Calendar entries cover 2023-2028. The 2027-2028 entries are provisional
     and require replacement with the annual NSE circular when published.
   - Any query outside MIN_SUPPORTED_YEAR..MAX_SUPPORTED_YEAR raises `CalendarHorizonError`
     to prevent synthetic or silent calculation errors.

2. Annual Update Checklist (Each November/December):
   a. Download the official NSE Trading Holidays Circular for the upcoming calendar year
      from https://www.nseindia.com/resources/exchange-communication-circulars
   b. Add newly declared trading holidays to `HOLIDAYS` set with date(YYYY, MM, DD).
   c. Add special trading sessions (e.g. Diwali Laxmi Pujan / Muhurat Trading) to `SPECIAL_SESSIONS`.
   d. Bump `MAX_SUPPORTED_YEAR` when the next annual schedule is codified.
   e. Run `python test_audit_remediations.py` to assert calendar continuity and leap-year rules.
================================================================================
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MIN_SUPPORTED_YEAR = 2023
MAX_SUPPORTED_YEAR = 2028
LAST_SOURCED_HOLIDAY_YEAR = 2026


class CalendarHorizonError(ValueError):
    """Raised when a date query falls outside the configured NSE holiday calendar horizon."""
    pass


HOLIDAYS = {
    # 2023 NSE Holidays
    date(2023, 1, 26), date(2023, 3, 7), date(2023, 3, 30), date(2023, 4, 4),
    date(2023, 4, 7), date(2023, 4, 14), date(2023, 5, 1), date(2023, 6, 29),
    date(2023, 8, 15), date(2023, 9, 19), date(2023, 10, 2), date(2023, 10, 24),
    date(2023, 11, 14), date(2023, 11, 27), date(2023, 12, 25),
    # 2024 NSE Holidays
    date(2024, 1, 22), date(2024, 1, 26), date(2024, 3, 8), date(2024, 3, 25),
    date(2024, 3, 29), date(2024, 4, 11), date(2024, 4, 17), date(2024, 5, 1),
    date(2024, 5, 20), date(2024, 6, 17), date(2024, 7, 17), date(2024, 8, 15),
    date(2024, 10, 2), date(2024, 11, 1), date(2024, 11, 15), date(2024, 12, 25),
    # 2025 NSE Holidays
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31), date(2025, 4, 10),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 21), date(2025, 10, 22),
    date(2025, 11, 5), date(2025, 12, 25),
    # 2026 NSE Holidays (Verified per NSE Circular FAOP71777)
    date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1),
    date(2026, 5, 28), date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
    date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
    # 2027 provisional weekday holidays, awaiting the annual NSE circular
    date(2027, 1, 26), date(2027, 3, 10), date(2027, 3, 22), date(2027, 3, 26),
    date(2027, 4, 14), date(2027, 4, 15), date(2027, 4, 19), date(2027, 5, 17),
    date(2027, 6, 15), date(2027, 10, 11), date(2027, 10, 29), date(2027, 11, 1),
    # 2028 provisional weekday holidays, awaiting the annual NSE circular
    date(2028, 1, 26), date(2028, 2, 23), date(2028, 3, 13), date(2028, 3, 27),
    date(2028, 4, 4), date(2028, 4, 7), date(2028, 4, 14), date(2028, 5, 1),
    date(2028, 5, 5), date(2028, 8, 15), date(2028, 8, 23), date(2028, 9, 27),
    date(2028, 10, 2), date(2028, 10, 17), date(2028, 10, 18), date(2028, 11, 2),
    date(2028, 12, 25),
}

# Muhurat 1-hour evening sessions (optional for EOD history requirement so missing Sunday/Holiday candle never halts Monday open)
SPECIAL_SESSIONS = {
    date(2024, 11, 1),
    date(2025, 10, 21),
    date(2026, 2, 1),
    date(2026, 11, 8),
    date(2027, 10, 29),
    date(2028, 10, 17),
}

IST = ZoneInfo('Asia/Kolkata')
# Exchange continuous-session windows, excluding pre-open and trade modification.
# NSE/FAOP/70320, NSE/CMTR/72349 and NSE/FAOP/76940.
SPECIAL_SESSION_TIMES = {
    date(2025, 10, 21): (time(13, 45), time(14, 45)),
    date(2026, 2, 1): (time(9, 15), time(15, 30)),
    date(2026, 11, 8): (time(14, 20), time(15, 40)),
}


def session_window(d):
    """Return aware IST equity-derivative open/close, or None on a closed day.

    Special dates without a sourced timing schedule are withheld; their presence
    in the day calendar alone must never imply a regular daytime session.
    """
    cur = _coerce_date(d)
    if not is_trading_day(cur, include_special=True):
        return None
    if cur in SPECIAL_SESSIONS:
        times = SPECIAL_SESSION_TIMES.get(cur)
        if times is None:
            return None
    else:
        # NSE/FAOP/75472 (CAS go-live) and NSE/FAOP/77356 (CAS revision).
        closing = time(15, 30) if cur < date(2026, 8, 3) else (
            time(15, 40) if cur < date(2026, 12, 7) else time(15, 45))
        times = (time(9, 15), closing)
    return tuple(datetime.combine(cur, t, tzinfo=IST) for t in times)


def expiry_close(d):
    """Sourced close for expiring NSE index derivatives (ACT/365 valuation)."""
    cur = _coerce_date(d)
    window = session_window(cur)
    if window is None:
        raise ValueError(f'No verified expiry session timing for {cur}.')
    # From 7 Dec 2026 expiring index contracts cease at 15:30, while other
    # index derivatives continue to 15:45 (NSE/FAOP/77356).
    if cur >= date(2026, 12, 7) and cur not in SPECIAL_SESSIONS:
        return datetime.combine(cur, time(15, 30), tzinfo=IST)
    return window[1]


def _coerce_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_trading_day(d, include_special: bool = False) -> bool:
    cur = _coerce_date(d)
    if cur.year < MIN_SUPPORTED_YEAR or cur.year > MAX_SUPPORTED_YEAR:
        raise CalendarHorizonError(f"Date {cur} falls outside configured NSE holiday calendar horizon ({MIN_SUPPORTED_YEAR}-{MAX_SUPPORTED_YEAR}).")
    if include_special and cur in SPECIAL_SESSIONS:
        return True
    return cur.weekday() < 5 and cur not in HOLIDAYS


def last_trading_day_on_or_before(d, include_special: bool = False) -> date:
    cur = _coerce_date(d)
    if cur.year < MIN_SUPPORTED_YEAR or cur.year > MAX_SUPPORTED_YEAR:
        raise CalendarHorizonError(f"Date {cur} falls outside configured NSE holiday calendar horizon ({MIN_SUPPORTED_YEAR}-{MAX_SUPPORTED_YEAR}).")
    while not is_trading_day(cur, include_special=include_special):
        cur -= timedelta(days=1)
    return cur


def check_horizon_warning(as_of_date: date | datetime | None = None) -> str | None:
    """Return a warning string if current date is within 365 days of calendar horizon expiration."""
    ref_d = (
        as_of_date.date() if isinstance(as_of_date, datetime) else (as_of_date or date.today())
    )
    horizon_end = date(MAX_SUPPORTED_YEAR, 12, 31)
    days_left = (horizon_end - ref_d).days
    if days_left <= 0:
        return f"CRITICAL: NSE calendar horizon expired on {horizon_end}. Update market_calendar.py immediately."
    if days_left <= 365:
        return f"WARNING: NSE calendar horizon expires in {days_left} days ({horizon_end}). Update per annual protocol."
    return None
