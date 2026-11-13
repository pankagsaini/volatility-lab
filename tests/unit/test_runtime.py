from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
import json
import sqlite3
from urllib.error import HTTPError

import pytest
from volatility_lab.runtime.settings import load_settings, ConfigurationError
from volatility_lab.runtime.health import inspect
from volatility_lab.runtime.external import ExternalReads, SourceUnavailable
from volatility_lab.storage.references import initialize_references, verified_references, text_digest


def test_settings_precedence_profiles_and_secrets(tmp_path):
    envfile = tmp_path / "config"
    envfile.write_text("UPSTOX_TOKEN=private-fixture\nVOL_LAB_ENV=staging\nSTREAMLIT_SERVER_PORT=8001\n")
    settings = load_settings({"VOL_LAB_ENV_FILE": str(envfile), "STREAMLIT_SERVER_PORT": "8002"})
    assert settings.port == 8002 and settings.environment == "staging"
    assert settings.upstox_token == "private-fixture" and "private-fixture" not in repr(settings)
    for key, value in [
        ("VOL_LAB_ENV", "invalid"),
        ("VOL_LAB_OFFLINE", "perhaps"),
        ("VOL_LAB_DATA_DIR", "relative"),
        ("STREAMLIT_SERVER_PORT", "99999"),
        ("GOOGLE_APPS_SCRIPT_WEBHOOK_URL", "https://example.org/?key=private-fixture"),
    ]:
        with pytest.raises(ConfigurationError) as error:
            load_settings({key: value})
        assert key in str(error.value) and "private-fixture" not in str(error.value)


def test_readonly_health_distinguishes_stale_partial_and_corrupt(tmp_path):
    root = tmp_path / "store"
    settings = load_settings({"VOL_LAB_DATA_DIR": str(root)})
    assert inspect(settings)["status"] == "degraded"
    assert not root.exists()
    root.mkdir()
    with closing(sqlite3.connect(root / "monitor.sqlite")) as db:
        db.execute("CREATE TABLE marker(id INTEGER PRIMARY KEY)")
    now = datetime(2026, 11, 13, 6, tzinfo=timezone.utc)
    (root / "google_archive").mkdir()
    (root / "google_archive/status.json").write_text(json.dumps({"updated": now.isoformat(), "status": "partial"}))
    (root / "expiry_latest.json").write_text(
        json.dumps({"provider": "Upstox", "rows": [{}], "timestamp": "2026-11-13T05:57:59Z"})
    )
    before = (root / "monitor.sqlite").read_bytes()
    states = {r["component"]: r["status"] for r in inspect(settings, now)["checks"]}
    assert states == {"storage": "ok", "database": "ok", "snapshot": "stale", "archive_sync": "degraded"}
    assert (root / "monitor.sqlite").read_bytes() == before
    (root / "expiry_latest.json").write_text("[]")
    assert inspect(settings, now)["status"] == "error"


def test_reference_integrity_and_existing_observations(tmp_path):
    source = tmp_path / "seeds"
    source.mkdir()
    (source / "obs.csv").write_bytes(b"a\r\n")
    manifest = {"schema": 1, "files": {"obs.csv": text_digest(b"a\n")}}
    (source / "reference-manifest.json").write_text(json.dumps(manifest))
    store = tmp_path / "store"
    initialize_references(store, source)
    (store / "obs.csv").write_text("new local observation")
    initialize_references(store, source)
    assert (store / "obs.csv").read_text() == "new local observation"
    (source / "obs.csv").write_text("wrong")
    with pytest.raises(ValueError, match="checksum"):
        initialize_references(tmp_path / "other", source)
    assert not (tmp_path / "other").exists()
    assert len(list(verified_references())) >= 20


def test_reads_retry_and_open_then_recover_per_source():
    clock = [100.0]
    waits = []
    reads = ExternalReads(lambda: clock[0], waits.append)

    def offline():
        raise OSError("fixture")

    for _ in range(3):
        with pytest.raises(OSError):
            reads.read("quotes", offline)
    assert waits == [1, 2] * 3
    with pytest.raises(SourceUnavailable):
        reads.read("quotes", lambda: 42)
    assert reads.read("other", lambda: 42) == 42
    clock[0] += 61
    assert reads.read("quotes", lambda: 43) == 43

    def rate_limit():
        raise HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "120"}, None)

    with pytest.raises(HTTPError):
        reads.read("quotes", rate_limit)
    with pytest.raises(SourceUnavailable):
        reads.read("quotes", lambda: 44)
    assert waits == [1, 2] * 3


def test_broker_response_contract_and_offline_guard(monkeypatch):
    from volatility_lab.ingestion.upstox_data import UpstoxDataClient, UpstoxDataError
    from types import SimpleNamespace

    client = UpstoxDataClient("test-token")
    for raw in [b"[]", b"null", b"{}", b'{"status":"success","data":null}', b"not-json"]:
        client._opener = SimpleNamespace(open=lambda *args, **kwargs: BytesIO(raw))
        with pytest.raises(UpstoxDataError):
            client.quotes(["NSE_INDEX|Nifty 50"])
    monkeypatch.setenv("VOL_LAB_OFFLINE", "true")
    client._opener = SimpleNamespace(open=lambda *a, **kw: pytest.fail("Unexpected network request"))
    with pytest.raises(UpstoxDataError, match="offline"):
        client.quotes([])
