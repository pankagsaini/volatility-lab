"""Consume archived market observations on a disposable runner using read-only Drive calls."""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import re
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Protocol

import requests

from volatility_lab.backtest import paper_worker as worker
from volatility_lab.core.market_calendar import last_trading_day_on_or_before
from volatility_lab.storage.collector_support import atomic_json, data_lock

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_ID = re.compile(r"[A-Za-z0-9_-]+")
MAX_ARCHIVE_BYTES = 20_000_000


@dataclass(frozen=True)
class Capture:
    name: str
    file_id: str
    size: int | None = None
    md5: str | None = None


class ArchiveReader(Protocol):
    folder_id: str

    def list_captures(self, start_date: str, cursor: str) -> Iterable[Capture]: ...

    def download(self, capture: Capture, destination: Path) -> None: ...


class MissingPriorClose(RuntimeError):
    """Keep this capture and all subsequent captures pending until the reference history is available."""


def _require_prior_close(report):
    if report.get("capture_profile") != "minute_snapshots_only":
        return
    day = report["day"]
    previous = last_trading_day_on_or_before(date.fromisoformat(day) - timedelta(days=1), include_special=True)
    metrics = report.get("risk_metrics") or {}
    for symbol in report["catalogue"]["indices"].keys() & {"NIFTY", "BANKNIFTY"}:
        item = metrics.get(symbol)
        if (not isinstance(item, dict) or item.get("as_of") != previous.isoformat()
                or None in worker.engine._risk_vol_metrics(report, symbol, day)):
            raise MissingPriorClose(f"Missing usable {symbol} risk inputs for the previous session {previous}")


class DriveReader:
    def __init__(self, folder_id: str, token: str):
        if not DRIVE_ID.fullmatch(folder_id) or not token or any(c.isspace() for c in token):
            raise ValueError("A Drive folder ID and access token are required")
        self.folder_id = folder_id
        self._token = token

    @contextmanager
    def _response(self, file_id, params):
        url = f"{DRIVE_FILES}/{file_id}" if file_id else DRIVE_FILES
        try:
            with requests.get(
                url, params={**params, "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {self._token}", "Accept-Encoding": "identity"},
                stream=True, timeout=(10, 45), allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"Drive read failed (HTTP {response.status_code})")
                yield response
        except requests.RequestException:
            raise RuntimeError("Drive read interrupted") from None

    def _metadata(self, file_id, params):
        body = io.BytesIO()
        with self._response(file_id, params) as response:
            for chunk in response.iter_content(64 * 1024):
                if body.tell() + len(chunk) > 2_000_000:
                    raise RuntimeError("Drive metadata exceeds the page limit")
                body.write(chunk)
        result = json.loads(body.getvalue())
        if not isinstance(result, dict):
            raise RuntimeError("Invalid Drive metadata")
        return result

    def verify_folder(self):
        folder = self._metadata(self.folder_id, {"fields": "id,mimeType,trashed,capabilities(canListChildren),driveId"})
        if (folder.get("id") != self.folder_id or folder.get("mimeType") != "application/vnd.google-apps.folder"
                or folder.get("trashed") is not False or folder.get("capabilities", {}).get("canListChildren") is not True):
            raise RuntimeError("Drive archive folder is unavailable")
        return folder

    def list_captures(self, start_date, cursor=""):
        folder = self.verify_folder()
        params = {
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "spaces": "drive", "corpora": "user", "includeItemsFromAllDrives": "true", "pageSize": 1000,
            "fields": "nextPageToken,incompleteSearch,files(id,name,size,md5Checksum)",
        }
        if folder.get("driveId"):
            params.update(corpora="drive", driveId=folder["driveId"])
        tokens = set()
        while True:
            page = self._metadata("", params)
            if page.get("incompleteSearch") or not isinstance(page.get("files"), list):
                raise RuntimeError("Incomplete Drive archive listing")
            for row in page["files"]:
                name = row["name"]
                if worker.CAPTURE.fullmatch(name) and name[:10] >= start_date and name > cursor:
                    if not DRIVE_ID.fullmatch(row["id"]):
                        raise ValueError("Invalid Drive capture ID")
                    yield Capture(name, row["id"], int(row["size"]), row.get("md5Checksum"))
            token = page.get("nextPageToken")
            if not token:
                return
            if token in tokens:
                raise RuntimeError("Drive archive repeated a page token")
            tokens.add(token)
            params["pageToken"] = token

    def download(self, capture, destination):
        if not DRIVE_ID.fullmatch(capture.file_id):
            raise ValueError("Invalid Drive capture ID")
        if capture.size is not None and capture.size > MAX_ARCHIVE_BYTES:
            raise ValueError("Compressed capture exceeds the archive limit")
        digest = hashlib.md5(usedforsecurity=False)  # Drive's checksum for binary files.
        with self._response(capture.file_id, {"alt": "media"}) as response, destination.open("xb") as stream:
            for chunk in response.iter_content(64 * 1024):
                if stream.tell() + len(chunk) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Compressed capture exceeds the archive limit")
                stream.write(chunk)
                digest.update(chunk)
            if ((capture.size is not None and stream.tell() != capture.size)
                    or (capture.md5 and digest.hexdigest() != capture.md5)):
                raise RuntimeError("Drive capture changed or transfer was incomplete")


