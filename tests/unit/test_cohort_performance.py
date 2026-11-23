import json
from pathlib import Path

import pytest

from volatility_lab.backtest.cohort_performance import format_outcomes, prospective_outcomes
from volatility_lab.backtest import forward_test as engine


def book(*trades):
    return {"source_authority": "python_paper_worker", "cohort_kind": "prospective", "policy_version": "research-v1",
            "prospective_start": "2026-11-23", "closed_trades": list(trades), "open_positions": [{"grossMtm": 99999}]}


def close(key, day="2026-11-23", gross=150, fees=50, **changes):
    entry = f"{day}T09:25:00+05:30"
    exit_at = f"{day}T15:15:00+05:30"
    return {"id": key, "sym": "NIFTY", "holding": "intraday", "entry_vrp_pp": -1.8,
            "policy_version": "research-v1", "session_date": day, "exit_session_date": day,
            "triggerTime": entry, "exit_at": exit_at, "gross": gross, "charges": fees, "net": gross - fees,
            "integrity_status": "VALIDATED_FRESH_EXECUTABLE_MARK", "provenance_status": "VALIDATED_EVENT_QUOTES",
            "legs": [{"entryPx": 80, "entryQuoteAt": entry, "exitPx": 81, "exitQuoteAt": exit_at}], **changes}


def test_inception_has_no_performance_estimate():
    original = json.loads((Path(__file__).resolve().parents[2] / "paper-ledger.json").read_text())
    # Keep valid if the public file bundled alongside the site advances beyond inception.
    report = prospective_outcomes(dict(original, closed_trades=[], open_positions=[]))
    assert report["totals"]["closes"] == report["totals"]["exit_sessions"] == 0
    assert report["totals"]["net_inr"] is None
    assert report["by_exit_session"] == {}
    assert "sampling uncertainty are unavailable" in format_outcomes(report)
    with pytest.raises(ValueError, match="Prospective"):
        prospective_outcomes(dict(book(), cohort_kind="historical_replay"))


def test_aggregate_costs_and_cluster_by_actual_exit_session():
    report = prospective_outcomes(book(
        close("A"),
        close("B", gross=150, fees=50, sym="BANKNIFTY", holding="positional", entry_vrp_pp=2.2),
        close("C", "2026-11-25", gross=-50, fees=50, entry_vrp_pp=None),
    ))
    total = report["totals"]
    assert (total["closes"], total["exit_sessions"]) == (3, 2)
    assert (total["gross_inr"], total["fees_inr"], total["net_inr"]) == (250, 150, 100)
    assert total["mean_net_per_close_inr"] == 33.33
    # Active exit-day totals are +200 and -100. The open position makes no contribution.
    assert (total["mean_net_per_exit_session_inr"], total["exit_session_mean_se_inr"]) == (50, 150)
    assert report["by_exit_session"]["2026-11-23"]["exit_session_mean_se_inr"] is None
    assert report["by_underlying"]["NIFTY"]["net_inr"] == 0
    assert report["by_holding"]["positional"]["closes"] == 1
    assert set(report["by_entry_vrp"]) == {"IV above HV30", "IV below HV30", "unavailable"}
    assert "3 eligible closes across 2 exit sessions" in format_outcomes(report)


def test_preserve_gap_economics_separately_and_expose_bad_evidence():
    report = prospective_outcomes(book(
        close("P", "2026-11-20"), close("L", policy_version=None), close("A"), close("A"),
        close("G", gross=-250, provenance_status="GAP_AFFECTED"), close("K", net=900),
        close("Q", legs=[]), close("Z", gross=50, fees=50, entry_vrp_pp=0.0),
    ))
    assert report["totals"]["closes"] == 1
    assert report["totals"]["net_inr"] == 0
    assert report["by_entry_vrp"]["IV equals HV30"]["closes"] == 1
    assert report["gap_affected"]["net_inr"] == -300
    assert report["exclusions"] == {"outside_cohort_or_undated": 2, "missing_or_duplicate_id": 2, "capture_gap": 1,
                                    "unreconciled_amounts": 1, "unverified_execution": 1}
    summary = format_outcomes(report)
    assert "Excluded · incomplete observation path" in summary and "-300.00" in summary


