#!/usr/bin/env python3
"""Standalone headless Dhan historical options downloader with Google Drive API sync.

Designed for scheduled GitHub Actions runs with checkpointed resume.
Uses the exact same checkpoint, ZIP archive, and coverage CSV format as
Dhan-Options-Fast-Resume-v2.ipynb.

Required environment variables:
  DHAN_ACCESS_TOKEN         Active DhanHQ v2 JWT access token
  DHAN_DRIVE_CLIENT_ID      Google OAuth Desktop/Web client ID
  DHAN_DRIVE_CLIENT_SECRET  Google OAuth client secret
  DHAN_DRIVE_REFRESH_TOKEN  Google OAuth offline refresh token (scope: https://www.googleapis.com/auth/drive)

Optional environment variables:
  DHAN_STAGING_FOLDER_ID    Google Drive folder ID for "Dhan minute history staging"
                            (default: 1sauTXWOxaVR-Rxssv5cwGzYcnwJOsJiy)
  MAX_RUN_MINUTES           Soft time limit before flushing checkpoints & exiting 0 (default: 320)
  LOCAL_STAGING_DIR         Local working directory (default: ./dhan_staging)
"""

RUN_DEADLINE = None
DRIVE_MIRROR = None
WRITER_OWNER = None


class RunTimeBudgetReached(BaseException):
    """Raised cleanly when MAX_RUN_MINUTES is reached so checkpoints flush to Drive."""


from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, local
from email.utils import parsedate_to_datetime
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import sleep, monotonic, time as wall_time
from zoneinfo import ZoneInfo
import csv
import errno
import hashlib
import io
import json
import math
import os
import uuid
import zipfile

import requests

IST = ZoneInfo("Asia/Kolkata")
START, END = date(2021, 11, 27), date(2026, 11, 27)  # end is exclusive
OPTION_CYCLES = {"NIFTY": ("MONTH", "WEEK"), "BANKNIFTY": ("MONTH",)}
SYMBOLS = {"NIFTY": 13, "BANKNIFTY": 25}
OPTION_FIELDS = ["open", "high", "low", "close", "volume", "oi", "strike", "spot", "iv"]
SPOT_FIELDS = ["open", "high", "low", "close", "volume"]
BASE = "https://api.dhan.co/v2/"
VERSION = 1


class DhanHTTPError(RuntimeError):
    def __init__(self, path, status, code, message=""):
        self.status = status
        self.code = code
        super().__init__(f"Dhan {path} returned HTTP {status}, broker code {code}. {message}".strip())


class UnresolvedOptionRequests(RuntimeError):
    def __init__(self, requests_failed):
        self.requests_failed = requests_failed
        super().__init__(f"{len(requests_failed)} option request(s) unresolved")


class IncompleteOptionWindow(RuntimeError):
    def __init__(self, gaps):
        self.gaps = gaps
        super().__init__(f"{len(gaps)} option series have unresolved requests")


WORKERS = 4
REQUEST_INTERVAL = 0.25
DAILY_BUDGET = 100_000


class DownloadPacer:
    def __init__(self, ledger):
        self.ledger = ledger
        # The old downloader did not meter its requests. Preserve headroom for its
        # recent traffic on first migration, without resetting it on each restart.
        marker = ledger.with_name(ledger.stem + "-metering-start.json")
        if not marker.exists():
            save_json(marker, wall_time())
        self.metering_start = float(json.loads(marker.read_text()))
        self.lock = Lock()
        self.cooldown_file = ledger.with_name("options-cooldown.json")
        retry_at = json.loads(self.cooldown_file.read_text())["retry_at"] if self.cooldown_file.exists() else 0
        self.next_start = monotonic() + max(0, retry_at - wall_time())
        self.interval = REQUEST_INTERVAL
        self.hour = None
        self.remaining = 0
        self.fatal_error = None
        self.generation = 0
        self.clean_since = None
        self.clean_responses = 0

    def reserve(self, now):
        hour = int(now // 3600)
        if self.hour == hour and self.remaining:
            return 0
        counts = json.loads(self.ledger.read_text()) if self.ledger.exists() else {}
        counts = {int(k): int(v) for k, v in counts.items() if int(k) >= hour - 24}
        reserve_until = self.metering_start + 24 * 3600
        reserve = 10_000 if now < reserve_until else 0
        available = DAILY_BUDGET - reserve - sum(counts.values())
        if available <= 0:
            wake = (min(counts) + 25) * 3600
            if reserve:
                wake = min(wake, reserve_until)
            return max(1, wake - now)
        self.remaining = min(100, available)
        counts[hour] = counts.get(hour, 0) + self.remaining
        save_json(self.ledger, counts)
        self.hour = hour
        return 0

    def wait(self):
        while True:
            if RUN_DEADLINE is not None and monotonic() >= RUN_DEADLINE:
                raise RunTimeBudgetReached()
            with self.lock:
                if self.fatal_error is not None:
                    raise self.fatal_error
                delay = self.next_start - monotonic()
                if delay <= 0:
                    delay = self.reserve(wall_time())
                    if delay == 0:
                        self.remaining -= 1
                        self.next_start = monotonic() + self.interval
                        return self.generation
                    self.next_start = monotonic() + delay
            sleep(min(30, delay))

    def throttle(self):
        with self.lock:
            self.generation += 1
            self.clean_since = None
            self.clean_responses = 0
            self.interval = min(2.0, max(0.5, self.interval * 2))
            self.next_start = max(self.next_start, monotonic() + self.interval)

    def success(self, generation):
        with self.lock:
            if generation != self.generation or self.interval <= REQUEST_INTERVAL:
                return
            if self.clean_since is None:
                self.clean_since = monotonic()
            self.clean_responses += 1
            if self.clean_responses >= 32 and monotonic() - self.clean_since >= 60:
                self.interval = max(REQUEST_INTERVAL, self.interval * 0.8)
                self.clean_responses = 0
                self.clean_since = monotonic()

    def cooldown(self, delay):
        with self.lock:
            self.next_start = max(self.next_start, monotonic() + delay)

    def halt(self, error):
        with self.lock:
            self.fatal_error = error


def retry_delay(header, attempt):
    delay = 2 ** (attempt + 1)
    if header:
        try:
            delay = max(delay, float(header))
        except ValueError:
            try:
                delay = max(delay, parsedate_to_datetime(header).timestamp() - wall_time())
            except (ValueError, TypeError, OverflowError):
                pass
    return delay


span_lock = Lock()
rejected_spans = set()


class TemporaryConnectionError(RuntimeError):
    pass


def call_api(session, path, payload=None):
    delay = 60
    while True:
        try:
            return call_api_with_retries(session, path, payload)
        except DhanHTTPError as exc:
            if exc.status == 429 or exc.code == "DH-904":
                if exc.status != 429:
                    request_pacer.throttle()
                delay = max(900, delay)
                reason = "Dhan rate limit"
            elif exc.status in (500, 502, 503, 504):
                reason = "Dhan service unavailable"
            else:
                raise
        except TemporaryConnectionError:
            reason = "Dhan connection interrupted"
        wait_seconds = max(delay, request_pacer.next_start - monotonic())
        retry_at = wall_time() + wait_seconds
        save_json(request_pacer.cooldown_file, {"retry_at": retry_at, "reason": reason})
        print(reason, "— retry at", datetime.fromtimestamp(retry_at, IST).isoformat(timespec="seconds"), flush=True)
        request_pacer.cooldown(wait_seconds)
        delay = min(3600, delay * 2)


def call_api_with_retries(session, path, payload=None):
    # Dhan's SDK sends the account ID in both the header and JSON body.
    # Keep it on the wire only, outside the stored market-data selectors.
    wire_payload = payload
    if path == "charts/rollingoption" and payload is not None and session.headers.get("client-id"):
        wire_payload = dict(payload, dhanClientId=session.headers["client-id"])
    for attempt in range(5):
        generation = request_pacer.wait()
        try:
            resp = session.request("GET" if payload is None else "POST", BASE + path,
                                   json=wire_payload, timeout=(15, 90), allow_redirects=False)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 4:
                raise TemporaryConnectionError("Dhan connection interrupted.") from None
            sleep(2 ** attempt)
            continue
        if path == "profile" and resp.status_code in (401, 403):
            raise PermissionError("Dhan declined the current session token.")
        if resp.status_code == 429:
            request_pacer.throttle()
        if resp.status_code in (429, 500, 502, 503, 504):
            delay = retry_delay(resp.headers.get("Retry-After", ""), attempt)
            if resp.status_code == 429 or resp.headers.get("Retry-After"):
                request_pacer.cooldown(delay)
            if attempt < 4:
                if resp.status_code != 429 and not resp.headers.get("Retry-After"):
                    sleep(delay)
                continue
        if resp.status_code != 200:
            message = ""
            try:
                body = resp.json()
                code = str(body.get("errorCode") or body.get("errorType") or "unreported")[:60]
                message = str(body.get("errorMessage") or body.get("message") or "")
                token = session.headers.get("access-token")
                if token:
                    message = message.replace(token, "[redacted]")
                if session.headers.get("client-id"):
                    message = message.replace(session.headers["client-id"], "[redacted]")
                message = " ".join(message.split())[:250]
            except (ValueError, AttributeError):
                code = "unreported"
            error = DhanHTTPError(path, resp.status_code, code, message)
            if stops_all_requests(error) and error.code != "DH-904" and resp.status_code != 429:
                request_pacer.halt(error)
            raise error
        try:
            raw = resp.json()
        except ValueError:
            raise ValueError("Dhan returned a non-JSON response.") from None
        if isinstance(raw, dict) and raw.get("errorCode") == "DH-904":
            error = DhanHTTPError(path, resp.status_code, "DH-904", "Dhan rate limit reached.")
            raise error
        if not isinstance(raw, dict) or raw.get("errorCode") or raw.get("status") == "failure":
            raise ValueError(f"Dhan {path} did not return a success payload.")
        request_pacer.success(generation)
        return raw


def windows(start, end):
    while start < end:
        stop = min(end, start + timedelta(days=30))
        yield start, stop
        start = stop


def series(symbol, window_start, window_end, pilot):
    # One bundle per symbol/window; store the untouched Dhan JSON with every request.
    sid = SYMBOLS[symbol]
    yield "spot", "charts/intraday", dict(
        securityId=str(sid), exchangeSegment="IDX_I", instrument="INDEX", interval="1", oi=False,
        fromDate=f"{window_start} 00:00:00", toDate=f"{window_end - timedelta(days=1)} 23:59:59")
    for flag in OPTION_CYCLES[symbol]:
        for code in ([1] if pilot else [1, 2, 3]):
            offsets = [0] if pilot else range(-10 if code == 1 else -3, 11 if code == 1 else 4)
            for offset in offsets:
                strike = "ATM" if offset == 0 else f"ATM{offset:+d}"
                for side in ("CALL", "PUT"):
                    yield f"{flag}_{code}_{strike}_{side}", "charts/rollingoption", dict(
                        securityId=sid, exchangeSegment="NSE_FNO", instrument="OPTIDX", interval="1",
                        expiryFlag=flag, expiryCode=code, strike=strike, drvOptionType=side,
                        requiredData=OPTION_FIELDS, fromDate=str(window_start), toDate=str(window_end))


def minute_epoch(value, row):
    try:
        stamp = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"Row {row}: timestamp is not numeric ({type(value).__name__}).") from None
    if not stamp.is_finite() or not 0 < stamp < 10**14 or stamp != stamp.to_integral_value():
        raise ValueError(f"Row {row}: expected a positive whole epoch in seconds or milliseconds.")
    epoch = int(stamp)
    if epoch >= 10**12:
        epoch, remainder = divmod(epoch, 1000)
        if remainder:
            raise ValueError(f"Row {row}: timestamp {stamp} includes sub-second data.")
    if epoch % 60:
        raise ValueError(f"Row {row}: timestamp {stamp} is {epoch % 60} seconds past the minute.")
    return epoch


