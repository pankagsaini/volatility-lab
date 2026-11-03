"""Bounded automatic polling; chart navigation itself never invalidates data."""
from datetime import datetime
from market_data import IST

def refresh_due(now,result,elapsed):
    if elapsed<55:return False
    now=now.astimezone(IST)
    if now.weekday()>=5:return False
    minute=now.hour*60+now.minute
    if 555<=minute<=940:return True
    # Retry the completed daily candle after its 16:30 admission boundary.
    if 990<=minute<=1110:return elapsed>=300
    # One hourly catalog/closing check outside market hours on weekdays.
    return elapsed>=3600