def _prefix_digest(previous, capture):
    # Anchor the immutable chronological prefix even if a later listing inserts an older capture.
    record = json.dumps([previous, capture.name, capture.file_id, capture.size, capture.md5]).encode()
    return hashlib.sha256(record).hexdigest()


def _eligible(reader, start_date, checkpoint):
    by_name = {}
    for capture in reader.list_captures(start_date, ""):
        if not worker.CAPTURE.fullmatch(capture.name) or capture.name[:10] < start_date:
            continue
        if by_name.setdefault(capture.name, capture) != capture:
            raise ValueError("Conflicting Drive captures have the same filename")
    cursor = checkpoint["cursor"]
    digest, last, pending = "", "", []
    for name in sorted(by_name):
        capture = by_name[name]
        if name <= cursor:
            digest = _prefix_digest(digest, capture)
            last = name
        else:
            pending.append(capture)
    if last != cursor or digest != checkpoint.get("archive_prefix_sha256", ""):
        raise ValueError("The committed Drive capture history changed. Reconcile the private checkpoint.")
    return pending


def build_batch(reader: ArchiveReader, state_dir, start_date, *, max_captures=None):
    """Advance an ordered prefix; return run metadata and the allowlisted public book."""
    if max_captures is not None and max_captures < 1:
        raise ValueError("max_captures must be positive")
    ledger = Path(state_dir) / worker.engine.LEDGER_FILENAME
    with data_lock(lock_path=ledger.with_suffix(".lock"), timeout=10):
        if ledger.exists():
            state = json.loads(ledger.read_bytes())
            worker.engine._validate_ledger(state)
            checkpoint = state.get("worker_checkpoint") or {}
            if (state.get("source_authority") != worker.AUTHORITY or state.get("cohort_kind") != "prospective"
                    or state.get("prospective_start") != start_date or checkpoint.get("drive_folder_id") != reader.folder_id
                    or "archive_dir" in checkpoint):
                raise ValueError("Private checkpoint belongs to another cohort or archive")
        else:
            if ledger.with_suffix(".bak").exists():
                raise ValueError("Private ledger is missing while its backup exists")
            state = worker.initial_state(start_date)
            checkpoint = state["worker_checkpoint"]
            checkpoint["drive_folder_id"] = reader.folder_id

        captures = _eligible(reader, start_date, checkpoint)
        if not ledger.exists():
            atomic_json(ledger, state)
        before = checkpoint["completed"]
        pending = None
        # Drive transport failures interrupt the batch. Only rejected capture contents may be quarantined.
        with tempfile.TemporaryDirectory(prefix="paper-captures-") as staging:
            for index, capture in enumerate(captures[:max_captures]):
                path = Path(staging) / capture.name
                try:
                    reader.download(capture, path)
                    report, digest = worker._read_report(path)
                    _require_prior_close(report)
                    # _apply can flag a market gap before the engine rejects a report. Roll that back on retry.
                    state = worker._apply(copy.deepcopy(state), report, capture.name, digest)
                except (EOFError, ValueError, zlib.error, gzip.BadGzipFile) as exc:
                    if index == len(captures) - 1:
                        pending = capture.name
                        break
                    worker._note_gap(state, {"from": capture.name, "reason": type(exc).__name__})
                finally:
                    path.unlink(missing_ok=True)
                checkpoint = state["worker_checkpoint"]
                checkpoint["archive_prefix_sha256"] = _prefix_digest(checkpoint.get("archive_prefix_sha256", ""), capture)
                checkpoint["cursor"] = capture.name
                atomic_json(ledger, state)
        metadata = {
            "processed": checkpoint["completed"] - before, "cursor": checkpoint["cursor"], "pending": pending,
            "more": bool(max_captures and len(captures) > max_captures),
        }
        return metadata, worker.public_state(state)
