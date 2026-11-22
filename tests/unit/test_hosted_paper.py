import gzip
import hashlib
import json
import shutil
from datetime import date, datetime

import pytest

from volatility_lab.backtest import hosted_paper as hosted

DAY = "2026-11-23"
LEDGER = hosted.worker.engine.LEDGER_FILENAME


@pytest.fixture(autouse=True)
def market_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 23, 17, tzinfo=tz)

    monkeypatch.setattr(hosted.worker, "datetime", Clock)


def name(time):
    return f"{DAY}_snapshot_{time}_complete.json.gz"


def report(time):
    stamp = f"{DAY}T{time[:2]}:{time[2:4]}:{time[4:]}+05:30"
    expiry = "2026-11-30"
    payload = {
        "schema": 1, "day": DAY, "provider": "Upstox", "mode": "snapshot", "status": "complete",
        "retrieved_at": stamp, "nfo_session_validated": True,
        "catalogue": {"indices": {"NIFTY": {"futures": [], "option_expiries": [expiry]}}},
        "records": [
            {"label": "quotes", "retrieved_at": stamp, "data": {}},
            {"label": "NIFTY|" + expiry, "retrieved_at": stamp,
             "data": [{"expiry": expiry, "underlying_spot_price": 25000, "strike_price": 25000}]},
        ],
    }
    return gzip.compress(json.dumps(payload).encode(), mtime=0)


class MemoryArchive:
    folder_id = "private-folder"

    def __init__(self, rows):
        self.rows = rows
        self.downloads = []

    def list_captures(self, start_date, cursor):
        for filename, data in self.rows.items():
            if filename > cursor:
                yield hosted.Capture(filename, filename, len(data), hashlib.md5(data).hexdigest())

    def download(self, capture, destination):
        self.downloads.append(destination)
        destination.write_bytes(self.rows[capture.name])


def test_ordered_catchup_atomic_checkpoints_and_public_export(tmp_path):
    archive = MemoryArchive({
        name("091810"): report("091810"), name("091510"): report("091510"),
        name("091610"): report("091610"), "2026-11-20_snapshot_091510_complete.json.gz": b"old",
        DAY + "_snapshot_091710_partial.json.gz": b"partial",
    })
    first = tmp_path / "runner1"
    metadata, public = hosted.build_batch(archive, first, DAY, max_captures=2)
    assert metadata == {"cursor": name("091610"), "pending": None, "more": True, "processed": 2}
    assert [p.name for p in archive.downloads] == [name("091510"), name("091610")]
    assert all(not p.exists() and first not in p.parents for p in archive.downloads)
    second = tmp_path / "runner2"
    second.mkdir()
    shutil.copy(first / LEDGER, second / LEDGER)
    metadata, public = hosted.build_batch(archive, second, DAY)
    assert metadata["processed"] == 1 and not metadata["more"]
    assert public["capture_audit"] == {"processed": 3, "exceptions": 1}
    assert public["last_capture_at"] == DAY + "T09:18:10+05:30"
    assert archive.folder_id not in json.dumps(public)
    assert "worker_checkpoint" not in public and name("091810") not in json.dumps(public)
    pristine = (second / LEDGER).read_bytes()
    assert str(first).encode() not in pristine
    assert hosted.build_batch(archive, second, DAY)[0]["processed"] == 0
    assert (second / LEDGER).read_bytes() == pristine
    archive.folder_id = "different-folder"
    with pytest.raises(ValueError, match="another cohort or archive"):
        hosted.build_batch(archive, second, DAY)
    assert (second / LEDGER).read_bytes() == pristine


def test_malformed_newest_is_retried_then_older_corruption_quarantined(tmp_path):
    archive = MemoryArchive({name("091510"): report("091510"), name("091610"): b"truncated"})
    metadata, public = hosted.build_batch(archive, tmp_path, DAY)
    assert metadata["pending"] == name("091610") and metadata["cursor"] == name("091510")
    assert public["capture_audit"] == {"processed": 1, "exceptions": 0}
    before = (tmp_path / LEDGER).read_bytes()
    assert hosted.build_batch(archive, tmp_path, DAY)[0]["pending"] == name("091610")
    assert (tmp_path / LEDGER).read_bytes() == before
    archive.rows[name("091710")] = report("091710")
    metadata, public = hosted.build_batch(archive, tmp_path, DAY, max_captures=1)
    assert metadata == {"processed": 0, "cursor": name("091610"), "pending": None, "more": True}
    assert public["capture_audit"] == {"processed": 1, "exceptions": 1}
    metadata, public = hosted.build_batch(archive, tmp_path, DAY)
    assert public["capture_audit"] == {"processed": 2, "exceptions": 2}
    assert metadata["cursor"] == name("091710")