def normalize_candles(full, path, req):
    spot = path == "charts/intraday"
    if spot:
        data = full
    else:
        side = "ce" if req["drvOptionType"] == "CALL" else "pe"
        # A missing advertised side is distinct from a valid, empty set of arrays.
        data = full["data"][side]
    if not isinstance(data, dict) or not isinstance(data.get("timestamp"), list):
        raise ValueError("Missing expected candle arrays.")
    raw_ts = data["timestamp"]
    fields = SPOT_FIELDS if spot else OPTION_FIELDS
    for f in fields:
        if not isinstance(data.get(f), list) or len(data[f]) != len(raw_ts):
            raise ValueError(f"{f} / timestamp length mismatch.")
    candle_columns = [name for name, values in data.items()
                      if name != "timestamp" and isinstance(values, list) and len(values) == len(raw_ts)]
    row_for_minute = {}
    boundary = date.fromisoformat(req["toDate"][:10]) if not spot else None
    for i, value in enumerate(raw_ts):
        epoch = minute_epoch(value, i)
        # Some option responses include the next session on the end date.
        # Retain it in the raw response and exclude it from this half-open window.
        if boundary and datetime.fromtimestamp(epoch, IST).date() == boundary:
            continue
        if epoch in row_for_minute:
            previous = row_for_minute[epoch]
            if any(data[f][previous] != data[f][i] for f in candle_columns):
                raise ValueError(f"Rows {previous} and {i}: conflicting candles at epoch {epoch}.")
        else:
            row_for_minute[epoch] = i
    ordered = sorted(row_for_minute)
    source_rows = [row_for_minute[t] for t in ordered]
    normalized = {f: [data[f][i] for i in source_rows] for f in fields}
    normalized["timestamp"] = ordered
    changed = len(ordered) != len(raw_ts) or any(type(raw) is not int or raw != t for raw, t in zip(raw_ts, ordered))
    index = {"timestamp": ordered, "source_row": source_rows} if changed else None
    return normalized, index


def measure(full, path, req):
    data, _ = normalize_candles(full, path, req)
    return measure_candles(data, path, req)


def measure_candles(data, path, req):
    spot = path == "charts/intraday"
    ts = data["timestamp"]
    fields = SPOT_FIELDS if spot else OPTION_FIELDS
    start = date.fromisoformat(req["fromDate"][:10])
    end = date.fromisoformat(req["toDate"][:10]) + timedelta(days=int(spot))
    lower = int(datetime.combine(start, time.min, IST).timestamp())
    upper = int(datetime.combine(end, time.min, IST).timestamp())
    dates = Counter()
    for t in ts:
        if not lower <= t < upper:
            raise ValueError(f"Epoch {t} is outside the requested trading dates.")
        day = datetime.fromtimestamp(t, IST).date()
        dates[day.isoformat()] += 1
    def positive(x):
        return type(x) in (int, float) and math.isfinite(x) and x > 0
    for f in fields:
        if any(x is not None and (type(x) not in (int, float) or not math.isfinite(x)) for x in data[f]):
            raise ValueError(f"Invalid numeric {f}")
    positive_iv = None if spot else sum(positive(x) for x in data["iv"])
    strike_missing = None if spot else sum(not positive(x) for x in data["strike"])
    null_iv = None if spot else sum(x is None for x in data["iv"])
    return dict(rows=len(ts), dates=dict(sorted(dates.items())), first=ts[0] if ts else None,
                last=ts[-1] if ts else None, positive_iv=positive_iv, missing_strike=strike_missing,
                null_iv=null_iv, nonpositive_iv=None if spot else len(ts)-positive_iv-null_iv)


def digest(data):
    return hashlib.sha256(data).hexdigest()


DATA_ERRORS = (ValueError, KeyError, TypeError, IndexError, OverflowError, zipfile.BadZipFile)


def quarantine(path):
    # Preserve the received bytes outside the checkpoint namespace for inspection.
    path.rename(path.with_name(f"{path.name}.{uuid.uuid4().hex}.rejected"))


def retry_drive_io(operation):
    delays = (5, 15, 45)
    for attempt in range(len(delays) + 1):
        try:
            return operation()
        except OSError as exc:
            if exc.errno not in (errno.EIO, errno.ETIMEDOUT, errno.EAGAIN, errno.EBUSY,
                                 errno.ECONNRESET, errno.ENETUNREACH) or attempt == len(delays):
                raise
            print(f"Temporary storage error; retrying in {delays[attempt]} seconds.", flush=True)
            sleep(delays[attempt])


def write_atomic(path, data):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    def commit():
        tmp.write_bytes(data)
        os.replace(tmp, path)
    retry_drive_io(commit)


