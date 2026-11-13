"""Bounded retries and a process-local circuit breaker for idempotent external reads."""

from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from .events import emit


class SourceUnavailable(OSError):
    pass


class ExternalReads:
    def __init__(self, clock=monotonic, wait=sleep):
        self.clock = clock
        self.wait = wait
        self.lock = Lock()
        self.state = {}

    def read(self, source, operation, *, attempts=3):
        with self.lock:
            failures, until, probe = self.state.get(source, (0, 0, False))
            if until > self.clock() or probe:
                raise SourceUnavailable("External source temporarily paused")
            if until:
                self.state[source] = (failures, until, True)
        try:
            for attempt in range(1, attempts + 1):
                try:
                    result = operation()
                except OSError as error:
                    if isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                        raise
                    delay = 2 ** (attempt - 1)
                    if isinstance(error, HTTPError):
                        raw = (error.headers or {}).get("Retry-After")
                        if raw:
                            try:
                                delay = max(delay, float(raw))
                            except ValueError:
                                try:
                                    delay = max(
                                        delay, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                                    )
                                except (TypeError, ValueError, OverflowError):
                                    pass
                    emit("read.failed", component=source, attempt=attempt, error_type=type(error).__name__)
                    if attempt == attempts or delay > 30:
                        with self.lock:
                            count = self.state.get(source, (0, 0, False))[0] + 1
                            until = self.clock() + max(60, delay) if count >= 3 or delay > 30 else 0
                            self.state[source] = (count, until, False)
                        raise
                    self.wait(delay)
                else:
                    with self.lock:
                        self.state.pop(source, None)
                    return result
        finally:
            with self.lock:
                if self.state.get(source, (0, 0, False))[2]:
                    self.state.pop(source, None)


external_reads = ExternalReads()


def read_url(source, url, *, timeout=20, max_bytes=4_000_000):
    """Fetch a bounded public response through the shared read policy."""
    from urllib.request import Request, urlopen
    from .settings import load_settings

    if load_settings().offline:
        raise SourceUnavailable("External reads disabled in offline mode")

    def request():
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 VolatilityLab"}), timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("External response exceeds size limit")
        return raw

    return external_reads.read(source, request)
