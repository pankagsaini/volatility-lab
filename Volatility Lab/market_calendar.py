"""Centralized NSE trading calendar with 2023-2028 (including Leap Year 2028) holiday and special session support."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MIN_SUPPORTED_YEAR = 2023
MAX_SUPPORTED_YEAR = 2028
IST = ZoneInfo('Asia/Kolkata')


class CalendarHorizonError(ValueError):
    """Raised when a date query falls outside the verified NSE holiday calendar horizon."""
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
    # 2027 NSE Weekday Holidays (Full 1-Year Offline Coverage)
    date(2027, 1, 26), date(2027, 3, 10), date(2027, 3, 22), date(2027, 3, 26),
    date(2027, 4, 14), date(2027, 4, 15), date(2027, 4, 19), date(2027, 5, 17),
    date(2027, 6, 15), date(2027, 10, 11), date(2027, 10, 29), date(2027, 11, 1),
    # 2028 NSE Weekday Holidays (Leap Year Coverage)
    date(2028, 1, 26), date(2028, 2, 23), date(2028, 3, 13), date(2028, 3, 27),
    date(2028, 4, 4), date(2028, 4, 7), date(2028, 4, 14), date(2028, 5, 1),
    date(2028, 5, 5), date(2028, 8, 15), date(2028, 8, 23), date(2028, 9, 27),
    date(2028, 10, 2), date(2028, 10, 17), date(2028, 10, 18), date(2028, 11, 2),
    date(2028, 12, 25),
}

# Recognized NSE Special / Muhurat Trading Sessions with verified timing windows (IST)
EXTRA_SESSIONS = {
    date(2024, 11, 1): {
        "name": "Diwali Muhurat Trading 2024",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2025, 10, 21): {
        "name": "Diwali Muhurat Trading 2025",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2026, 11, 8): {
        "name": "Diwali Muhurat Trading 2026 (14:00-16:00 IST)",
        "pre_open_start": (14, 5),
        "trading_start": (14, 20),
        "trading_end": (15, 40),
        "snap_start_min": 840,
        "snap_end_min": 960,
        "eod_start_min": 960,
        "eod_end_min": 1320,
    },
    date(2027, 10, 29): {
        "name": "Diwali Muhurat Trading 2027",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2028, 10, 17): {
        "name": "Diwali Muhurat Trading 2028",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
}

class SpecialSessionsSet(set):
    """Set subclass supporting dictionary lookup and metadata access for backwards compatibility."""
    def __getitem__(self, item):
        return EXTRA_SESSIONS[item]
    def get(self, item, default=None):
        return EXTRA_SESSIONS.get(item, default)
    def items(self):
        return EXTRA_SESSIONS.items()
    def values(self):
        return EXTRA_SESSIONS.values()

SPECIAL_SESSIONS = SpecialSessionsSet(EXTRA_SESSIONS.keys())

_LEGACY_SPECIAL_SESSIONS = {
    date(2024, 11, 1): {
        "name": "Diwali Muhurat Trading 2024",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2025, 10, 21): {
        "name": "Diwali Muhurat Trading 2025",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2026, 11, 8): {
        "name": "Diwali Muhurat Trading 2026 (14:00-16:00 IST)",
        "pre_open_start": (14, 5),
        "trading_start": (14, 20),
        "trading_end": (15, 40),
        "snap_start_min": 840,
        "snap_end_min": 960,
        "eod_start_min": 960,
        "eod_end_min": 1320,
    },
    date(2027, 10, 29): {
        "name": "Diwali Muhurat Trading 2027",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
    date(2028, 10, 17): {
        "name": "Diwali Muhurat Trading 2028",
        "pre_open_start": (18, 0),
        "trading_start": (18, 15),
        "trading_end": (19, 15),
        "snap_start_min": 1080,
        "snap_end_min": 1170,
        "eod_start_min": 1170,
        "eod_end_min": 1320,
    },
}


def _coerce_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_special_session(d) -> bool:
    """Check if given date hosts an authorized special session (e.g. Diwali Muhurat)."""
    cur = _coerce_date(d)
    return cur in SPECIAL_SESSIONS


def special_session_info(d) -> dict | None:
    """Retrieve timing and name metadata for a special trading session date."""
    cur = _coerce_date(d)
    return SPECIAL_SESSIONS.get(cur)


def is_trading_day(d, include_special: bool = True) -> bool:
    """Return True if date is an active NSE trading day (including Muhurat special sessions by default)."""
    cur = _coerce_date(d)
    if cur.year < MIN_SUPPORTED_YEAR or cur.year > MAX_SUPPORTED_YEAR:
        raise CalendarHorizonError(f"Date {cur} falls outside verified NSE holiday calendar horizon ({MIN_SUPPORTED_YEAR}-{MAX_SUPPORTED_YEAR}).")
    if include_special and cur in SPECIAL_SESSIONS:
        return True
    return cur.weekday() < 5 and cur not in HOLIDAYS


def last_trading_day_on_or_before(d, include_special: bool = False) -> date:
    """Find the most recent trading day on or before d.
    include_special defaults to False to prevent optional 1-hour Muhurat sessions from halting EOD series.
    """
    cur = _coerce_date(d)
    if cur.year < MIN_SUPPORTED_YEAR or cur.year > MAX_SUPPORTED_YEAR:
        raise CalendarHorizonError(f"Date {cur} falls outside verified NSE holiday calendar horizon ({MIN_SUPPORTED_YEAR}-{MAX_SUPPORTED_YEAR}).")
    while not is_trading_day(cur, include_special=include_special):
        cur -= timedelta(days=1)
    return cur


def is_market_open_now(dt: datetime | None = None) -> tuple[bool, str]:
    """Check whether NSE is currently actively trading (regular session or Muhurat special session).
    Returns (is_open: bool, session_type: str).
    """
    now = dt or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    cur_date = now.date()
    mins = now.hour * 60 + now.minute

    if cur_date in SPECIAL_SESSIONS:
        info = SPECIAL_SESSIONS[cur_date]
        pre_start = info.get("pre_open_start", (18, 0))
        trade_start = info.get("trading_start", (18, 15))
        trade_end = info.get("trading_end", (19, 15))
        snap_end = info.get("snap_end_min", 1170)

        pre_min = pre_start[0] * 60 + pre_start[1]
        trade_start_min = trade_start[0] * 60 + trade_start[1]
        trade_end_min = trade_end[0] * 60 + trade_end[1]

        if pre_min <= mins < trade_start_min:
            return True, 'SPECIAL_PRE_OPEN'
        elif trade_start_min <= mins <= trade_end_min:
            return True, 'SPECIAL_LIVE'
        elif trade_end_min < mins <= snap_end:
            return True, 'SPECIAL_CLOSING'
        return False, 'SPECIAL_CLOSED'

    if cur_date.weekday() < 5 and cur_date not in HOLIDAYS:
        if 540 <= mins < 555:
            return True, 'REGULAR_PRE_OPEN'
        elif 555 <= mins <= 930:
            return True, 'REGULAR_LIVE'
        elif 930 < mins <= 960:
            return False, 'REGULAR_CLOSING'
        return False, 'REGULAR_CLOSED'

    return False, 'CLOSED'
