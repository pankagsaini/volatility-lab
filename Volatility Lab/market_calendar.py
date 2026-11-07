"""Centralized NSE trading calendar with 2024-2028 (including Leap Year 2028) holiday & special session support."""
from datetime import date, timedelta

HOLIDAYS = {
    # 2024 NSE Holidays
    date(2024, 1, 22), date(2024, 1, 26), date(2024, 3, 8), date(2024, 3, 25),
    date(2024, 3, 29), date(2024, 4, 11), date(2024, 4, 17), date(2024, 5, 1),
    date(2024, 5, 20), date(2024, 6, 17), date(2024, 7, 17), date(2024, 8, 15),
    date(2024, 10, 2), date(2024, 11, 1), date(2024, 11, 15), date(2024, 11, 20),
    date(2024, 12, 25),
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

# Muhurat 1-hour evening sessions (optional for EOD history requirement so missing Sunday/Holiday candle never halts Monday open)
SPECIAL_SESSIONS = {
    date(2024, 11, 1),
    date(2025, 10, 21),
    date(2026, 11, 8),
    date(2027, 10, 29),
    date(2028, 10, 17),
}


def is_trading_day(d: date, include_special: bool = False) -> bool:
    if include_special and d in SPECIAL_SESSIONS:
        return True
    return d.weekday() < 5 and d not in HOLIDAYS


def last_trading_day_on_or_before(d: date, include_special: bool = False) -> date:
    cur = d
    while not is_trading_day(cur, include_special=include_special):
        cur -= timedelta(days=1)
    return cur
