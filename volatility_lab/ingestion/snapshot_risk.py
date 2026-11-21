"""Compute dated prior-close risk inputs outside the minute collector."""
from datetime import timedelta
import math
from statistics import stdev


def prior_close_metrics(frame, session):
    """Use the same trailing 100 calendar days and sample variance as legacy snapshots."""
    if frame is None:
        return {}
    start = (session - timedelta(days=100)).isoformat()
    frame = frame.loc[(frame.Date >= start) & (frame.Date < session.isoformat())].sort_values("Date")
    if len(frame) < 31 or frame.Date.duplicated().any():
        return {}
    last = frame.Date.iloc[-1]
    if last < (session - timedelta(days=5)).isoformat():
        return {}
    closes = [float(value) for value in frame.Close]
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        return {}
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    variance = returns[0] ** 2
    for value in returns[1:]:
        variance = 0.94 * variance + 0.06 * value ** 2
    scale = math.sqrt(245) * 100
    return {
        "as_of": last, "units": "annualized_percent", "close": closes[-1],
        "source": "Lab daily close history", "ewma": math.sqrt(variance) * scale,
        **{f"hv{n}": stdev(returns[-n:]) * scale for n in (10, 20, 30)},
    }
