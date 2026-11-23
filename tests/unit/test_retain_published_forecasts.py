from copy import deepcopy
from datetime import datetime
import json

import pytest

from scripts import public_artifact, retain_published_forecasts as retain

NOW = datetime.fromisoformat("2026-11-23T18:00:00+05:30")


@pytest.fixture
def publication(tmp_path, monkeypatch):
    day = "2026-11-13"
    row = {"expiry": "2026-11-23", "as_of": day + "T15:40:00+05:30", "training_through": day,
           "status": "Ready", "model": "EGARCH(1,1,1) Gaussian", "egarch": 14.3,
           "sessions": 6, "training_returns": 750, "simulations": 20000}
    state = {"schema": 1, "ledger": {"source_authority": "dated_archive"},
             "forecasts": {symbol: {"training_through": day, "yang_zhang20": 14.0, "expiries": [deepcopy(row)]}
                           for symbol in ("NIFTY", "BANKNIFTY")},
             "sources": {"nifty_one_year.csv": "a" * 64, "banknifty_one_year.csv": "b" * 64,
                         "forward_test_ledger.json": "c" * 64},
             "forecast_update": {"completed_session": day, "generated_at": day + "T17:00:00+05:30",
                                 "provider": "Upstox daily candles", "status": "complete"}}
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/live_state.json").write_text(json.dumps(state))
    html = '<main>Current release</main><script type="application/json" id="deskPublicState">{}</script>'
    (tmp_path / "index.html").write_text(html)
    deployed = deepcopy(state)
    deployed["forecast_update"]["generated_at"] = "2026-11-19T18:00:00+05:30"
    deployed["ledger"] = {"wrong": "ledger from a previous release"}
    monkeypatch.setattr(retain.publish_live_state, "ROOT", tmp_path)
    monkeypatch.setattr(retain, "fetch_json", lambda *args: deployed)
    return state, deployed


def test_latest_dated_forecast_reuses_only_forecast_fields(tmp_path, publication):
    local, deployed = publication
    deployed["forecasts"]["NIFTY"]["expiries"][0]["egarch"] = 21.3
    deployed["forecasts"]["NIFTY"]["unrelated"] = "discard me"
    deployed["sources"]["banknifty_one_year.csv"] = "e" * 64
    deployed["sources"]["forward_test_ledger.json"] = "f" * 64
    retain.retain(NOW)
    result = json.loads((tmp_path / "docs/live_state.json").read_bytes())
    assert result["forecast_update"] == deployed["forecast_update"]
    assert result["forecasts"]["NIFTY"]["expiries"][0]["egarch"] == 21.3
    assert "unrelated" not in result["forecasts"]["NIFTY"]
    assert result["ledger"] == local["ledger"]
    assert result["sources"] == {**local["sources"], "banknifty_one_year.csv": "e" * 64}
    html = (tmp_path / "docs/index.html").read_bytes()
    assert html == (tmp_path / "index.html").read_bytes()
    assert b"Current release" in html and b"discard me" not in html


def test_older_training_session_cannot_win_on_generation_time(tmp_path, publication):
    local, deployed = publication
    new_day = "2026-11-12"
    deployed["forecast_update"]["completed_session"] = new_day
    for series in deployed["forecasts"].values():
        series["training_through"] = new_day
        for point in series["expiries"]:
            point.update(training_through=new_day, as_of=new_day + "T15:40:00+05:30")
    retain.retain(NOW)
    assert json.loads((tmp_path / "docs/live_state.json").read_bytes()) == local


@pytest.mark.parametrize("fault", ["missing", "future", "mixed_days", "nonfinite", "duplicate", "hash", "wrong_model"])
def test_unverified_prior_publication_blocks_replacement(fault, publication, tmp_path, monkeypatch):
    local, deployed = publication
    series = deployed["forecasts"]["NIFTY"]
    if fault == "missing":
        def missing(*args):
            raise FileNotFoundError
        monkeypatch.setattr(retain, "fetch_json", missing)
    elif fault == "future":
        deployed["forecast_update"]["generated_at"] = "2099-11-13T18:00:00+05:30"
    elif fault == "mixed_days":
        series["training_through"] = "2026-11-12"
    elif fault == "nonfinite":
        series["expiries"][0]["egarch"] = float("nan")
    elif fault == "duplicate":
        series["expiries"].append(series["expiries"][0])
    elif fault == "hash":
        deployed["sources"]["nifty_one_year.csv"] = "invalid"
    elif fault == "wrong_model":
        series["expiries"][0]["model"] = "EWMA"
    with pytest.raises((ValueError, FileNotFoundError)):
        retain.retain(NOW)
    assert json.loads((tmp_path / "docs/live_state.json").read_bytes()) == local
    assert not (tmp_path / "docs/index.html").exists()


def test_same_origin_reader_size_and_redirects(monkeypatch):
    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, size):
            return [b'{"a":', b'123}']

    response = Response()

    def get(url, **options):
        assert url == "https://volatilitylab.in/live_state.json"
        assert options["allow_redirects"] is False and options["stream"] is True
        assert options["timeout"] == (5, 15)
        return response

    monkeypatch.setattr(public_artifact.requests, "get", get)
    assert public_artifact.fetch_json("live_state.json", 9) == {"a": 123}
    with pytest.raises(ValueError, match="size"):
        public_artifact.fetch_json("live_state.json", 8)
    with pytest.raises(ValueError, match="name"):
        public_artifact.fetch_json("../live_state.json", 9)
    response.status_code = 302
    with pytest.raises(RuntimeError, match="302"):
        public_artifact.fetch_json("live_state.json", 9)
    response.status_code = 404
    with pytest.raises(FileNotFoundError):
        public_artifact.fetch_json("live_state.json", 9)
