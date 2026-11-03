from datetime import date

from expiry_forecast import sessions
from market_calendar import HOLIDAYS


def test_2026_maharashtra_election_holiday_is_not_counted_as_a_session():
    assert date(2026, 1, 15) in HOLIDAYS
    assert sessions(date(2026, 1, 14), date(2026, 1, 16)) == [
        date(2026, 1, 14),
        date(2026, 1, 16),
    ]
