"""Market-hours refresh policy normalized explicitly to Asia/Kolkata (IST)."""
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def refresh_due(now: datetime, result=None, elapsed: float = 0.0) -> bool:
    """Evaluate whether a live or EOD refresh is due in IST regardless of host timezone."""
    if now.tzinfo is None:
        ist_now = now.replace(tzinfo=IST)
    else:
        ist_now = now.astimezone(IST)
    if ist_now.weekday() >= 5:
        return False
    mins = ist_now.hour * 60 + ist_now.minute
    in_live_session = 555 <= mins <= 940   # 09:15 to 15:40 IST
    in_eod_window = 990 <= mins <= 1110    # 16:30 to 18:30 IST
    if not (in_live_session or in_eod_window):
        return False
    min_interval = 60.0 if in_live_session else 300.0
    if result is None:
        return True
    return float(elapsed) >= min_interval
