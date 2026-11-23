import pytest

from volatility_lab.backtest.execution_evidence import ExecutionEvidenceError, verify_execution_evidence
from volatility_lab.backtest.forward_test import compute_position_charges


STAMP = "2026-11-19T09:25:44+05:30"
CHAIN = "2026-11-19T09:25:42+05:30"
QUOTE = "2026-11-19T09:25:41+05:30"
EXPIRY = "2026-11-23"


def execution(endpoint):
    row = {"expiry": EXPIRY, "strike_price": 25000}
    legs = []
    for kind, side, inside in (("CE", 1, (100, 101)), ("PE", -1, (89, 90))):
        bid, ask = inside
        buy = (side > 0) == (endpoint == "entry")
        row["call_options" if kind == "CE" else "put_options"] = {"market_data": {
            "ltp": 5, "bid_price": bid, "ask_price": ask, "bid_qty": 260 if not buy else 1,
            "ask_qty": 260 if buy else 1, "quote_timestamp": QUOTE,
        }}
        legs.append({"type": kind, "side": side, "strike": 25000, "mult": 2,
                     endpoint + "Px": ask if buy else bid, endpoint + "QuoteAt": QUOTE})
    chain = {"label": "NIFTY|" + EXPIRY, "retrieved_at": CHAIN, "data": [row]}
    trade = {"sym": "NIFTY", "exp": EXPIRY, "triggerTime": STAMP, "exit_at": STAMP, "lots": 2, "legs": legs}
    if endpoint == "exit":
        trade.update(triggerTime=STAMP.replace("-19T", "-18T"), session_date="2026-11-18", exit_session_date="2026-11-19")
        for leg in legs:
            leg["entryPx"] = 95
        # Call bid 100 and put ask 90, two lots with a 2x multiplier on each.
        fees = compute_position_charges(trade["session_date"], "NIFTY", legs, lots=2, exit_day=trade["exit_session_date"])
        trade.update(gross=2600.0, charges=fees, net=round(2600.0 - fees, 2))
    return {"records": [chain], "retrieved_at": STAMP}, trade


@pytest.mark.parametrize("endpoint", ["entry", "exit"])
def test_all_sides_at_exact_full_size(endpoint):
    report, trade = execution(endpoint)
    verify_execution_evidence(report, trade, endpoint)
    quote = report["records"][0]["data"][0]["call_options"]["market_data"]
    quote["ask_qty" if endpoint == "entry" else "bid_qty"] = 259
    with pytest.raises(ExecutionEvidenceError, match="quantity"):
        verify_execution_evidence(report, trade, endpoint)


@pytest.mark.parametrize("field", ["gross", "charges", "net", "exit_session_date"])
def test_close_preserves_signed_gross_and_both_dated_sides_of_fees(field):
    report, trade = execution("exit")
    trade[field] = "2026-11-18" if field == "exit_session_date" else trade[field] + 0.01
    with pytest.raises(ExecutionEvidenceError):
        verify_execution_evidence(report, trade, "exit")


@pytest.mark.parametrize("alter", ["ltp_price", "crossed", "missing_side", "wrong_time", "stale_chain",
                                   "future_quote", "wrong_expiry", "wrong_strike", "duplicate_strike"])
def test_false_execution_blocks_publication(alter):
    report, trade = execution("entry")
    chain = report["records"][0]
    row = chain["data"][0]
    book = row["call_options"]["market_data"]
    if alter == "ltp_price":
        trade["legs"][0]["entryPx"] = book["ltp"]
    elif alter == "crossed":
        book["bid_price"] = 102
    elif alter == "missing_side":
        del row["put_options"]
    elif alter == "wrong_time":
        trade["legs"][0]["entryQuoteAt"] = CHAIN
    elif alter == "stale_chain":
        chain["retrieved_at"] = STAMP.replace("09:25", "09:22")
    elif alter == "future_quote":
        book["quote_timestamp"] = trade["legs"][0]["entryQuoteAt"] = STAMP
    elif alter == "wrong_expiry":
        row["expiry"] = "2026-12-01"
    elif alter == "wrong_strike":
        trade["legs"][1]["strike"] += 50
    elif alter == "duplicate_strike":
        chain["data"].append(dict(row))
    with pytest.raises(ExecutionEvidenceError):
        verify_execution_evidence(report, trade, "entry")