def test_older_late_arrival_or_change_requires_reconciliation(tmp_path):
    archive = MemoryArchive({name("091610"): report("091610")})
    hosted.build_batch(archive, tmp_path, DAY)
    before = (tmp_path / LEDGER).read_bytes()
    archive.rows[name("091510")] = report("091510")
    with pytest.raises(ValueError, match="committed Drive capture history changed"):
        hosted.build_batch(archive, tmp_path, DAY)
    del archive.rows[name("091510")]
    archive.rows[name("091610")] = report("091611")
    with pytest.raises(ValueError, match="history changed"):
        hosted.build_batch(archive, tmp_path, DAY)
    assert (tmp_path / LEDGER).read_bytes() == before


def test_transport_failure_preserves_cursor_for_retry(tmp_path, monkeypatch):
    archive = MemoryArchive({name("091510"): report("091510"), name("091610"): report("091610")})
    download = archive.download

    def interrupted(capture, destination):
        if capture.name == name("091610"):
            raise OSError("connection interrupted")
        download(capture, destination)

    monkeypatch.setattr(archive, "download", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        hosted.build_batch(archive, tmp_path, DAY)
    state = json.loads((tmp_path / LEDGER).read_bytes())
    assert state["worker_checkpoint"]["cursor"] == name("091510")
    assert state["worker_checkpoint"]["quarantine"] == []
    monkeypatch.setattr(archive, "download", download)
    assert hosted.build_batch(archive, tmp_path, DAY)[0]["processed"] == 1


def test_gate_rejection_cannot_mutate_the_public_checkpoint(tmp_path, monkeypatch):
    archive = MemoryArchive({name("091510"): report("091510")})
    _, before = hosted.build_batch(archive, tmp_path, DAY)
    archive.rows[name("091810")] = report("091810")
    # The new event is beyond a missing market minute, which _apply flags before checking the gate.
    monkeypatch.setattr(hosted.worker.engine, "process_live_upstox_snapshot", lambda *a, **k: {"gate_blocked": True})
    metadata, after = hosted.build_batch(archive, tmp_path, DAY)
    assert metadata["pending"] == name("091810") and after == before


def test_minute_only_and_legacy_inputs_use_their_point_in_time_risk(tmp_path, monkeypatch):
    import pandas as pd
    from volatility_lab.ingestion import google_archive_sync as validator
    from volatility_lab.ingestion.snapshot_risk import prior_close_metrics

    reference = tmp_path / "references"
    reference.mkdir()
    dates = pd.bdate_range(end="2026-11-24", periods=43)
    frame = pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": [24000 + i**2 for i in range(len(dates))]})
    frame.to_csv(reference / "nifty_one_year.csv", index=False)
    monkeypatch.setattr(validator, "DATA", reference)
    monkeypatch.setattr(validator, "_ONE_YEAR_CSV_CACHE", {})

    def with_risk(time, **fields):
        raw = json.loads(gzip.decompress(report(time)))
        return gzip.compress(json.dumps(dict(raw, **fields)).encode())

    legacy = {"NIFTY": {"as_of": "2026-11-20", "hv10": 12, "hv30": 14}}
    archive = MemoryArchive({
        name("091510"): with_risk("091510", capture_profile="minute_snapshots_only"),
        name("091610"): with_risk("091610", risk_metrics=legacy),
    })
    actual = []
    process = hosted.worker.engine.process_live_upstox_snapshot

    def observe(payload, **kwargs):
        actual.append(payload["risk_metrics"])
        return process(payload, **kwargs)

    monkeypatch.setattr(hosted.worker.engine, "process_live_upstox_snapshot", observe)
    meta, _ = hosted.build_batch(archive, tmp_path / "state", DAY)
    expected = prior_close_metrics(frame.loc[frame.Date <= "2026-11-20"], date.fromisoformat(DAY))
    assert expected["as_of"] == "2026-11-20"
    assert meta["processed"] == 2 and actual == [{"NIFTY": expected}, legacy]


def test_wait_for_the_previous_session_for_each_index_before_consuming(tmp_path, monkeypatch):
    import pandas as pd
    from volatility_lab.ingestion import google_archive_sync as validator

    reference = tmp_path / "references"
    reference.mkdir()
    dates = pd.bdate_range(end="2026-11-20", periods=43)
    frame = pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": [24000 + i**2 for i in range(len(dates))]})
    frame.to_csv(reference / "nifty_one_year.csv", index=False)
    bank_path = reference / "banknifty_one_year.csv"
    # Thursday's close is recent enough for the engine's five-day window. Friday is the actual prior session.
    frame.iloc[:-1].to_csv(bank_path, index=False)
    monkeypatch.setattr(validator, "DATA", reference)
    monkeypatch.setattr(validator, "_ONE_YEAR_CSV_CACHE", {})
    item = json.loads(gzip.decompress(report("091610")))
    item["capture_profile"] = "minute_snapshots_only"
    item["catalogue"]["indices"]["BANKNIFTY"] = item["catalogue"]["indices"]["NIFTY"]
    item["records"].append(dict(item["records"][-1], label="BANKNIFTY|2026-11-30"))
    archive = MemoryArchive({
        name("091510"): report("091510"), name("091610"): gzip.compress(json.dumps(item).encode()),
        name("091710"): report("091710"),
    })
    work = tmp_path / "state"
    with pytest.raises(hosted.MissingPriorClose, match="BANKNIFTY.*2026-11-20"):
        hosted.build_batch(archive, work, DAY)
    checkpoint = json.loads((work / LEDGER).read_bytes())["worker_checkpoint"]
    assert checkpoint["cursor"] == name("091510") and checkpoint["quarantine"] == []
    assert [p.name for p in archive.downloads] == [name("091510"), name("091610")]
    frame.to_csv(bank_path, index=False)
    meta, public = hosted.build_batch(archive, work, DAY)
    assert meta["processed"] == 2 and public["capture_audit"] == {"processed": 3, "exceptions": 0}


FOLDER = {"id": "folder", "mimeType": "application/vnd.google-apps.folder", "trashed": False,
          "capabilities": {"canListChildren": True}}


class Response:
    def __init__(self, value, status_code=200):
        self.chunks = value if isinstance(value, list) else [json.dumps(value).encode()]
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_content(self, chunk_size):
        yield from self.chunks


def test_drive_checks_directory_and_reads_every_page_including_empty(monkeypatch):
    params = []
    responses = iter([
        Response(dict(FOLDER, driveId="shared")),
        Response({"files": [], "nextPageToken": "next"}),
        Response({"files": [{"id": "id1", "name": name("091510"), "size": "45"}]}),
    ])

    def get(url, **kwargs):
        assert url.startswith("https://www.googleapis.com/drive/v3/files")
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
        params.append(kwargs["params"])
        return next(responses)

    monkeypatch.setattr(hosted.requests, "get", get)
    listed = list(hosted.DriveReader("folder", "token").list_captures(DAY))
    assert listed == [hosted.Capture(name("091510"), "id1", 45)]
    assert params[1]["q"] == "'folder' in parents and trashed = false"
    assert params[1]["driveId"] == "shared" and params[1]["corpora"] == "drive"
    assert params[2]["pageToken"] == "next"


@pytest.mark.parametrize("folder,page", [
    (dict(FOLDER, trashed=True), {}), (dict(FOLDER, id="wrong"), {}),
    (dict(FOLDER, capabilities={"canListChildren": False}), {}),
    (FOLDER, {"files": [], "incompleteSearch": True}),
])
def test_drive_empty_or_unavailable_is_fail_closed(tmp_path, monkeypatch, folder, page):
    responses = iter([Response(folder), Response(page)])
    monkeypatch.setattr(hosted.requests, "get", lambda *a, **k: next(responses))
    with pytest.raises(RuntimeError):
        hosted.build_batch(hosted.DriveReader("folder", "test-token"), tmp_path, DAY)
    assert not (tmp_path / LEDGER).exists()


def test_bounded_download_rejects_truncated_or_oversized_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(hosted, "MAX_ARCHIVE_BYTES", 5)
    monkeypatch.setattr(hosted.requests, "get", lambda *a, **k: Response([b"abc", b"def"]))
    reader = hosted.DriveReader("folder", "token")
    with pytest.raises(ValueError, match="archive limit"):
        reader.download(hosted.Capture(name("091510"), "id"), tmp_path / "over")
    assert (tmp_path / "over").stat().st_size <= 5
    monkeypatch.setattr(hosted.requests, "get", lambda *a, **k: Response([b"abc"]))
    with pytest.raises(RuntimeError, match="transfer was incomplete"):
        reader.download(hosted.Capture(name("091510"), "id", size=4), tmp_path / "short")