def get_option_window(session, req, spot_days, day_folder, repair=False):
    endpoint = "charts/rollingoption"
    wire = dict(req, securityId=int(req["securityId"]), expiryCode=int(req["expiryCode"]))
    days = sorted(d for d in spot_days if wire["fromDate"] <= d < wire["toDate"])
    side = "ce" if wire["drvOptionType"] == "CALL" else "pe"
    merged = {f: [] for f in ("timestamp", *OPTION_FIELDS)}
    originals = []
    present = set()
    single_checked = set()
    failures = []
    attempt = None

    def consume(entry):
        data, _ = normalize_candles(entry["response"], endpoint, entry["request"])
        stats = measure_candles(data, endpoint, entry["request"])
        present.update(stats["dates"])
        for field in merged:
            merged[field].extend(data[field])
        originals.append(entry)

    def same_request(a, b):
        return dict(a, securityId=str(a["securityId"]), expiryCode=str(a["expiryCode"])) == dict(
            b, securityId=str(b["securityId"]), expiryCode=str(b["expiryCode"]))

    buffered = []

    def flush_batch():
        if buffered:
            # Keep at most eight validated responses in memory between durable saves.
            batch = json.dumps(buffered, separators=(",", ":"), allow_nan=False).encode()
            write_atomic(day_folder / (digest(batch) + ".batch"), batch)
            buffered.clear()

    def get_span(first, last):
        end = str(date.fromisoformat(last) + timedelta(days=1))
        request = dict(wire, fromDate=first, toDate=end)
        entry = dict(request=request, response=call_api(session, endpoint, request),
                     retrieved_utc=datetime.now(timezone.utc).isoformat())
        try:
            consume(entry)
        except DATA_ERRORS:
            save_json(day_folder / (f"{first}_{last}.{uuid.uuid4().hex}.rejected"), entry)
            raise
        buffered.append(entry)
        if len(buffered) >= 8:
            flush_batch()
        if first == last:
            single_checked.add(first)

    # Support day checkpoints from either older notebook, including numeric strings.
    def restore(entry):
        request = entry["request"]
        first, end = request["fromDate"], request["toDate"]
        if not same_request(request, dict(wire, fromDate=first, toDate=end)):
            raise ValueError("Checkpoint selector differs")
        if not (wire["fromDate"] <= first < end <= wire["toDate"]):
            raise ValueError("Checkpoint dates outside requested window")
        single = date.fromisoformat(end) - date.fromisoformat(first) == timedelta(days=1)
        if repair and not single and (day_folder / "_daily_replay").exists():
            return
        consume(entry)
        if single and (not repair or first in present):
            single_checked.add(first)

    saved = sorted((*day_folder.glob("*.json"), *day_folder.glob("*.batch")))
    for path in saved:
        try:
            contents = retry_drive_io(path.read_bytes)
            if path.suffix == ".batch" and digest(contents) != path.stem:
                raise ValueError("Checkpoint batch checksum differs")
            payload = json.loads(contents)
            if path.name == "_full_rejected.json":
                if not same_request(payload["request"], wire):
                    raise ValueError("Rejected request differs")
                attempt = payload
            elif path.suffix == ".batch":
                if not isinstance(payload, list):
                    raise ValueError("Invalid response batch")
                for entry in payload:
                    try:
                        restore(entry)
                    except DATA_ERRORS:
                        save_json(path.with_suffix("." + uuid.uuid4().hex + ".rejected"), entry)
            else:
                restore(payload)
        except DATA_ERRORS:
            quarantine(path)

    family = (wire["securityId"], wire["expiryFlag"], wire["expiryCode"], wire["fromDate"], wire["toDate"])
    if not saved and not repair:
        with span_lock:
            full_rejected = (family, 30) in rejected_spans
        if not full_rejected:
            try:
                response = call_api(session, endpoint, wire)
            except DATA_ERRORS:
                pass  # Try smaller date spans below.
            except DhanHTTPError as exc:
                if (exc.status, exc.code) != (400, "DH-905"):
                    raise
                attempt = dict(request=wire, status=exc.status, code=exc.code)
                day_folder.mkdir(exist_ok=True)
                save_json(day_folder / "_full_rejected.json", attempt)
                with span_lock:
                    rejected_spans.add((family, 30))
            else:
                entry = dict(request=wire, response=response, retrieved_utc=datetime.now(timezone.utc).isoformat())
                day_folder.mkdir(exist_ok=True)
                try:
                    consume(entry)
                except DATA_ERRORS:
                    save_json(day_folder / (uuid.uuid4().hex + ".rejected"), entry)
                else:
                    if set(days) <= present:
                        return dict(response=response, wire_request=wire)
                    save_json(day_folder / "_full_partial.json", entry)

    if not days and attempt:
        raise UnresolvedOptionRequests([attempt])
    day_folder.mkdir(exist_ok=True)
    # Try the longest remaining contiguous span first. Keep boundaries exclusive.
    # If a size fails for this family of selectors, try smaller spans directly.
    try:
        for limit in ((1,) if repair else (29, 15, 7, 3, 1)):
            with span_lock:
                if (family, limit) in rejected_spans:
                    continue
            i = 0
            while i < len(days):
                if days[i] in present or days[i] in single_checked:
                    i += 1
                    continue
                first = days[i]
                end = str(date.fromisoformat(first) + timedelta(days=limit))
                i += 1
                while i < len(days) and days[i] < end and days[i] not in present and days[i] not in single_checked:
                    i += 1
                last = days[i - 1]
                if first == last and limit > 1:
                    continue
                if limit > 1 and (first, str(date.fromisoformat(last) + timedelta(days=1))) == (wire["fromDate"], wire["toDate"]):
                    continue
                # A successful, sparse range can be followed by shorter gap requests.
                if first != last and any(e["request"]["fromDate"] == first and e["request"]["toDate"] ==
                                         str(date.fromisoformat(last) + timedelta(days=1)) for e in originals):
                    continue
                try:
                    get_span(first, last)
                except DATA_ERRORS as exc:
                    if limit == 1:
                        failures.append(dict(date=first, error=str(exc)))
                    continue
                except DhanHTTPError as exc:
                    if (exc.status, exc.code) != (400, "DH-905"):
                        raise
                    if limit == 1:
                        end_date = str(date.fromisoformat(first) + timedelta(days=1))
                        failures.append(dict(request=dict(wire, fromDate=first, toDate=end_date),
                                             status=exc.status, code=exc.code, error=str(exc)))
                        continue
                    with span_lock:
                        rejected_spans.add((family, limit))
                    break
    finally:
        flush_batch()
    failures.extend(dict(date=d, error="No option rows on a spot trading date")
                    for d in sorted(set(days) - present - {f.get("date") for f in failures}))
    if failures:
        raise UnresolvedOptionRequests(failures)
    result = dict(response={"data": {side: merged}}, source_responses=originals)
    if attempt is not None:
        result["full_window_rejection"] = attempt
    return result


def collect_bundle(folder, symbol, start, stop, pilot, session, repair=False):
    scope = list(series(symbol, start, stop, pilot))
    label = f"{symbol}_{start}_{stop}"
    dest = folder / (label + ".zip")
    plan_hash = digest(json.dumps([VERSION, scope], sort_keys=True).encode())
    if dest.exists():
        try:
            with zipfile.ZipFile(dest) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                if manifest["plan_hash"] != plan_hash:
                    raise ValueError(f"Invalid prior bundle: {dest.name}")
                if set(archive.namelist()) != {"manifest.json", *(k + ".json" for k, _, _ in scope)}:
                    raise ValueError(f"Incomplete prior bundle: {dest.name}")
                if any(digest(archive.read(key + ".json")) != manifest["sha256"][key] for key, _, _ in scope):
                    raise ValueError("Stored archive member checksum differs")
                return manifest["coverage"]
        except DATA_ERRORS:
            quarantine(dest)

    partial = folder / (label + ".in-progress")
    partial.mkdir(exist_ok=True)
    stats, hashes = {}, {}
    gaps = {}
    spot_minutes, spot_days = set(), set()
    local_session = local()
    worker_sessions = []
    sessions_lock = Lock()

    def worker_session():
        if not hasattr(local_session, "http"):
            http = requests.Session()
            http.headers.update(session.headers)
            local_session.http = http
            with sessions_lock:
                worker_sessions.append(http)
        return local_session.http

    def stage(selector):
        key, endpoint, request = selector
        path = partial / (key + ".json")
        existing = path.is_file()
        blob = None
        item = None
        if existing:
            try:
                blob = retry_drive_io(path.read_bytes)
                item = json.loads(blob)
                if item["request"] != request or item["endpoint"] != endpoint:
                    raise ValueError("Checkpoint request differs")
                measure(item["response"], endpoint, request)
            except DATA_ERRORS:
                quarantine(path)
                item = None
                existing = False
        if item is None and key == "spot" and index_chunk(folder.parent, symbol, start, stop).is_file():
            try:
                with zipfile.ZipFile(index_chunk(folder.parent, symbol, start, stop)) as z:
                    candidate = json.loads(z.read("spot.json"))
                if candidate["request"] == request and candidate["endpoint"] == endpoint:
                    measure(candidate["response"], endpoint, request)
                    item = candidate
            except DATA_ERRORS:
                pass  # An unusable index cache can be fetched independently.
        if item is None:
            try:
                if key == "spot":
                    fetched = dict(response=call_api(session, endpoint, request))
                else:
                    fetched = get_option_window(worker_session(), request, spot_days, partial / (key + ".days"), repair=repair)
            except UnresolvedOptionRequests as exc:
                save_json(partial / (key + ".error.json"), dict(endpoint=endpoint, request=request,
                          failed_requests=exc.requests_failed, utc=datetime.now(timezone.utc).isoformat()))
                raise
            except DhanHTTPError as exc:
                if not selector_error(exc):
                    raise
                failures = [dict(request=request, status=exc.status, code=exc.code)]
                save_json(partial / (key + ".error.json"), dict(endpoint=endpoint, failed_requests=failures))
                raise UnresolvedOptionRequests(failures) from exc
            except (ValueError, RuntimeError, PermissionError) as exc:
                save_json(partial / (key + ".error.json"), dict(endpoint=endpoint, request=request,
                          error=str(exc), utc=datetime.now(timezone.utc).isoformat()))
                raise
            item = dict(schema=VERSION, provider="Dhan", endpoint=endpoint, request=request,
                        retrieved_utc=datetime.now(timezone.utc).isoformat(), **fetched)
        if item["request"] != request or item["endpoint"] != endpoint:
            raise ValueError(f"Checkpoint request differs: {path.name}")
        try:
            data, time_index = normalize_candles(item["response"], endpoint, request)
            summary = measure_candles(data, endpoint, request)
        except (KeyError, TypeError, ValueError) as exc:
            save_json(partial / (key + ".rejected.json"), item)
            if key != "spot":
                daily = partial / (key + ".days")
                daily.mkdir(exist_ok=True)
                write_atomic(daily / "_daily_replay", b"1")
            raise ValueError(f"{symbol} {key}: {exc}") from None
        if key == "spot":
            if not summary["rows"]:
                raise ValueError("No spot trading dates for this window")
            spot_minutes.update(data["timestamp"])
            spot_days.update(summary["dates"])
        else:
            actual = set(data["timestamp"])
            summary["missing_spot_minutes"] = len(spot_minutes - actual)
            summary["off_spot_minutes"] = len(actual - spot_minutes)
        if not existing:
            if time_index is not None:
                item["time_index"] = time_index
            blob = json.dumps(item, separators=(",", ":"), allow_nan=False).encode()
            write_atomic(path, blob)
        return key, summary, digest(blob)

    tmp_zip = dest.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        def pack(result):
            key, summary, checksum = result
            stats[key] = summary
            hashes[key] = checksum
            archive.write(partial / (key + ".json"), key + ".json")
        try:
            pack(stage(scope[0]))
        except UnresolvedOptionRequests as exc:
            raise IncompleteOptionWindow({"spot": exc.requests_failed}) from exc
        except DATA_ERRORS as exc:
            raise IncompleteOptionWindow({"spot": str(exc)}) from exc
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        try:
            # Each worker owns a distinct selector and its checkpoints. Only the
            # coordinator appends to the ZIP, even when requests finish out of order.
            futures = {pool.submit(stage, selector): selector[0] for selector in scope[1:]}
            for finished in as_completed(futures):
                try:
                    result = finished.result()
                except UnresolvedOptionRequests as exc:
                    gaps[futures[finished]] = exc.requests_failed
                    continue
                except DATA_ERRORS as exc:
                    gaps[futures[finished]] = str(exc)
                    continue
                pack(result)
                if DRIVE_MIRROR is not None and not pilot:
                    DRIVE_MIRROR.checkpoint_series(partial, result[0])
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            for http in worker_sessions:
                http.close()
        if gaps:
            raise IncompleteOptionWindow(gaps)
        stats = {key: stats[key] for key, _, _ in scope}
        archive.writestr("manifest.json", json.dumps(dict(plan_hash=plan_hash, coverage=stats, sha256=hashes)))
    with zipfile.ZipFile(tmp_zip) as check:
        for key, expected in hashes.items():
            if digest(check.read(key + ".json")) != expected:
                raise IOError("Bundle verification failed; the source checkpoints are still available.")
    os.replace(tmp_zip, dest)
    for key in stats:
        (partial / (key + ".json")).unlink(missing_ok=True)
        day_folder = partial / (key + ".days")
        if day_folder.is_dir():
            for completed in (*day_folder.glob("*.json"), *day_folder.glob("*.batch")):
                completed.unlink()
            (day_folder / "_daily_replay").unlink(missing_ok=True)
            if not any(day_folder.iterdir()):
                day_folder.rmdir()
        (partial / (key + ".error.json")).unlink(missing_ok=True)
    if not any(partial.iterdir()):
        partial.rmdir()
    return stats


