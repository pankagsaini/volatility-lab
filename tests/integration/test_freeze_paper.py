"""Event-time and snapshot reconciliation regressions for the research book."""
import copy
import gzip
import json
import pytest
from volatility_lab.backtest import forward_test as engine


def test_mirror_is_atomic_idempotent_and_rejects_missing_trades(tmp_path):
    source = tmp_path / "capture.json.gz"
    dest = tmp_path / "mirror"
    row = dict(id="FWD-20261112-NIF-1123-S1", date="2026-11-12", session_date="2026-11-12",
               triggerTime="2026-11-12T09:20:25+05:30", sym="NIFTY", exp="2026-11-23", strat="S1")
    ledger = dict(copy.deepcopy(engine.CLEAN_SLATE_LEDGER), updated_at="2026-11-12T12:00:00+05:30", closed_trades=[row])

    def write(state):
        source.write_bytes(gzip.compress(json.dumps(dict(provider="Upstox", status="complete",
            nfo_session_validated=True, forward_test_ledger=state)).encode()))

    write(ledger)
    path, changed = engine.sync_forward_ledger(source, dest)
    assert changed
    before = path.read_bytes()
    assert engine.sync_forward_ledger(source, dest) == (path, False)
    for invalid in [dict(ledger, updated_at="2026-11-12T11:59:59+05:30"),
                    dict(ledger, updated_at="2026-11-12T12:01:01+05:30", closed_trades=[]),
                    dict(ledger, updated_at="2026-11-12T12:01:01+05:30", closed_trades=[dict(row, net=999)])]:
        write(invalid)
        with pytest.raises(ValueError):
            engine.sync_forward_ledger(source, dest)
        assert path.read_bytes() == before


def test_exact_event_time_and_prior_close():
    stamp = "2026-11-20T09:20:02+05:30"
    assert not engine._quote_timestamp_is_fresh("2026-11-20T09:20:03+05:30", stamp)
    assert engine._quote_timestamp_is_fresh("2026-11-20T09:20:01+05:30", stamp)
    assert engine._risk_vol_metrics({"risk_metrics":{"NIFTY":{"as_of":"2026-11-20","hv10":12,"hv30":13}}},
                                    "NIFTY","2026-11-20") == (None,None)
    assert engine.scheduled_exit_at("2026-11-18","2026-11-23","positional").isoformat() == "2026-11-20T15:15:00+05:30"


@pytest.mark.parametrize("exit_bid,exit_ask,reason", [(30,31,"Stop-Loss"),(140,141,"Target Hit")])
def test_versioned_stop_and_target_use_executable_exit(exit_bid, exit_ask, reason):
    day = "2026-11-19"
    stamp = day+"T10:00:01+05:30"
    leg = lambda kind: dict(strike=25000,type=kind,mult=1,side=1,entryPx=100)
    position = dict(id="OPEN-20261119-NIF-1123-S2", date=day,session_date=day,strat="S2",sym="NIFTY",
                    exp="2026-11-23", triggerTime=day+"T09:25:10+05:30",holding="intraday",policy_version="research-v1",
                    capital=50000,legs=[leg("CE"),leg("PE")],provenance_status="VALIDATED_EVENT_QUOTES")
    book = {"strike":25000}
    for cp in ("c","p"):
        book.update({cp+"Bid":exit_bid,cp+"Ask":exit_ask,cp+"BidQty":65,cp+"AskQty":65,cp+"QuoteTimestamp":stamp})
    def mark(row):
        return engine.mark_open_positions_to_market([position],{"NIFTY|2026-11-23":[row]},current_time_ist="10:00 IST",
            live_chain_timestamps={"NIFTY|2026-11-23":stamp},reference_timestamp=stamp)
    stale,flat=mark(dict(book,pBidQty=64))
    assert not flat and stale[0]["freshness"]=="STALE"
    pending,closed=mark(book)
    assert not pending and closed[0]["exitReason"].startswith(reason)
    assert closed[0]["gross"]==(exit_bid-100)*130
    assert all(leg["exitPx"]==exit_bid for leg in closed[0]["legs"])
    assert closed[0]["exit_at"]==stamp and closed[0]["mtmPath"][-1]["at"]==stamp
