import importlib
import json
import asyncio
import httpx

import pandas as pd
import pytest
from volatility_lab.services import web_data


def test_candles_preserve_only_observed_volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(web_data, "DATA", tmp_path)
    path = tmp_path / "nifty_one_year.csv"
    frame = pd.DataFrame([["2026-11-11", 100, 103, 99, 102],
                          ["2026-11-12", 102, 105, 101, 104]],
                         columns=["Date", "Open", "High", "Low", "Close"])
    frame.to_csv(path, index=False)
    result = web_data.get_historical_candles()
    assert len(result["candles"]) == 2 and result["volumes"] == []
    frame["Volume"] = [0, 12]
    frame.iloc[::-1].to_csv(path, index=False)
    assert [r["value"] for r in web_data.get_historical_candles()["volumes"]] == [0, 12]


def test_missing_chain_observations_remain_null(tmp_path, monkeypatch):
    monkeypatch.setattr(web_data, "DATA", tmp_path)
    monkeypatch.setattr(web_data, "get_available_expiries", lambda _: [{"expiry": "2026-11-17", "dte": 3}])
    content = {"spot": 25001, "valuation": "2026-11-13T13:14:00+05:30",
               "broker_option_chain": [{"strike_price": 25000, "expiry": "2026-11-17",
                                       "call_options": {"option_greeks": {"delta": 0}}, "put_options": None}]}
    (tmp_path / "nifty_option_quotes_2026-11-17.json").write_text(json.dumps(content))
    result = web_data.get_option_chain_table("NIFTY")
    assert result["rows"][0]["call"]["delta"] == 0
    assert result["rows"][0]["call"]["iv"] is None
    assert result["rows"][0]["put"]["ltp"] is None
    assert result["rows"][0]["put"]["volume"] is None and result["total_call_oi"] is None
    assert result["pcr"] is None and result["atm_straddle"] is None and result["max_pain"] is None
    assert "error" in web_data.get_option_chain_table("NIFTY", "2026-11-23")
    json.dumps(result, allow_nan=False)


def test_api_session_status_and_zero_values(monkeypatch, tmp_path):
    from volatility_lab.services import expiry_data
    monkeypatch.setattr(web_data, "DATA", tmp_path)
    monkeypatch.setattr(expiry_data, "load_snapshot", lambda *a, **k: {
        "rows": [{"Instrument": "NIFTY", "Price": 25000, "% change": 0, "IV-RV Spread": 0}]})
    assert not web_data.get_market_summary(pd.Timestamp("2026-11-10T10:00+05:30"))["market_open"]
    special = web_data.get_market_summary(pd.Timestamp("2026-11-08T14:30+05:30"))
    assert special["market_open"] and "SPECIAL" in special["market_status"]
    assert special["nifty"]["change_pct"] == 0 and special["nifty"]["iv_rv_spread"] == 0
    assert special["india_vix"]["regime"] is None


@pytest.mark.parametrize('value', [None, -1, 1.5, 'unknown', float('nan'), float('inf')])
def test_missing_cloud_counts_remain_unknown(monkeypatch, tmp_path, value):
    from volatility_lab.services import expiry_data
    monkeypatch.setattr(web_data, 'DATA', tmp_path)
    monkeypatch.setattr(expiry_data, 'load_snapshot', lambda *a, **k: {})
    archive = tmp_path / 'google_archive'
    archive.mkdir()
    (archive / 'status.json').write_text(json.dumps({'total_files': value, 'today_count': 0}))
    summary = web_data.get_market_summary(pd.Timestamp('2026-11-13T10:00+05:30'))
    assert summary['cloud_sync']['total_files'] is None
    assert summary['cloud_sync']['today_count'] == 0
    (archive / 'status.json').write_text(json.dumps({'total_files': 0}))
    summary = web_data.get_market_summary(pd.Timestamp('2026-11-13T10:00+05:30'))
    assert summary['cloud_sync']['total_files'] == 0
    assert summary['cloud_sync']['today_count'] is None
    json.dumps(summary, allow_nan=False)


@pytest.mark.parametrize('key,value', [('Price', float('nan')), ('Price', -1), ('IV', float('inf')), ('IV', -1)])
def test_trade_radar_rejects_nonfinite_or_negative_inputs(monkeypatch, key, value):
    from volatility_lab.services import expiry_data
    row = {'Instrument': 'NIFTY', 'Price': 25000, 'IV': 15, 'Expiry': '2026-11-17',
           'As of': '2026-11-13T10:00:00+05:30', key: value}
    monkeypatch.setattr(expiry_data, 'load_snapshot', lambda *a, **k: {'rows': [row]})
    result = web_data.get_trade_radar('NIFTY')
    assert 'error' in result
    json.dumps(result, allow_nan=False)


def test_api_import_is_readonly_and_routes_validate(monkeypatch):
    from volatility_lab import PACKAGE_ROOT
    before = sorted(str(p.relative_to(PACKAGE_ROOT)) for p in PACKAGE_ROOT.rglob("*") if p.is_file() and "__pycache__" not in str(p))
    server = importlib.import_module("volatility_lab.ui.web_server")
    after = sorted(str(p.relative_to(PACKAGE_ROOT)) for p in PACKAGE_ROOT.rglob("*") if p.is_file() and "__pycache__" not in str(p))
    assert before == after
    async def get(path):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            return await client.get(path)
    for path in ["/api/candles?symbol=../../private", "/api/vol-surface?range=nan",
                 "/api/option-chain?expiry=../../file", "/api/vol-surface?mode=wrong"]:
        assert asyncio.run(get(path)).status_code == 422
    assert asyncio.run(get("/api/health")).status_code == 200
    monkeypatch.setattr(web_data, "get_historical_candles", lambda symbol: {"symbol": symbol, "candles": [], "volumes": []})
    assert asyncio.run(get("/api/candles")).json()["volumes"] == []
    def fail():
        raise OSError("private-token-for-test")
    monkeypatch.setattr(web_data, "get_market_summary", fail)
    response = asyncio.run(get("/api/market-summary"))
    assert response.status_code == 503 and "private-token-for-test" not in response.text