COVERAGE_COLUMNS = ["symbol","window_start","window_end_exclusive","series","rows","positive_iv",
                     "null_iv","nonpositive_iv","missing_strike","first_epoch","last_epoch",
                     "spot_days_without_series","series_days_without_spot","missing_spot_minutes","off_spot_minutes"]


def read_coverage(coverage_path):
    coverage = {}
    if coverage_path.is_file():
        with coverage_path.open(newline="") as src:
            for row in csv.DictReader(src):
                coverage[(row["symbol"], row["window_start"], row["series"])] = [row[c] for c in COVERAGE_COLUMNS]
    return coverage


def update_coverage(coverage, symbol, left, right, stats):
    spot_dates = set(stats["spot"]["dates"])
    for key, values in stats.items():
        days = set(values["dates"])
        missing = sorted(spot_dates - days)
        extra = sorted(days - spot_dates)
        coverage[(symbol, str(left), key)] = [symbol,left,right,key,values["rows"],values["positive_iv"],
                         values["null_iv"],values["nonpositive_iv"],values["missing_strike"],
                         values["first"],values["last"]," ".join(missing)," ".join(extra),
                         values.get("missing_spot_minutes"),values.get("off_spot_minutes")]


def write_coverage(coverage_path, coverage):
    report = io.StringIO()
    writer = csv.writer(report)
    writer.writerow(COVERAGE_COLUMNS)
    writer.writerows(coverage.values())
    write_atomic(coverage_path, report.getvalue().encode())


def coverage_totals(coverage):
    saved = [dict(zip(COVERAGE_COLUMNS, values)) for values in coverage.values()]
    series_count = len(saved)
    total = sum(int(row["rows"]) for row in saved)
    positive = sum(int(row["positive_iv"] or 0) for row in saved)
    issues = sum(bool(not int(row["rows"]) or row["spot_days_without_series"] or row["series_days_without_spot"]
                      or int(row["missing_spot_minutes"] or 0) or int(row["missing_strike"] or 0)
                      or (row["series"] != "spot" and int(row["positive_iv"] or 0) != int(row["rows"])))
                 for row in saved)
    missing_spot_minutes = sum(int(row["missing_spot_minutes"] or 0) for row in saved)
    off_spot_minutes = sum(int(row["off_spot_minutes"] or 0) for row in saved)
    return dict(series_windows=series_count, series_rows=total, positive_iv_observations=positive,
                series_windows_flagged=issues, missing_spot_minutes=missing_spot_minutes, off_spot_minutes=off_spot_minutes)


def download_windows(root, session, pilot, symbol_only=None, repair=False):
    first, end = (date(2026, 11, 16), date(2026, 11, 24)) if pilot else (START, END)
    folder = root / ("pilot-v1" if pilot else f"history-v1_{first}_{end}")
    folder.mkdir(exist_ok=True)
    prefix = symbol_only + "_" if symbol_only else ""
    coverage_path = folder / (prefix + "coverage.csv")
    coverage = read_coverage(coverage_path)
    pending_path = root / "options-pending.json"

    def read_pending():
        return load_state(pending_path)

    # Both symbol generators share this durable queue. Reload before each change.
    pilot_gaps = []
    queued_names = set(read_pending())
    for left, right in windows(first, end):
        for symbol in ([symbol_only] if symbol_only else SYMBOLS):
            zip_name = f"{symbol}_{left}_{right}.zip"
            if not pilot and ((zip_name in queued_names) != repair):
                continue
            if DRIVE_MIRROR is not None and not pilot and DRIVE_MIRROR.has_remote_zip(zip_name):
                expected = [key for key, _, _ in series(symbol, left, right, False)]
                if not repair and all((symbol, str(left), key) in coverage for key in expected):
                    continue
                # Repair legacy ZIPs whose coverage CSV was never published.
                DRIVE_MIRROR.restore_archive(zip_name, folder / zip_name)
            if repair and read_pending().get(zip_name, {}).get("archive_error") and (folder / zip_name).exists():
                quarantine(folder / zip_name)
            was_final = (folder / zip_name).is_file()
            if not was_final:
                if DRIVE_MIRROR is not None and not pilot:
                    DRIVE_MIRROR.pull_in_progress(folder, f"{symbol}_{left}_{right}.in-progress")
                save_json(root / "options-run-status.json",
                          dict(state="downloading", symbol=symbol, start=str(left), end_exclusive=str(right),
                               utc=datetime.now(timezone.utc).isoformat()))
            try:
                def collect():
                    return collect_bundle(folder, symbol, left, right, pilot, session, repair=repair)
                stats = retry_drive_io(collect)
            except IncompleteOptionWindow as exc:
                pending = dict(symbol=symbol, start=str(left), end_exclusive=str(right),
                               unresolved_series=sorted(exc.gaps))
                if not pilot:
                    queue = read_pending()
                    queue[zip_name] = pending
                    save_json(pending_path, queue)
                else:
                    pilot_gaps.append(pending)
                yield dict(state="incomplete", **pending)
                continue
            new_coverage = any((symbol, str(left), key) not in coverage for key in stats)
            update_coverage(coverage, symbol, left, right, stats)
            for key, values in stats.items():
                if pilot and (not values["rows"] or (key != "spot" and not values["positive_iv"])):
                    raise ValueError(f"Pilot insufficient: {symbol} {key} has no usable rows or IV. Inspect pilot bundle.")
            if not was_final or new_coverage or right == end:
                write_coverage(coverage_path, coverage)
            if DRIVE_MIRROR is not None and not pilot:
                DRIVE_MIRROR.publish_window(folder, zip_name, coverage_path, f"{symbol}_{left}_{right}.in-progress")
            if repair:
                queue = read_pending()
                queue.pop(zip_name, None)
                save_json(pending_path, queue)
            print(f"{symbol} {left} to {right}: {len(stats)} series staged.", flush=True)
            if not was_final:
                yield dict(state="archived", symbol=symbol, start=str(left), end_exclusive=str(right))
    pending_windows = pilot_gaps if pilot else [
        row for row in read_pending().values() if symbol_only is None or row["symbol"] == symbol_only
    ]
    # Include coverage from windows published by previous runners.
    summary = dict(provider="Dhan", interval_minutes=1, start=str(first), end_exclusive=str(end),
                   **coverage_totals(coverage), incomplete_windows=pending_windows,
                   status="incomplete" if pending_windows else "staged_pending_review",
                   note="Rolling strike overlaps can duplicate market observations. No fixed expiry is inferred.")
    write_atomic(folder / (prefix + "summary.json"), json.dumps(summary, indent=2).encode())
    if DRIVE_MIRROR is not None and not pilot:
        DRIVE_MIRROR.upload_summary(folder / (prefix + "summary.json"))
    return summary


