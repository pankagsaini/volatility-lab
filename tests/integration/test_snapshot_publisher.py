import gzip
import json
import re

import pytest
from scripts import publish_snapshot_history as publisher


def test_publish_keeps_missing_broker_leg_and_unobserved_close_null(tmp_path, monkeypatch):
    day, expiry, capture = "2026-11-13", "2026-11-17", "2026-11-13T07:44:47Z"
    raw = {"schema": 1, "provider": "Upstox", "status": "complete", "day": day,
           "mode": "snapshot", "nfo_session_validated": True, "retrieved_at": capture,
           "catalogue": {"indices": {}},
           "records": [{"label": "quotes", "data": {}, "retrieved_at": capture}]}
    for symbol in ("NIFTY", "BANKNIFTY"):
        raw["catalogue"]["indices"][symbol] = {"futures": [], "option_expiries": [expiry]}
        raw["records"].append({"label": symbol + "|" + expiry, "retrieved_at": capture,
                               "data": [{"strike_price": 25000, "expiry": expiry, "underlying_spot_price": 25001,
                                         "call_options": {"option_greeks": {"iv": 16}}, "put_options": None}]})
    filename = tmp_path / (day + "_snapshot_131447_complete.json.gz")
    filename.write_bytes(gzip.compress(json.dumps(raw).encode()))
    (tmp_path / "docs").mkdir()
    page = tmp_path / "index.html"
    page.write_text('var REAL_HISTORICAL_1Y = {"NIFTY":[],"BANKNIFTY":[]};\nvar marketCatalog = {};\n')
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    publisher.build(tmp_path, day)
    html = page.read_text()
    history = json.loads(re.search(r"var REAL_HISTORICAL_1Y = (.*);", html)[1])
    catalog = json.loads(re.search(r"var marketCatalog = (.*);", html)[1])
    assert history["NIFTY"][0]["c"] is None and history["NIFTY"][0]["iv"] is None
    opt = catalog["BANKNIFTY"][0]
    assert opt["iv"] is None and opt["pIv"] is None and opt["cIv"] == 16
    assert opt["intraday"][0]["strad"] is None and opt["intraday"][0]["iv"] is None
    assert page.read_bytes() == (tmp_path / "docs/index.html").read_bytes()
    raw["records"][1]["data"][0]["expiry"] = day
    filename.write_bytes(gzip.compress(json.dumps(raw).encode()))
    with pytest.raises(ValueError, match="expiry"):
        publisher.build(tmp_path, day)
    assert page.read_text() == html


@pytest.mark.parametrize("leg", [float("inf"), True, float("nan"), -1, None])
def test_unusable_iv_cannot_become_a_published_average(leg):
    assert publisher.average([16, leg]) is None
