import pytest

from volatility_lab.backtest import forward_test as engine


DAY = "2026-11-23"
EXPIRY = "2026-12-01"


def position(deadline, expiry=EXPIRY):
    stamp = DAY + "T09:25:10+05:30"
    return {
        "id": "OPEN-20261123-NIF-1201-S2", "date": DAY, "session_date": DAY, "triggerTime": stamp,
        "sym": "NIFTY", "exp": expiry, "strat": "S2", "holding": "intraday", "policy_version": "research-v1",
        "capital": 50000, "scheduled_exit_at": deadline, "provenance_status": "VALIDATED_EVENT_QUOTES",
        "legs": [{"type": kind, "strike": 25000, "side": 1, "mult": 1, "entryPx": 100, "entryQuoteAt": stamp}
                 for kind in ("CE", "PE")],
    }


def advance(state, day, clock, bid=None):
    stamp = f"{day}T{clock}+05:30"
    records = []
    if bid is not None:
        row = {"strike": 25000}
        for side in ("c", "p"):
            row.update({side + "Bid": bid, side + "Ask": bid+1, side + "BidQty": 65,
                        side + "AskQty": 65, side + "QuoteTimestamp": stamp})
        records = [{"label": "NIFTY|" + EXPIRY, "data": [row], "retrieved_at": stamp}]
    return engine.process_live_upstox_snapshot(
        {"status": "complete", "day": day, "retrieved_at": stamp, "nfo_session_validated": True, "records": records},
        ledger=state,
    )


@pytest.mark.parametrize("bid", [100, 140])
def test_first_executable_capture_after_missed_deadline_keeps_actual_exit(bid):
    due = DAY + "T15:15:00+05:30"
    initial = dict(engine.CLEAN_SLATE_LEDGER, open_positions=[position(due)])
    before = advance(initial, DAY, "15:14:44", 100)
    after = advance(before, DAY, "15:16:44", bid)
    assert not after["open_positions"]
    trade, = after["closed_trades"]
    assert "15:15 IST" in trade["exitReason"] and "delayed until fresh quote" in trade["exitReason"]
    assert trade["path_status"] == "INCOMPLETE_CAPTURE_PATH" and trade["unobserved_path_since"] == due
    assert trade["exit_at"] == DAY + "T15:16:44+05:30"
    assert all(leg["exitQuoteAt"] == trade["exit_at"] and leg["exitPx"] == bid for leg in trade["legs"])
    assert trade["gross"] == (bid - 100) * 130


def test_exit_during_scheduled_minute_uses_saved_session_deadline():
    due = DAY + "T12:45:00+05:30"
    initial = dict(engine.CLEAN_SLATE_LEDGER, open_positions=[position(due)])
    result = advance(initial, DAY, "12:45:58", 100)
    trade, = result["closed_trades"]
    assert "12:45 IST" in trade["exitReason"]
    assert "delayed" not in trade["exitReason"]
    assert trade["provenance_status"] == "VALIDATED_EVENT_QUOTES"


def test_no_chains_after_expiry_requires_settlement_reconciliation():
    due = DAY + "T15:15:00+05:30"
    initial = dict(engine.CLEAN_SLATE_LEDGER, open_positions=[position(due, expiry=DAY)])
    pending = advance(initial, DAY, "15:15:44")
    held, = pending["open_positions"]
    assert held["exit_pending"] and not held.get("manual_reconciliation_required")
    result = advance(pending, "2026-11-25", "09:15:45")
    held, = result["open_positions"]
    assert held["exit_pending"] and held["manual_reconciliation_required"]
    assert "settlement" in held["staleReason"]
    assert not result["closed_trades"] and result["intraday_mtm_series"][-1]["net_mtm"] is None
