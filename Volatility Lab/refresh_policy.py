"""Market-hours refresh policy normalized explicitly to Asia/Kolkata (IST)."""
from datetime import datetime
from zoneinfo import ZoneInfo
from market_calendar import SPECIAL_SESSIONS, HOLIDAYS

IST = ZoneInfo('Asia/Kolkata')


def refresh_due(now: datetime, result=None, elapsed: float = 0.0) -> bool:
    """Evaluate whether a live or EOD refresh is due in IST regardless of host timezone."""
    if now.tzinfo is None:
        ist_now = now.replace(tzinfo=IST)
    else:
        ist_now = now.astimezone(IST)

    cur_date = ist_now.date()
    mins = ist_now.hour * 60 + ist_now.minute

    # Check for special trading sessions (e.g. Diwali Muhurat Trading)
    if cur_date in SPECIAL_SESSIONS:
        info = SPECIAL_SESSIONS[cur_date]
        snap_start = info.get("snap_start_min", 1080)  # 18:00 IST
        snap_end = info.get("snap_end_min", 1170)      # 19:30 IST
        eod_start = info.get("eod_start_min", 1170)    # 19:30 IST
        eod_end = info.get("eod_end_min", 1320)        # 22:00 IST

        in_live_session = snap_start <= mins <= snap_end
        in_eod_window = eod_start <= mins <= eod_end
        if not (in_live_session or in_eod_window):
            return False
        min_interval = 60.0 if in_live_session else 300.0
        if result is None:
            return True
        return float(elapsed) >= min_interval

    if ist_now.weekday() >= 5:
        return False
    if cur_date in HOLIDAYS:
        return False

    in_live_session = 555 <= mins <= 940   # 09:15 to 15:40 IST
    in_eod_window = 990 <= mins <= 1110    # 16:30 to 18:30 IST
    if not (in_live_session or in_eod_window):
        return False
    min_interval = 60.0 if in_live_session else 300.0
    if result is None:
        return True
    return float(elapsed) >= min_interval
