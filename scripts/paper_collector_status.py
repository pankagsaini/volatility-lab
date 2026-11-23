"""Read the collector's public runtime counters for the hosted session audit."""

from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests

from volatility_lab.core.market_calendar import IST


CONFIG = Path(__file__).resolve().parents[1] / "volatility_lab/cloud/apps_script/sync_config.json"
MAX_BYTES = 128 * 1024
DEPLOYMENT = re.compile(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec")


def _read():
    endpoint = json.loads(CONFIG.read_bytes())["web_app_url"]
    if not isinstance(endpoint, str) or not DEPLOYMENT.fullmatch(endpoint):
        raise ValueError("Unexpected status endpoint")
    url = endpoint + "?action=status"
    with requests.Session() as client:
        client.trust_env = False  # Public request, with no netrc credentials or proxy configuration.
        for attempt in range(2):
            with client.get(url, allow_redirects=False, timeout=(3, 6), stream=True) as response:
                if attempt == 0 and response.status_code in (301, 302, 303, 307, 308):
                    url = response.headers.get("Location", "")
                    target = urlsplit(url)
                    if (target.scheme, target.netloc, target.path) != (
                        "https", "script.googleusercontent.com", "/macros/echo"
                    ) or target.fragment:
                        raise ValueError("Unexpected collector content redirect")
                    continue
                if response.status_code != 200:
                    raise ValueError("Collector status unavailable")
                body = bytearray()
                for chunk in response.iter_content(16384):
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise ValueError("Unexpected collector status size")
                    body.extend(chunk)
                return json.loads(body)


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _format(status, now):
    if not isinstance(status, dict) or status.get("capture_profile") != "minute_snapshots_only":
        raise ValueError("Unknown collector profile")
    lines = ["## Collector runtime", f"Status read at {now:%Y-%m-%d %H:%M:%S} IST."]
    policy = _object(status.get("runtime_policy"))
    cap = policy.get("snapshot_minutes")
    budget = _object(status.get("budget"))
    start_ms, end_ms, used_ms = (budget.get(k) for k in ("windowStartMs", "windowEndMs", "ms"))
    if (all(_number(v) for v in (start_ms, end_ms, used_ms, cap)) and cap > 0
            and end_ms - start_ms == 86400000):
        start, end = (datetime.fromtimestamp(ms / 1000, IST) for ms in (start_ms, end_ms))
        period = f"{start:%Y-%m-%d %H:%M:%S} to {end:%Y-%m-%d %H:%M:%S} IST"
        if start <= now < end:
            minutes = used_ms / 60000
            lines.append(f"Recorded trigger work in the 24-hour window ({period}) was **{minutes:.2f} of "
                         f"{cap:g} minutes**. Remaining snapshot allowance was **{max(0, cap - minutes):.2f} minutes**.")
        else:
            why = "expired" if now >= end else "future-dated"
            lines.append(f"The stored window ({period}) is {why}. Current consumption and remaining allowance are unavailable.")
    else:
        lines.append("The status contains no usable current 24-hour accounting window.")
    last = _object(status.get("last_snapshot"))
    try:
        stamp = datetime.fromisoformat(last["retrieved_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > now + timedelta(seconds=5):
            raise ValueError("Invalid capture time")
    except (AttributeError, KeyError, TypeError, ValueError):
        lines.append("Last snapshot timings are unavailable.")
    else:
        labels = (("fetch_seconds", "fetch"), ("archive_seconds", "archive"), ("seconds", "total"))
        timings = [f"{label} {last[key]:.2f}s" if _number(last.get(key)) else f"{label} unavailable" for key, label in labels]
        lines.append(f"Snapshot timings at {stamp.astimezone(IST):%Y-%m-%d %H:%M:%S} IST were " + ", ".join(timings) + ".")
    lines.append("The counter measures work recorded by this collector. Google account-wide quota use is unavailable here.")
    return "\n\n".join(lines)


def collector_runtime_summary():
    """A failed status read still allows publication of the paper book and archive audit."""
    try:
        return _format(_read(), datetime.now(IST))
    except (requests.RequestException, ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        return "## Collector runtime\n\nThe public runtime status is unavailable for this batch."
