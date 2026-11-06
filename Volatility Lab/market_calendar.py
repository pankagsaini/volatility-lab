"""NSE trading holiday calendar (2023-2027) with horizon check and special session support."""
from datetime import date, timedelta

class CalendarHorizonError(ValueError):
    pass

HOLIDAYS = {
    date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26), date(2026, 3, 31),
    date(2026, 4, 3), date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28),
    date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
    date(2027, 1, 26), date(2027, 8, 15), date(2027, 10, 2), date(2027, 12, 25),
}
SPECIAL_SESSIONS = {date(2026, 11, 8)}

def last_trading_day_on_or_before(value: date) -> date:
    if value.year < 2023 or value.year > 2027:
        raise CalendarHorizonError(f"Date {value} outside verified calendar horizon (2023-2027)")
    cur = value
    while (cur.weekday() >= 5 or cur in HOLIDAYS) and cur not in SPECIAL_SESSIONS:
        cur -= timedelta(days=1)
    return cur
