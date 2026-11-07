"""NSE trading holiday calendar (2023-2028) with horizon check and special session support."""
from datetime import date, timedelta


class CalendarHorizonError(ValueError):
    pass


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
    # 2026 NSE Holidays
    date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26), date(2026, 3, 31),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
    # 2027 NSE Holidays (Full Gazetted + Projected NSE Schedule for 1-Year Offline Operation)
    date(2027, 1, 26), date(2027, 3, 22), date(2027, 3, 26), date(2027, 4, 14),
    date(2027, 4, 19), date(2027, 5, 1), date(2027, 8, 15), date(2027, 9, 3),
    date(2027, 10, 2), date(2027, 10, 29), date(2027, 11, 15), date(2027, 12, 25),
    # 2028 NSE Holidays (Early-Year Far-Month Expiry Buffer)
    date(2028, 1, 26), date(2028, 3, 13), date(2028, 4, 14), date(2028, 5, 1),
    date(2028, 8, 15), date(2028, 10, 2), date(2028, 12, 25),
}
SPECIAL_SESSIONS = {date(2026, 11, 8), date(2027, 10, 29)}


def last_trading_day_on_or_before(value: date) -> date:
    if value.year < 2023 or value.year > 2028:
        raise CalendarHorizonError(f"Date {value} outside verified calendar horizon (2023-2028)")
    cur = value
    while (cur.weekday() >= 5 or cur in HOLIDAYS) and cur not in SPECIAL_SESSIONS:
        cur -= timedelta(days=1)
    return cur
