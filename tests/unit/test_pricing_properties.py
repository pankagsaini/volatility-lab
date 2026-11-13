import math
from hypothesis import given, settings, strategies as st
import numpy as np

from volatility_lab.pricing.option_math import black76_greeks
from volatility_lab.pricing.vol_surface_3d import load_expiry_chains, extract_surface_mesh


@settings(max_examples=120, deadline=None, derandomize=True)
@given(
    forward=st.floats(10, 100_000),
    ratio=st.floats(0.6, 1.4),
    years=st.floats(1 / 365, 2),
    rate=st.floats(0, 0.15),
    sigma=st.floats(0.02, 1),
)
def test_black76_parity_bounds_and_monotonicity(forward, ratio, years, rate, sigma):
    strike = ratio * forward
    discount = math.exp(-rate * years)
    call = black76_greeks(forward, strike, years, rate, sigma, "CE")["price"]
    put = black76_greeks(forward, strike, years, rate, sigma, "PE")["price"]
    higher = black76_greeks(forward, strike, years, rate, sigma + 0.05, "CE")["price"]
    assert math.isclose(call - put, discount * (forward - strike), abs_tol=1e-8, rel_tol=1e-10)
    assert -1e-8 <= call <= discount * forward + 1e-8
    assert higher >= call - 1e-8


def test_archived_surface_requires_greeks_and_capture_time(tmp_path):
    from datetime import date
    import json

    expiry = "2026-11-17"
    path = tmp_path / f"nifty_option_quotes_{expiry}.json"
    rows = [
        {
            "expiry": expiry,
            "strike_price": strike,
            "call_options": {"option_greeks": {"iv": iv}, "market_data": {"ltp": 80}},
            "put_options": {"option_greeks": {"iv": iv}, "market_data": {"ltp": 75}},
        }
        for strike, iv in [(990, 19), (1000, 20), (1010, 21), (1020, "invalid")]
    ]
    obj = {"provider": "Upstox", "spot": 1000, "valuation": "2026-11-13T12:00:00+05:30", "broker_option_chain": rows}
    path.write_text(json.dumps(obj))
    chains = load_expiry_chains("NIFTY", tmp_path, today=date(2026, 11, 13))
    x, _, z, _, info = extract_surface_mesh(chains, n_strikes=17)
    assert info["pricing_model"] == "Captured broker IV"
    assert z[0, np.where(x == 100)[0][0]] == 20
    assert np.isnan(z[0, 0]) and np.isnan(z[0, -1])
    for row in rows:
        row["put_options"].pop("option_greeks")
        row["call_options"].pop("option_greeks")
    path.write_text(json.dumps(obj))
    assert extract_surface_mesh(load_expiry_chains("NIFTY", tmp_path, today=date(2026, 11, 13)))[2].size == 0
    obj["valuation"] = "undated"
    path.write_text(json.dumps(obj))
    assert load_expiry_chains("NIFTY", tmp_path, today=date(2026, 11, 13)) == []

def test_surface_payload_uses_same_broker_quotes_for_each_chart(monkeypatch):
    from volatility_lab.pricing import vol_surface_3d
    from volatility_lab.services.web_data import get_vol_surface_payload

    def chain(expiry, dte, spot, base):
        return {
            "expiry": expiry, "dte": dte, "spot": spot, "as_of": "2026-11-13T12:00:00+05:30",
            "chain": [
                {"strike_price": strike, "call_options": {"option_greeks": {"iv": base + i}},
                 "put_options": {"option_greeks": {"iv": base + i + 10}}}
                for i, strike in enumerate(range(spot - 3, spot + 5, 2))
            ],
        }

    front = chain("2026-11-17", 4, 100, 20)
    back = chain("2026-11-24", 11, 110, 25)
    monkeypatch.setattr(vol_surface_3d, "load_expiry_chains", lambda *_: [front, back])
    for mode, expected in [("call", 21), ("put", 31), ("otm", 31), ("mid", 26)]:
        payload = get_vol_surface_payload("NIFTY", mode)
        assert payload["as_of_by_expiry"][back["expiry"]] == back["as_of"]
        assert payload["smile_curves"][0]["ivs"][1] == expected
        assert payload["atm_ridge"]["x"] == [99, 109]
        assert payload["term_structure"]["ivs"] == [expected, expected + 5]
        assert payload["diagnostics"]["front_atm_iv"] == expected
        assert payload["diagnostics"]["term_slope"] == 5
        assert payload["diagnostics"]["front_skew"] is None
    for invalid in [0, 121, None]:
        back["chain"][1]["put_options"]["option_greeks"]["iv"] = invalid
        payload = get_vol_surface_payload("NIFTY", "mid")
        assert 109 not in payload["smile_curves"][1]["strikes"]
        assert payload["term_structure"]["ivs"] == [26]
        assert payload["diagnostics"]["back_atm_iv"] is None
        assert payload["term_structure"]["regime"] == "Unavailable"
    back["chain"].clear()
    later = chain("2026-12-01", 18, 112, 40)
    monkeypatch.setattr(vol_surface_3d, "load_expiry_chains", lambda *_: [front, back, later])
    payload = get_vol_surface_payload("NIFTY", "call")
    assert payload["diagnostics"]["term_slope"] is None