def run_download(root, session, pilot, symbol_only=None):
    job = download_windows(root, session, pilot, symbol_only)
    while True:
        try:
            next(job)
        except StopIteration as done:
            return done.value


RECOVERABLE_STATE = ("options-progress.json", "options-pending.json", "options-integrity.json")


def decode_state(path, raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(row, dict) for row in value.values()):
        raise ValueError("Invalid saved report")
    if path.name == "options-progress.json":
        for symbol, row in value.items():
            if symbol not in SYMBOLS or not all(isinstance(phase, dict) for phase in row.values()):
                raise ValueError("Invalid progress entry")
    elif path.name == "options-pending.json":
        for name, row in value.items():
            if row.get("symbol") not in SYMBOLS or name != f"{row['symbol']}_{row['start']}_{row['end_exclusive']}.zip":
                raise ValueError("Invalid pending window")
    elif not all(isinstance(row.get("remaining"), dict) and isinstance(row.get("retry"), bool)
                 and all(isinstance(gap, dict) and all(type(gap.get(key)) is int and gap[key] >= 0
                         for key in ("invalid_iv", "missing_minutes")) for gap in row["remaining"].values())
                 for row in value.values()):
        raise ValueError("Invalid integrity entry")
    return value


def load_state(path):
    for candidate in (path, path.with_suffix(".json.bak")):
        if candidate.exists():
            try:
                return decode_state(path, candidate.read_bytes())
            except DATA_ERRORS:
                quarantine(candidate)
    return {}


def save_json(path, obj):
    if path.name in RECOVERABLE_STATE and path.is_file():
        old = path.read_bytes()
        try:
            decode_state(path, old)
        except DATA_ERRORS:
            quarantine(path)
        else:
            backup = path.with_suffix(".json.bak")
            write_atomic(backup, old)
            if DRIVE_MIRROR is not None:
                DRIVE_MIRROR.maybe_sync_metadata(backup)
    if path.name == "options-run-status.json" and WRITER_OWNER:
        obj = dict(obj, writer=WRITER_OWNER)
    write_atomic(path, json.dumps(obj, indent=2).encode())
    if DRIVE_MIRROR is not None:
        DRIVE_MIRROR.maybe_sync_metadata(path)


def index_chunk(root, symbol, left, right):
    return root / "index-candles-1m" / symbol / f"{left}_{right}.zip"


CSV_COLUMNS = ["timestamp_ist", "epoch_seconds", *SPOT_FIELDS, "quality_flags", "outside_0915_1530_ist"]
INDEX_SCHEMA = 2


def spot_csv(item):
    data, _ = normalize_candles(item["response"], item["endpoint"], item["request"])
    counts = measure_candles(data, item["endpoint"], item["request"])
    if not counts["rows"]:
        raise ValueError("The index window returned zero candles.")
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    flagged = outside = 0
    for t, o, h, lo, c, v in zip(data["timestamp"], *(data[f] for f in SPOT_FIELDS)):
        flags = []
        if any(price is None or price <= 0 for price in (o, h, lo, c)):
            flags.append("missing_or_nonpositive_price")
        elif not lo <= min(o, c) <= max(o, c) <= h:
            flags.append("ohlc_range")
        if v is None:
            flags.append("missing_volume")
        elif v < 0:
            flags.append("negative_volume")
        local = datetime.fromtimestamp(t, IST)
        outside_clock = not time(9, 15) <= local.time() < time(15, 30)
        outside += int(outside_clock)
        flagged += bool(flags)
        writer.writerow([local.isoformat(), t, o, h, lo, c, v, "|".join(flags), int(outside_clock)])
    counts.update(quality_flagged=flagged, outside_0915_1530_ist=outside)
    return out.getvalue().encode(), counts


def collect_index_window(root, session, symbol, left, right):
    dest = index_chunk(root, symbol, left, right)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _, endpoint, req = next(series(symbol, left, right, False))
    if dest.exists():
        with zipfile.ZipFile(dest) as z:
            manifest = json.loads(z.read("manifest.json"))
            raw = z.read("spot.json")
            if (manifest["request"] != req or digest(z.read("candles.csv")) != manifest["csv_sha256"]
                    or digest(raw) != manifest["raw_sha256"]):
                raise ValueError(f"Checksum or request mismatch for {dest.name}.")
            if manifest.get("index_schema") == INDEX_SCHEMA:
                return manifest["coverage"]
    elif dest.with_suffix(".rejected.json").exists():
        raw = dest.with_suffix(".rejected.json").read_bytes()
    else:
        item = dict(schema=VERSION, provider="Dhan", endpoint=endpoint, request=req,
                    response=call_api(session, endpoint, req), retrieved_utc=datetime.now(timezone.utc).isoformat())
        raw = json.dumps(item, separators=(",", ":")).encode()
    item = json.loads(raw)
    if item.get("provider") != "Dhan" or item.get("endpoint") != endpoint or item.get("request") != req:
        raise ValueError(f"Cached market data differs from the requested window for {symbol} {left}.")
    try:
        csv_bytes, counts = spot_csv(item)
    except (KeyError, TypeError, ValueError):
        write_atomic(dest.with_suffix(".rejected.json"), raw)
        raise
    meta = dict(index_schema=INDEX_SCHEMA, request=req, coverage=counts,
                csv_sha256=digest(csv_bytes), raw_sha256=digest(raw))
    temp = dest.with_suffix(".zip.part")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("candles.csv", csv_bytes)
        z.writestr("spot.json", raw)
        z.writestr("manifest.json", json.dumps(meta))
    with zipfile.ZipFile(temp) as z:
        if z.testzip():
            raise IOError(f"Index window ZIP failed verification for {symbol} {left}.")
    os.replace(temp, dest)
    return counts


def assemble_index(root, symbol, periods, total_rows):
    folder = root / "index-candles-1m"
    name = f"{symbol}_1minute_{periods[0][0]}_to_{periods[-1][1] - timedelta(days=1)}.csv"
    dest = folder / (name + ".zip")
    temp = dest.with_suffix(".zip.part")
    written = 0
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target, target.open(name, "w") as output:
        for i, (left, right) in enumerate(periods):
            with zipfile.ZipFile(index_chunk(root, symbol, left, right)) as chunk:
                with chunk.open("candles.csv") as src:
                    header = src.readline()
                    if header.decode().strip().split(",") != CSV_COLUMNS:
                        raise ValueError("Unexpected CSV columns in a saved index window.")
                    if i == 0:
                        output.write(header)
                    rows = src.read()
                    written += rows.count(b"\n")
                    output.write(rows)
    with zipfile.ZipFile(temp) as finished:
        if written != total_rows or not written or finished.testzip():
            raise IOError(f"Final row count or ZIP verification failed for {symbol}.")
    os.replace(temp, dest)
    return dest.name


def selector_error(exc):
    return exc.status in (400, 404, 422) and not stops_all_requests(exc)


def stops_all_requests(exc):
    return isinstance(exc, DhanHTTPError) and (exc.status in (401, 403, 429) or exc.code in {"DH-901", "DH-902", "DH-903", "DH-904"})


