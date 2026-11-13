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
