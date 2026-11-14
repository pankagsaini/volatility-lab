"""Market-hours refresh policy normalized explicitly to Asia/Kolkata (IST)."""
from datetime import datetime
from zoneinfo import ZoneInfo
from volatility_lab.core.market_calendar import CalendarHorizonError, session_window

IST = ZoneInfo('Asia/Kolkata')


def refresh_due(now: datetime, result=None, elapsed: float = 0.0) -> bool:
    """Evaluate whether a live or EOD refresh is due in IST regardless of host timezone."""
    if now.tzinfo is None:
        ist_now = now.replace(tzinfo=IST)
    else:
        ist_now = now.astimezone(IST)
    try:
        window = session_window(ist_now.date())
    except CalendarHorizonError:
        return False
    if window is None:
        return False
    mins = ist_now.hour * 60 + ist_now.minute
    in_live_session = window[0] <= ist_now.replace(second=0, microsecond=0) <= window[1]
    in_eod_window = 990 <= mins <= 1110    # 16:30 to 18:30 IST
    if not (in_live_session or in_eod_window):
        return False
    min_interval = 60.0 if in_live_session else 300.0
    if result is None:
        return True
    return float(elapsed) >= min_interval