def download_indices(root, session, start=START, end=END):
    folder = root / "index-candles-1m"
    folder.mkdir(exist_ok=True)
    status_file = folder / "progress.json"
    periods = list(windows(start, end))
    report = {s: dict(windows_total=len(periods), windows_saved=0, rows=0, quality_flagged=0,
                      outside_0915_1530_ist=0, errors={}) for s in SYMBOLS}
    pending = [(s, l, r) for l, r in periods for s in SYMBOLS]
    for attempt in range(2):
        retry = []
        for symbol, left, right in pending:
            key = f"{left}_{right}"
            entry = report[symbol]
            try:
                counts = collect_index_window(root, session, symbol, left, right)
            except (RuntimeError, ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
                entry["errors"][key] = str(exc)
                save_json(status_file, report)
                if stops_all_requests(exc):
                    raise
                retry.append((symbol, left, right))
                print(f"{symbol} {left}: {exc}", flush=True)
                continue
            entry["errors"].pop(key, None)
            entry["windows_saved"] += 1
            entry["rows"] += counts["rows"]
            entry["quality_flagged"] += counts["quality_flagged"]
            entry["outside_0915_1530_ist"] += counts["outside_0915_1530_ist"]
            print(f"{symbol} {left} to {right} (exclusive): {counts['rows']} candles", flush=True)
            if entry["windows_saved"] == len(periods):
                entry["file"] = assemble_index(root, symbol, periods, entry["rows"])
            save_json(status_file, report)
        pending = retry
        if not pending:
            break
    return report


def download_options(root, session, *, pilot_only=False):
    status_path = root / "options-progress.json"
    report = load_state(status_path)
    for symbol in SYMBOLS:
        report.setdefault(symbol, {})
    for mode in (("pilot",) if pilot_only else ("pilot", "history", "repair")):
        pilot = mode == "pilot"
        phase = "pilot" if pilot else "history"
        if pilot and all(report.get(s, {}).get("pilot", {}).get("ready_for_history") for s in SYMBOLS):
            continue
        jobs = {symbol: download_windows(root, session, pilot, symbol, repair=mode == "repair")
                for symbol in SYMBOLS
                if (pilot or report[symbol].get("pilot", {}).get("ready_for_history")
                    or report[symbol].get("pilot", {}).get("incomplete_windows"))
                and (mode != "repair" or report[symbol].get("history", {}).get("incomplete_windows"))}
        while jobs:
            for symbol, job in list(jobs.items()):
                entry = report[symbol]
                try:
                    checkpoint = next(job)
                except StopIteration as done:
                    result = done.value
                    entry[phase] = dict(passed=not (result["incomplete_windows"] or
                                        result["series_windows_flagged"] or result["off_spot_minutes"]), **result)
                    if pilot:
                        entry[phase]["ready_for_history"] = not result["incomplete_windows"]
                    del jobs[symbol]
                except RunTimeBudgetReached:
                    save_json(status_path, report)
                    raise
                except (RuntimeError, ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
                    entry[phase] = dict(passed=False, error=str(exc))
                    if stops_all_requests(exc):
                        save_json(status_path, report)
                        raise
                    if isinstance(exc, OSError):
                        raise
                    del jobs[symbol]
                else:
                    entry[phase] = dict(passed=False, status="in_progress", last_window=checkpoint)
                save_json(status_path, report)
                print(symbol, phase, entry[phase], flush=True)
    if not pilot_only:
        integrity_pass(root, session, report)
    return report


def quality_days(data, spot):
    missing = spot.difference(data["timestamp"])
    invalid = {ts for ts, iv in zip(data["timestamp"], data["iv"]) if iv is None or iv <= 0}
    return sorted({datetime.fromtimestamp(ts, IST).date() for ts in missing | invalid})


def repair_member(item, spot, session, cache):
    endpoint, request = item["endpoint"], item["request"]
    data, _ = normalize_candles(item["response"], endpoint, request)
    measure_candles(data, endpoint, request)
    rows = {ts: [data[k][i] for k in OPTION_FIELDS] for i, ts in enumerate(data["timestamp"])}
    iv_col, strike_col = OPTION_FIELDS.index("iv"), OPTION_FIELDS.index("strike")
    changed = False
    receipts, errors = [], {}
    previous = {r["request"]["fromDate"]: r for r in item.get("quality_responses", [])}
    for day in quality_days(data, spot):
        one_day = dict(request, fromDate=str(day), toDate=str(day + timedelta(days=1)))
        fingerprint = digest(json.dumps(one_day, sort_keys=True).encode())
        cache.mkdir(exist_ok=True)
        path = cache / (fingerprint + ".json")
        entry = previous.get(str(day))
        if entry is not None:
            if entry["request"] != one_day:
                raise ValueError("Prior daily repair selector differs")
            measure(entry["response"], endpoint, one_day)
        if entry is None and path.exists():
            try:
                record = json.loads(path.read_bytes())
                if not isinstance(record["payload"], str):
                    raise ValueError("Invalid repair receipt")
                blob = record["payload"].encode()
                if digest(blob) != record["sha256"]:
                    raise ValueError("Repair receipt checksum differs")
                entry = json.loads(blob)
                if entry["request"] != one_day:
                    raise ValueError("Repair selector differs")
                measure(entry["response"], endpoint, one_day)
            except DATA_ERRORS:
                quarantine(path)
                entry = None
        if entry is None:
            try:
                response = call_api(session, endpoint, one_day)
                measure(response, endpoint, one_day)
            except DhanHTTPError as exc:
                if not selector_error(exc):
                    raise
                errors[str(day)] = dict(status=exc.status, code=exc.code)
                continue
            except DATA_ERRORS as exc:
                errors[str(day)] = str(exc)
                continue
            entry = dict(request=one_day, response=response, retrieved_utc=datetime.now(timezone.utc).isoformat())
            payload = json.dumps(entry, separators=(",", ":"), allow_nan=False)
            save_json(path, dict(payload=payload, sha256=digest(payload.encode())))
        candles, _ = normalize_candles(entry["response"], endpoint, one_day)
        receipts.append(entry)
        for i, ts in enumerate(candles["timestamp"]):
            row = rows.get(ts)
            if row is None and ts in spot:
                rows[ts] = [candles[k][i] for k in OPTION_FIELDS]
                changed = True
            elif row is not None and (row[iv_col] is None or row[iv_col] <= 0):
                incoming = candles["iv"][i]
                if (incoming is not None and incoming > 0 and row[strike_col] is not None
                        and row[strike_col] > 0 and row[strike_col] == candles["strike"][i]):
                    row[iv_col] = incoming
                    changed = True
    if changed:
        if "original_response" not in item:
            item["original_response"] = item["response"]
        ordered = sorted(rows)
        data = {field: [rows[ts][i] for ts in ordered] for i, field in enumerate(OPTION_FIELDS)}
        data["timestamp"] = ordered
        side = "ce" if request["drvOptionType"] == "CALL" else "pe"
        item["response"] = {"data": {side: data}}
        item.pop("time_index", None)
    if receipts:
        # Keep raw daily evidence, including successful but empty provider replies.
        previous.update({r["request"]["fromDate"]: r for r in receipts})
        item["quality_responses"] = list(previous.values())
    stats = measure_candles(data, endpoint, request)
    actual = set(data["timestamp"])
    stats.update(missing_spot_minutes=len(spot - actual), off_spot_minutes=len(actual - spot))
    return stats, errors


def audit_archive(path, scope, session, cache):
    expected = digest(json.dumps([VERSION, scope], sort_keys=True).encode())
    tmp = path.with_suffix(".integrity.part")
    cache.mkdir(exist_ok=True)
    stats, hashes, errors = {}, {}, {}
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as out:
            manifest = json.loads(src.read("manifest.json"))
            if (manifest["plan_hash"] != expected or len(src.namelist()) != len(scope) + 1
                    or set(src.namelist()) != {"manifest.json", *(k + ".json" for k, _, _ in scope)}):
                raise ValueError("Unrecognized archive plan or membership")
            for key, endpoint, request in scope:
                if RUN_DEADLINE is not None and monotonic() >= RUN_DEADLINE:
                    raise RunTimeBudgetReached()
                name = key + ".json"
                raw = src.read(name)
                if digest(raw) != manifest["sha256"][key]:
                    raise ValueError("Stored archive member checksum differs")
                item = json.loads(raw)
                if (item["endpoint"], item["request"]) != (endpoint, request):
                    raise ValueError("Stored archive selector differs")
                if key == "spot":
                    data, _ = normalize_candles(item["response"], endpoint, request)
                    stats[key] = measure_candles(data, endpoint, request)
                    spot = set(data["timestamp"])
                    if not spot:
                        raise ValueError("Archive has no reference spot candles")
                else:
                    stats[key], failed = repair_member(item, spot, session, cache)
                    if failed:
                        errors[key] = failed
                    raw = json.dumps(item, separators=(",", ":"), allow_nan=False).encode()
                out.writestr(name, raw)
                hashes[key] = digest(raw)
            manifest.update(coverage=stats, sha256=hashes, quality_pass=1, repair_errors=errors)
            out.writestr("manifest.json", json.dumps(manifest))
        with zipfile.ZipFile(tmp) as check:
            for key, sha in hashes.items():
                if digest(check.read(key + ".json")) != sha:
                    raise OSError("Integrity archive verification failed")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return stats, errors


def integrity_pass(root, session, report):
    history = root / f"history-v1_{START}_{END}"
    state_path = root / "options-integrity.json"
    state = load_state(state_path)
    for symbol in SYMBOLS:
        if not report[symbol].get("history"):
            continue
        coverage_path = history / (symbol + "_coverage.csv")
        coverage = read_coverage(coverage_path)
        available = set()
        for left, right in windows(START, END):
            filename = f"{symbol}_{left}_{right}.zip"
            path = history / filename
            if DRIVE_MIRROR is not None:
                remote = DRIVE_MIRROR.archive_files.get(filename)
                if not remote:
                    continue
                signature = remote.get("md5Checksum")
            else:
                if not path.is_file():
                    continue
                signature = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
            available.add(filename)
            row = state.get(filename, {})
            if signature and row.get("signature") == signature and not row.get("retry"):
                continue
            cache = history / (path.stem + ".integrity.in-progress")
            if DRIVE_MIRROR is not None:
                DRIVE_MIRROR.restore_archive(filename, path)
                DRIVE_MIRROR.pull_in_progress(history, cache.name)
            save_json(root / "options-run-status.json", dict(state="integrity", symbol=symbol, start=str(left),
                      end_exclusive=str(right), utc=datetime.now(timezone.utc).isoformat()))
            try:
                stats, errors = audit_archive(path, list(series(symbol, left, right, False)), session, cache)
            except DATA_ERRORS as exc:
                pending_path = root / "options-pending.json"
                pending = load_state(pending_path)
                pending[filename] = dict(symbol=symbol, start=str(left), end_exclusive=str(right),
                                         unresolved_series=["archive"], archive_error=str(exc))
                save_json(pending_path, pending)
                state[filename] = dict(retry=True, remaining={}, archive_error=str(exc))
                save_json(state_path, state)
                continue
            update_coverage(coverage, symbol, left, right, stats)
            write_coverage(coverage_path, coverage)
            if DRIVE_MIRROR is not None:
                DRIVE_MIRROR.publish_window(history, filename, coverage_path, cache.name, replace=True,
                                            retain_partial=bool(errors))
                signature = DRIVE_MIRROR.archive_files[filename]["md5Checksum"]
            else:
                signature = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
            remaining = {k: dict(missing_minutes=v["missing_spot_minutes"], invalid_iv=v["rows"]-v["positive_iv"])
                         for k, v in stats.items() if k != "spot" and
                         (v["missing_spot_minutes"] or v["rows"] != v["positive_iv"])}
            state[filename] = dict(signature=signature, remaining=remaining, retry=bool(errors), request_errors=errors)
            save_json(state_path, state)
            if not errors:
                for receipt in cache.glob("*.json"):
                    receipt.unlink()
                if cache.exists() and not any(cache.iterdir()):
                    cache.rmdir()
        expected = [f"{symbol}_{left}_{right}.zip" for left, right in windows(START, END)]
        audited = [state[name] for name in expected if name in available and name in state]
        report[symbol]["integrity"] = dict(windows=len(audited), expected=len(expected),
            retry_windows=sum(r["retry"] for r in audited),
            windows_with_remaining_gaps=sum(bool(r["remaining"]) for r in audited),
            missing_spot_minutes=sum(v["missing_minutes"] for r in audited for v in r["remaining"].values()),
            invalid_iv=sum(v["invalid_iv"] for r in audited for v in r["remaining"].values()))
        history_summary = report[symbol].get("history", {})
        if "series_windows" in history_summary:
            history_summary.update(coverage_totals(coverage))
            history_summary["passed"] = not (history_summary["incomplete_windows"] or
                    history_summary["series_windows_flagged"] or history_summary["off_spot_minutes"]
                    or report[symbol]["integrity"]["retry_windows"] or len(audited) != len(expected))
            summary_path = history / (symbol + "_summary.json")
            save_json(summary_path, history_summary)
            if DRIVE_MIRROR is not None:
                DRIVE_MIRROR.upload_summary(summary_path)
        save_json(root / "options-progress.json", report)


def lock_document(credentials, staging_id):
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    files = drive.files().list(q=f"'{staging_id}' in parents and trashed=false "
        "and name='Dhan downloader writer lock' and mimeType='application/vnd.google-apps.document'",
        fields="nextPageToken,files(id)", pageSize=2).execute(num_retries=3)
    if len(files["files"]) != 1 or files.get("nextPageToken"):
        raise RuntimeError("Expected one private Dhan downloader writer lock document in staging.")
    docs = build("docs", "v1", credentials=credentials, cache_discovery=False).documents()
    return docs, files["files"][0]["id"]


def read_owner(docs, document_id):
    record = docs.get(documentId=document_id).execute(num_retries=3)
    text = "".join(e.get("textRun", {}).get("content", "") for block in record["body"]["content"]
                   for e in block.get("paragraph", {}).get("elements", []))
    return record["revisionId"], text.removesuffix("\n")


@contextmanager
def exclusive_writer(docs, document_id, runner):
    global WRITER_OWNER
    revision, current = read_owner(docs, document_id)
    if current:
        yield False
        return
    owner = runner + ":" + uuid.uuid4().hex
    def change(rev, request):
        return docs.batchUpdate(documentId=document_id, body=dict(writeControl={"requiredRevisionId": rev},
                                requests=[request])).execute(num_retries=0)
    try:
        change(revision, {"insertText": {"location": {"index": 1}, "text": owner}})
    except Exception:
        # A lost response may still have committed. Only our exact UUID grants ownership.
        _, observed = read_owner(docs, document_id)
        if observed != owner:
            if observed:
                yield False
                return
            raise
    WRITER_OWNER = owner
    try:
        yield True
    finally:
        WRITER_OWNER = None
        revision, current = read_owner(docs, document_id)
        if current == owner:
            change(revision, {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 1 + len(owner)}}})


