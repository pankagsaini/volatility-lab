"""Exercise the encrypted restart boundary without a network or a real credential."""

import copy
import gzip
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import build_paper_feed as feed
from volatility_lab.backtest.hosted_paper import Capture


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.org",
                           *args], check=True, capture_output=True).stdout.decode().strip()


def test_encrypted_restart_and_missing_checkpoint(tmp_path, monkeypatch):
    if not shutil.which("gpg"):
        pytest.skip("Requires the GnuPG program used on the hosted runner")
    repo = tmp_path / "feed"
    repo.mkdir()
    git(repo, "init")
    seed = Path(__file__).resolve().parents[2] / feed.PUBLIC
    shutil.copyfile(seed, repo / feed.PUBLIC)
    git(repo, "add", feed.PUBLIC)
    git(repo, "commit", "-m", "Armed test cohort")
    monkeypatch.setattr(feed, "ARMED_COMMIT", git(repo, "rev-parse", "HEAD"))

    class Reader:
        folder_id = "fixture-private-folder"
        downloads = 0

        def list_captures(self, start_date, cursor):
            if start_date < feed.START:
                yield Capture("2026-11-20_snapshot_091500_complete.json.gz", "private-id")

        def download(self, capture, path):
            self.downloads += 1
            stamp = "2026-11-20T09:15:00+05:30"
            expiry = "2026-11-24"
            body = {
                "schema": 1, "provider": "Upstox", "mode": "snapshot", "status": "complete",
                "day": stamp[:10], "retrieved_at": stamp, "nfo_session_validated": True,
                "catalogue": {"indices": {"NIFTY": {"option_expiries": [expiry], "futures": []}}},
                "records": [
                    {"label": "quotes", "data": {}, "retrieved_at": stamp},
                    {"label": "NIFTY|" + expiry, "data": [{"expiry": expiry, "strike_price": 100,
                                                           "underlying_spot_price": 100}], "retrieved_at": stamp},
                ],
            }
            path.write_bytes(gzip.compress(json.dumps(body).encode()))

    key = b"ephemeral-test-only-passphrase-42"
    reader = Reader()

    def run(name, passphrase=key):
        private = tmp_path / name
        private.mkdir()
        feed.update_feed(repo, private, passphrase, reader)

    run("first")
    assert reader.downloads == 1
    public = json.loads((repo / feed.PUBLIC).read_bytes())
    assert public["capture_audit"]["processed"] == 0
    assert public["updated_at"] is None
    assert (repo / feed.PUBLIC).read_bytes() == seed.read_bytes()
    sealed = (repo / feed.SEALED).read_bytes()
    assert reader.folder_id.encode() not in sealed and b"worker_checkpoint" not in sealed
    assert {p.name for p in repo.iterdir()} == {".git", feed.PUBLIC, feed.SEALED}
    git(repo, "add", feed.SEALED)
    git(repo, "commit", "-m", "Checkpoint installed")
    run("second")
    assert reader.downloads == 1
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / feed.SEALED).read_bytes() == sealed
    with pytest.raises(ValueError, match="authentication"):
        run("wrong-key", b"X" * 32)

    (repo / feed.SEALED).write_bytes(sealed[:len(sealed)//2])
    git(repo, "commit", "-am", "Truncated")
    with pytest.raises(ValueError, match="authentication"):
        run("corrupt")
    (repo / feed.SEALED).unlink()
    git(repo, "commit", "-am", "Lost")
    with pytest.raises(ValueError, match="missing checkpoint"):
        run("missing")


def test_preserve_published_closes_and_capture_time():
    prior = {"capture_audit": {"processed": 2, "exceptions": 0},
             "last_capture_at": "2026-11-23T10:00:00+05:30", "closed_trades": [{"id": "paper", "net": 42}]}
    candidate = copy.deepcopy(prior)
    feed.verify_progress(prior, candidate)
    candidate["closed_trades"].clear()
    with pytest.raises(ValueError, match="closed trade"):
        feed.verify_progress(prior, candidate)
    candidate = dict(prior, last_capture_at="2026-11-23T09:59:59+05:30")
    with pytest.raises(ValueError, match="backwards"):
        feed.verify_progress(prior, candidate)


def test_authentication_rejects_custom_token_endpoint(tmp_path):
    credential = {"type": "service_account", "client_email": "reader@fixture.iam.gserviceaccount.com",
                  "token_uri": "https://invalid.example/token", "private_key": "fixture-secret"}
    with pytest.raises(ValueError, match="configuration"):
        feed.drive_token(credential, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_paired_daily_history_install_and_corrupt_source(tmp_path, monkeypatch):
    from scripts import paper_risk_history as risk
    from volatility_lab.ingestion.google_archive_sync import _cached_one_year_csv
    from volatility_lab.ingestion import google_archive_sync as archive

    output = tmp_path / "public.json"
    risk.write_bundle(risk.REFERENCE_DIR, output)
    bundle = json.loads(output.read_bytes())
    monkeypatch.setattr(risk, "fetch_bundle", lambda: risk.validate(bundle))
    monkeypatch.setattr(archive, "DATA", tmp_path)
    risk.install(tmp_path)
    for symbol in ("nifty", "banknifty"):
        assert _cached_one_year_csv(symbol).Date.iloc[-1] == bundle["through"]
    first = (tmp_path / risk.NAMES[0]).read_bytes()
    bundle["files"][risk.NAMES[1]]["sha256"] = "invalid"
    with pytest.raises(ValueError, match="checksum"):
        risk.install(tmp_path)
    assert (tmp_path / risk.NAMES[0]).read_bytes() == first
    bundle["files"]["../another.csv"] = {}
    with pytest.raises(ValueError, match="Unknown"):
        risk.install(tmp_path)
