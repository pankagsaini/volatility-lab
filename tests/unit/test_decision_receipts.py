import pytest

from volatility_lab.backtest import forward_test as engine


def capture(hm="09:25", expiry="2026-11-23", iv=12.0, depth=65):
    day = "2026-11-19"
    stamp = f"{day}T{hm}:01+05:30"
    row = {"underlying_spot_price": 25000, "strike": 25000}
    for side in ("c", "p"):
        row.update({side + "Bid": 100, side + "Ask": 101, side + "BidQty": depth, side + "AskQty": depth,
                    side + "Iv": iv, side + "Delta": 0.5 if side == "c" else -0.5, side + "QuoteTimestamp": stamp})
    return {
        "day": day, "status": "complete", "retrieved_at": stamp, "nfo_session_validated": True,
        "risk_metrics": {"NIFTY": {"as_of": "2026-11-18", "hv10": 15, "hv30": 15}},
        "catalogue": {"indices": {"NIFTY": {"option_expiries": [expiry]}, "BANKNIFTY": {"option_expiries": [expiry]}}},
        "records": [{"label": "NIFTY|" + expiry, "data": [row], "retrieved_at": stamp}],
    }


def trace(report, ledger=None):
    book = engine.CLEAN_SLATE_LEDGER if ledger is None else ledger
    rows = []
    result = engine.process_live_upstox_snapshot(report, ledger=book, decision_receipts=rows)
    assert result == engine.process_live_upstox_snapshot(report, ledger=book)
    return result, rows


@pytest.mark.parametrize(("changes", "outcome", "reason"), [
    ({}, "entered", "position_opened"),
    ({"expiry": "2026-11-20"}, "expiry_ineligible", "dte_outside_range"),
    ({"iv": 16}, "rule_rejected", "vrp_outside_range"),
    ({"depth": 64}, "inputs_unavailable", "entry_book_unavailable"),
])
def test_receipts_distinguish_the_production_entry_gates(changes, outcome, reason):
    book, rows = trace(capture(**changes))
    assert [(r["symbol"], r["strategy"]) for r in rows] == [("NIFTY", "S2"), ("BANKNIFTY", "S2")]
    assert (rows[0]["outcome"], rows[0]["reason"]) == (outcome, reason)
    assert rows[1]["reason"] == "front_chain_unavailable"
    assert bool(book["open_positions"]) == (outcome == "entered")


def test_missing_wings_prior_risk_and_stale_chains_have_explicit_receipts():
    burst = capture(hm="09:20")
    _, rows = trace(burst)
    assert {r["strategy"] for r in rows} == {"S1", "S4", "S5"} and len(rows) == 6
    assert next(r for r in rows if (r["symbol"], r["strategy"]) == ("NIFTY", "S4"))["reason"] == "rr25_unavailable"
    burst["risk_metrics"] = {}
    _, rows = trace(burst)
    assert all(r["outcome"] == "inputs_unavailable" for r in rows)
    burst["records"][0]["retrieved_at"] = "2026-11-19T09:19:55+05:30"
    _, rows = trace(burst)
    assert all(r["reason"] == "chain_outside_decision_minute" for r in rows if r["symbol"] == "NIFTY")
    burst["records"] = []
    _, rows = trace(burst)
    assert all(r["reason"] == "front_chain_unavailable" for r in rows)


def test_duplicate_or_blocked_report_and_off_minute_never_claim_a_fresh_decision():
    report = capture()
    opened, _ = trace(report)
    assert trace(report, opened)[1] == []
    report["retrieved_at"] = "2026-11-19T09:25:18+05:30"
    _, repeated = trace(report, opened)
    assert repeated[0]["outcome"] == "position_exists"
    assert trace(dict(report, nfo_session_validated=False))[1] == []
    assert trace(capture(hm="09:26"))[1] == []