def legacy_busy(root):
    path = root / "options-run-status.json"
    if not path.exists():
        return False
    status = json.loads(path.read_bytes())
    if status.get("writer") or status.get("state") not in {"starting", "downloading", "integrity"}:
        return False
    # A quiet period alone cannot distinguish a dead legacy VM from a long backoff.
    handoff = root / "options-legacy-handoff.json"
    return not (status.get("utc") and handoff.is_file() and
                json.loads(handoff.read_bytes()).get("stopped_status_utc") == status["utc"])


def integrity_finished(report):
    for symbol in SYMBOLS:
        entry = report.get(symbol, {})
        audit = entry.get("integrity", {})
        if (not entry.get("pilot", {}).get("ready_for_history") or
                entry.get("history", {}).get("status") != "staged_pending_review" or
                audit.get("windows") != len(list(windows(START, END))) or audit.get("retry_windows") != 0):
            return False
    return True


class DriveMirror:
    """Syncs staging metadata, active .in-progress checkpoints, and completed ZIPs with Google Drive."""

    METADATA_FILES = (
        *RECOVERABLE_STATE,
        *(name + ".bak" for name in RECOVERABLE_STATE),
        "options-legacy-handoff.json",
        "options-run-status.json",
        "options-request-budget.json",
        "options-request-budget-metering-start.json",
        "options-cooldown.json",
    )

    def __init__(self, staging_folder_id: str, local_root: Path, creds):
        self.staging_id = staging_folder_id
        self.local_root = local_root
        self.history_name = f"history-v1_{START}_{END}"
        self.lock = Lock()

        from googleapiclient.discovery import build
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

        self.history_id = self._ensure_folder(self.staging_id, self.history_name)
        self.archive_files = {k: v for k, v in self._list_children(self.history_id).items()
                              if k.endswith(".zip") and int(v.get("size", 0)) > 0}
        self.remote_zips = set(self.archive_files)
        self.partial_cache = {}
        print(f"Drive connected. Found {len(self.remote_zips)} completed archive ZIP(s) in {self.history_name}.", flush=True)

    def _list_children(self, parent_id: str):
        items = {}
        page_token = None
        while True:
            resp = self.service.files().list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)",
                pageSize=1000,
                pageToken=page_token,
            ).execute(num_retries=3)
            for item in resp.get("files", []):
                name = item["name"]
                if name in (".", "..") or "/" in name or name in items:
                    raise ValueError("Unsafe or duplicate Drive checkpoint name")
                items[name] = item
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items

    def _list_names(self, parent_id: str, suffix: str = ""):
        return {name for name in self._list_children(parent_id) if name.endswith(suffix)}

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        children = self._list_children(parent_id)
        if name in children and children[name]["mimeType"] == "application/vnd.google-apps.folder":
            return children[name]["id"]
        created = self.service.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
            fields="id",
        ).execute(num_retries=3)
        return created["id"]

    def _download_file(self, file_id: str, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id)
        tmp = dest.with_suffix(dest.suffix + ".dl")
        with tmp.open("wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=3)
        os.replace(tmp, dest)

    def _upload_file(self, parent_id: str, local_path: Path, mime_type: str = "application/octet-stream"):
        from googleapiclient.http import MediaFileUpload

        children = self._list_children(parent_id)
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
        if local_path.name in children:
            result = self.service.files().update(fileId=children[local_path.name]["id"], media_body=media,
                                                 fields="id,md5Checksum,size").execute(num_retries=3)
        else:
            result = self.service.files().create(
                body={"name": local_path.name, "parents": [parent_id]},
                media_body=media,
                fields="id,md5Checksum,size",
            ).execute(num_retries=3)
        with local_path.open("rb") as source:
            expected = hashlib.file_digest(source, "md5").hexdigest()
        if result.get("md5Checksum") != expected:
            raise OSError(errno.EIO, "Google Drive upload checksum mismatch")

        return result

    def pull_startup_state(self):
        self.local_root.mkdir(parents=True, exist_ok=True)
        staging_children = self._list_children(self.staging_id)
        for name in self.METADATA_FILES:
            if name in staging_children:
                self._download_file(staging_children[name]["id"], self.local_root / name)
        history_local = self.local_root / self.history_name
        history_local.mkdir(parents=True, exist_ok=True)
        history_children = self._list_children(self.history_id)
        for name, item in history_children.items():
            if name.endswith("_coverage.csv") or name == "coverage.csv":
                self._download_file(item["id"], history_local / name)

    def restore_archive(self, name, destination):
        self._download_file(self.archive_files[name]["id"], destination)

    def upload_summary(self, path):
        self._upload_file(self.history_id, path, "application/json")

    def checkpoint_series(self, partial, key):
        with self.lock:
            if partial.name not in self.partial_cache:
                parent = self._ensure_folder(self.history_id, partial.name)
                self.partial_cache[partial.name] = (parent, set(self._list_children(parent)))
            parent, existing = self.partial_cache[partial.name]
            path = partial / (key + ".json")
            if path.name not in existing or any(partial.glob(path.name + ".*.rejected")):
                self._upload_file(parent, path, "application/json")
                existing.add(path.name)

    def has_remote_zip(self, zip_name: str) -> bool:
        return zip_name in self.remote_zips

    def maybe_sync_metadata(self, path: Path):
        if path.parent == self.local_root and path.name in self.METADATA_FILES and path.is_file():
            with self.lock:
                self._upload_file(self.staging_id, path, "application/json")

    def pull_in_progress(self, history_local: Path, folder_name: str):
        with self.lock:
            history_children = self._list_children(self.history_id)
            if folder_name not in history_children:
                return
            remote_folder_id = history_children[folder_name]["id"]
            local_folder = history_local / folder_name
            self._pull_tree(remote_folder_id, local_folder)
            print(f"Resumed in-progress checkpoints from Drive: {folder_name}", flush=True)

    def _pull_tree(self, remote_folder_id: str, local_dir: Path):
        local_dir.mkdir(parents=True, exist_ok=True)
        children = self._list_children(remote_folder_id)
        for name, item in children.items():
            if name.endswith(".days") and name[:-5] + ".json" in children:
                continue
            if item["mimeType"] == "application/vnd.google-apps.folder":
                self._pull_tree(item["id"], local_dir / name)
            else:
                target = local_dir / name
                if not target.is_file():
                    self._download_file(item["id"], target)

    def push_in_progress_all(self):
        history_local = self.local_root / self.history_name
        if not history_local.is_dir():
            return
        with self.lock:
            for item in history_local.glob("*.in-progress"):
                if item.is_dir():
                    remote_id = self._ensure_folder(self.history_id, item.name)
                    self._push_tree(item, remote_id)
                    print(f"Flushed in-progress checkpoints to Drive: {item.name}", flush=True)

    def _push_tree(self, local_dir: Path, remote_folder_id: str):
        from googleapiclient.http import MediaFileUpload

        existing = self._list_children(remote_folder_id)
        for child in sorted(local_dir.iterdir()):
            if child.is_dir():
                if child.name.endswith(".days") and child.with_suffix(".json").is_file():
                    continue
                sub_id = (
                    existing[child.name]["id"]
                    if child.name in existing and existing[child.name]["mimeType"] == "application/vnd.google-apps.folder"
                    else self._ensure_folder(remote_folder_id, child.name)
                )
                self._push_tree(child, sub_id)
            elif child.is_file() and child.name not in existing:
                mime = "application/json" if child.suffix == ".json" else "application/octet-stream"
                media = MediaFileUpload(str(child), mimetype=mime, resumable=True)
                self.service.files().create(
                    body={"name": child.name, "parents": [remote_folder_id]},
                    media_body=media,
                    fields="id",
                ).execute(num_retries=3)

    def publish_window(self, history_local: Path, zip_name: str, coverage_path: Path, in_progress_name: str,
                       replace=False, retain_partial=False):
        zip_path = history_local / zip_name
        with self.lock:
            # Publish the CSV first. A published ZIP marks a committed window.
            self._upload_file(self.history_id, coverage_path, "text/csv")
            damaged = any(history_local.glob(zip_name + ".*.rejected"))
            if damaged and zip_name in self.remote_zips:
                old_id = self.archive_files[zip_name]["id"]
                self.service.files().copy(fileId=old_id, body={"name": zip_name + "." + uuid.uuid4().hex + ".rejected",
                                                              "parents": [self.history_id]}).execute(num_retries=3)
            if zip_name not in self.remote_zips or damaged or replace:
                self.archive_files[zip_name] = self._upload_file(self.history_id, zip_path, "application/zip")
                self.remote_zips.add(zip_name)
            zip_path.unlink(missing_ok=True)
            history_children = self._list_children(self.history_id)
            if in_progress_name in history_children and not retain_partial:
                self.service.files().update(fileId=history_children[in_progress_name]["id"],
                                            body={"trashed": True}).execute(num_retries=3)
            self.partial_cache.pop(in_progress_name, None)
            print(f"Published {zip_name} to Google Drive.", flush=True)


def open_dhan_session_from_env() -> requests.Session:
    token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if not token or not token.isascii() or not token.isprintable() or " " in token:
        raise RuntimeError("Missing or invalid DHAN_ACCESS_TOKEN environment variable.")
    session = requests.Session()
    session.headers.update({"access-token": token, "Accept": "application/json"})
    return session


def drive_credentials():
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None,
        refresh_token=os.environ["DHAN_DRIVE_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["DHAN_DRIVE_CLIENT_ID"].strip(),
        client_secret=os.environ["DHAN_DRIVE_CLIENT_SECRET"].strip(),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(GoogleAuthRequest())
    return creds


def main():
    staging_id = os.environ.get("DHAN_STAGING_FOLDER_ID", "1sauTXWOxaVR-Rxssv5cwGzYcnwJOsJiy").strip()
    creds = drive_credentials()
    docs, doc_id = lock_document(creds, staging_id)
    with exclusive_writer(docs, doc_id, "github-" + os.environ.get("GITHUB_RUN_ID", "local")) as acquired:
        if not acquired:
            print("Another downloader owns the writer lock; deferring.")
            return
        run_headless(staging_id, creds)


def run_headless(staging_folder_id, creds):
    global request_pacer, RUN_DEADLINE, DRIVE_MIRROR

    max_run_minutes = float(os.environ.get("MAX_RUN_MINUTES", "320"))
    if not 1 <= max_run_minutes <= 320:
        raise ValueError("MAX_RUN_MINUTES must be between 1 and 320")
    RUN_DEADLINE = monotonic() + max_run_minutes * 60.0

    local_root = Path(os.environ.get("LOCAL_STAGING_DIR", "dhan_staging")).resolve()

    DRIVE_MIRROR = DriveMirror(staging_folder_id, local_root, creds)
    DRIVE_MIRROR.pull_startup_state()

    if legacy_busy(local_root):
        print("Legacy downloader has no confirmed stop. Deferring this run.", flush=True)
        return
    status_file = local_root / "options-run-status.json"
    if status_file.is_file() and json.loads(status_file.read_bytes()).get("state") == "archived_pending_review":
        prior = load_state(local_root / "options-progress.json")
        checked = load_state(local_root / "options-integrity.json")
        names = {f"{s}_{a}_{b}.zip" for s in SYMBOLS for a, b in windows(START, END)}
        if integrity_finished(prior) and all(name in checked and not checked[name]["retry"]
                and name in DRIVE_MIRROR.archive_files and checked[name].get("signature")
                == DRIVE_MIRROR.archive_files[name].get("md5Checksum") for name in names):
            print("All scheduled archive revisions received the final integrity pass.")
            return
    session = open_dhan_session_from_env()
    request_pacer = DownloadPacer(local_root / "options-request-budget.json")

    profile = call_api(session, "profile")
    if str(profile.get("dataPlan", "")).strip().lower() != "active":
        raise RuntimeError("Dhan token accepted, but an active Data API entitlement was not confirmed.")
    account_id = profile.get("dhanClientId") or profile.get("clientId")
    if account_id:
        session.headers["client-id"] = str(account_id)
    print("Dhan token accepted. Data plan active.", flush=True)

    run_status = local_root / "options-run-status.json"

    def record_run_state(state, **details):
        previous = json.loads(run_status.read_text()) if run_status.is_file() else {}
        previous.update(state=state, utc=datetime.now(timezone.utc).isoformat(), **details)
        save_json(run_status, previous)

    save_json(run_status, dict(state="starting", utc=datetime.now(timezone.utc).isoformat()))
    try:
        option_result = download_options(local_root, session)
    except RunTimeBudgetReached:
        print(f"Reached MAX_RUN_MINUTES ({max_run_minutes:.0f}m). Flushing checkpoints to Drive...", flush=True)
        DRIVE_MIRROR.push_in_progress_all()
        record_run_state("paused_time_limit")
        return
    except KeyboardInterrupt:
        DRIVE_MIRROR.push_in_progress_all()
        record_run_state("interrupted")
        raise
    except Exception as exc:
        DRIVE_MIRROR.push_in_progress_all()
        record_run_state("failed", exception_type=type(exc).__name__)
        raise
    else:
        all_archived = integrity_finished(option_result)
        record_run_state("archived_pending_review" if all_archived else "incomplete")
        if not all_archived:
            DRIVE_MIRROR.push_in_progress_all()
            raise RuntimeError("Incomplete history; see options-progress.json in Drive")


if __name__ == "__main__":
    main()
