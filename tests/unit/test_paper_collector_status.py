from datetime import datetime, timedelta
import json

import pytest

from scripts import paper_collector_status as status


NOW = datetime.fromisoformat("2026-11-23T15:49:08+05:30")


def data(start):
    ms = round(start.timestamp() * 1000)
    return {"capture_profile": "minute_snapshots_only", "runtime_policy": {"snapshot_minutes": 85},
            "budget": json.dumps(dict(windowStartMs=ms, windowEndMs=ms + 86400000, ms=4829918)),
            "last_snapshot": json.dumps({"retrieved_at": "2026-11-23T10:10:22Z", "seconds": 9.939,
                                         "fetch_seconds": 3.633, "archive_seconds": 2.082, "file_id": "private-id"})}


def test_active_expired_and_actual_capture_times():
    current = status._format(data(NOW - timedelta(hours=2)), NOW)
    for text in ("80.50 of 85 minutes", "4.50 minutes", "2026-11-23 15:40:22 IST", "total 9.94s", "archive 2.08s"):
        assert text in current
    assert "private-id" not in current
    expired = status._format(data(NOW - timedelta(days=1)), NOW)
    assert "expired" in expired and "remaining allowance are unavailable" in expired
    assert "4.50 minutes" not in expired
    missing = status._format(dict(data(NOW), budget=None, last_snapshot=None), NOW)
    assert "no usable current" in missing and "timings are unavailable" in missing


class Response:
    def __init__(self, code, body=b"", location=None):
        self.status_code = code
        self.headers = {"Location": location}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_content(self, size):
        yield self.body


def mock_requests(monkeypatch, responses):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kw):
            assert self.trust_env is False and kw == {"allow_redirects": False, "timeout": (3, 6), "stream": True}
            calls.append(url)
            return responses[len(calls) - 1]

    monkeypatch.setattr(status.requests, "Session", Session)
    return calls


def test_google_content_redirect_and_soft_fail(monkeypatch):
    echo = "https://script.googleusercontent.com/macros/echo?user_content_key=fixture"
    calls = mock_requests(monkeypatch, [Response(302, location=echo), Response(200, json.dumps(data(NOW)).encode())])
    assert status._read()["capture_profile"] == "minute_snapshots_only"
    assert len(calls) == 2 and calls[0].endswith("/exec?action=status") and calls[1] == echo
    mock_requests(monkeypatch, [Response(500)])
    assert "unavailable" in status.collector_runtime_summary()
    mock_requests(monkeypatch, [Response(200, b"x" * (status.MAX_BYTES + 1))])
    assert "unavailable" in status.collector_runtime_summary()


@pytest.mark.parametrize("redirect", ["https://example.com/macros/echo?hidden", "https://script.googleusercontent.com:8443/macros/echo",
                                      "https://user@script.googleusercontent.com/macros/echo", "http://script.googleusercontent.com/macros/echo"])
def test_other_redirect_target_is_never_called(monkeypatch, redirect):
    calls = mock_requests(monkeypatch, [Response(302, location=redirect)])
    assert "unavailable" in status.collector_runtime_summary()
    assert len(calls) == 1


def test_untrusted_configuration_never_sends_request(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"web_app_url": "https://example.com/macros/s/valid/exec"}))
    monkeypatch.setattr(status, "CONFIG", config)
    calls = mock_requests(monkeypatch, [])
    assert "unavailable" in status.collector_runtime_summary() and not calls