@pytest.mark.parametrize(("field", "observed", "eligible"), [
    ("entryQuoteAt", "2026-11-23T09:23:00+05:30", True),
    ("entryQuoteAt", "2026-11-23T09:22:59+05:30", False),
    ("exitQuoteAt", "2026-11-23T15:15:01+05:30", False),
])
def test_quote_age_is_checked_at_paper_entry_and_exit(field, observed, eligible):
    row = close("A", gross=6344, fees=152.66, net=6191.34)
    row["legs"][0][field] = observed
    report = prospective_outcomes(book(row))
    assert report["totals"]["net_inr"] == (6191.34 if eligible else None)


def test_subpaise_and_nonfinite_amounts_are_excluded():
    report = prospective_outcomes(book(close("A", gross=100.004, net=50.004), close("B", net=float("inf"))))
    assert report["totals"]["net_inr"] is None
    assert report["exclusions"] == {"unreconciled_amounts": 2}


@pytest.mark.parametrize("gap", ["no_chain", "thin_bid", "none"])
def test_missing_liquidation_book_survives_recovery_at_a_later_executable_exit(gap):
    day = "2026-11-23"
    entered = day + "T09:25:00+05:30"
    expiry = "2026-12-01"
    position = {
        "id": "OPEN-20261123-NIF-1201-S2", "date": day, "session_date": day, "triggerTime": entered,
        "strat": "S2", "sym": "NIFTY", "exp": expiry, "holding": "intraday", "policy_version": "research-v1",
        "capital": 50000, "scheduled_exit_at": day + "T15:15:00+05:30",
        "provenance_status": "VALIDATED_EVENT_QUOTES",
        "legs": [{"strike": 25000, "type": cp, "mult": 1, "side": 1, "entryPx": 100, "entryQuoteAt": entered}
                 for cp in ["CE", "PE"]],
    }
    def advance(ledger, stamp, bid, depth, missing=False):
        row = {"strike": 25000}
        for cp in ("c", "p"):
            row.update({cp + "Bid": bid, cp + "Ask": bid + 1, cp + "BidQty": depth, cp + "AskQty": 65,
                        cp + "QuoteTimestamp": stamp})
        records = [] if missing else [{"label": "NIFTY|" + expiry, "data": [row], "retrieved_at": stamp}]
        capture = {"day": day, "status": "complete", "retrieved_at": stamp, "records": records,
                   "nfo_session_validated": True}
        return engine.process_live_upstox_snapshot(capture, ledger=ledger)

    initial = dict(engine.CLEAN_SLATE_LEDGER, open_positions=[position])
    entered_state = advance(initial, entered, 99, 65)
    assert entered_state["open_positions"][0]["provenance_status"] == "VALIDATED_EVENT_QUOTES"
    missed = day + "T09:26:00+05:30"
    held = advance(entered_state, missed, 99, 64 if gap == "thin_bid" else 65, missing=(gap == "no_chain"))
    exited = advance(held, day + "T09:27:00+05:30", 140, 65)
    assert len(exited["closed_trades"]) == 1
    closed = exited["closed_trades"][0]
    assert closed["gross"] == 5200 and closed["exitReason"].startswith("Target Hit")
    outcome = prospective_outcomes(book(closed))
    if gap == "none":
        assert outcome["totals"]["closes"] == 1
        assert "path_status" not in closed
    else:
        assert closed["unobserved_path_since"] == missed
        assert closed["path_status"] == "INCOMPLETE_QUOTE_PATH"
        assert closed["provenance_status"] == "GAP_AFFECTED"
        assert outcome["exclusions"] == {"quote_gap": 1}
        assert outcome["gap_affected"]["net_inr"] == closed["net"]
