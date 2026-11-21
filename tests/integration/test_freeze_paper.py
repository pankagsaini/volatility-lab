"""Event-time and snapshot reconciliation regressions for the research book."""

import copy
import gzip
import json
import pytest
from volatility_lab.backtest import forward_test as engine


def test_mirror_is_atomic_idempotent_and_rejects_missing_trades(tmp_path):
    source = tmp_path / "capture.json.gz"
    dest = tmp_path / "mirror"
    row = dict(
        id="FWD-20261112-NIF-1123-S1",
        date="2026-11-12",
        session_date="2026-11-12",
        triggerTime="2026-11-12T09:20:25+05:30",
        sym="NIFTY",
        exp="2026-11-23",
        strat="S1",
    )
    ledger = dict(copy.deepcopy(engine.CLEAN_SLATE_LEDGER), updated_at="2026-11-12T12:00:00+05:30", closed_trades=[row])

    def write(state):
        source.write_bytes(
            gzip.compress(
                json.dumps(
                    dict(provider="Upstox", status="complete", nfo_session_validated=True, forward_test_ledger=state)
                ).encode()
            )
        )

    write(ledger)
    path, changed = engine.sync_forward_ledger(source, dest)
    assert changed
    before = path.read_bytes()
    assert engine.sync_forward_ledger(source, dest) == (path, False)
    for invalid in [
        dict(ledger, updated_at="2026-11-12T11:59:59+05:30"),
        dict(ledger, updated_at="2026-11-12T12:01:01+05:30", closed_trades=[]),
        dict(ledger, updated_at="2026-11-12T12:01:01+05:30", closed_trades=[dict(row, net=999)]),
    ]:
        write(invalid)
        with pytest.raises(ValueError):
            engine.sync_forward_ledger(source, dest)
        assert path.read_bytes() == before


def test_exact_event_time_and_prior_close():
    stamp = "2026-11-20T09:20:02+05:30"
    assert not engine._quote_timestamp_is_fresh("2026-11-20T09:20:03+05:30", stamp)
    assert engine._quote_timestamp_is_fresh("2026-11-20T09:20:01+05:30", stamp)
    assert engine._risk_vol_metrics(
        {"risk_metrics": {"NIFTY": {"as_of": "2026-11-20", "hv10": 12, "hv30": 13}}}, "NIFTY", "2026-11-20"
    ) == (None, None)
    assert engine.scheduled_exit_at("2026-11-18", "2026-11-23", "positional").isoformat() == "2026-11-20T15:15:00+05:30"


@pytest.mark.parametrize("exit_bid,exit_ask,reason", [(30, 31, "Stop-Loss"), (140, 141, "Target Hit")])
def test_versioned_stop_and_target_use_executable_exit(exit_bid, exit_ask, reason):
    day = "2026-11-19"
    stamp = day + "T10:00:01+05:30"
    leg = lambda kind: dict(strike=25000, type=kind, mult=1, side=1, entryPx=100)
    position = dict(
        id="OPEN-20261119-NIF-1123-S2",
        date=day,
        session_date=day,
        strat="S2",
        sym="NIFTY",
        exp="2026-11-23",
        triggerTime=day + "T09:25:10+05:30",
        holding="intraday",
        policy_version="research-v1",
        capital=50000,
        legs=[leg("CE"), leg("PE")],
        provenance_status="VALIDATED_EVENT_QUOTES",
    )
    book = {"strike": 25000}
    for cp in ("c", "p"):
        book.update(
            {
                cp + "Bid": exit_bid,
                cp + "Ask": exit_ask,
                cp + "BidQty": 65,
                cp + "AskQty": 65,
                cp + "QuoteTimestamp": stamp,
            }
        )

    def mark(row):
        return engine.mark_open_positions_to_market(
            [position],
            {"NIFTY|2026-11-23": [row]},
            current_time_ist="10:00 IST",
            live_chain_timestamps={"NIFTY|2026-11-23": stamp},
            reference_timestamp=stamp,
        )

    stale, flat = mark(dict(book, pBidQty=64))
    assert not flat and stale[0]["freshness"] == "STALE"
    pending, closed = mark(book)
    assert not pending and closed[0]["exitReason"].startswith(reason)
    assert closed[0]["gross"] == (exit_bid - 100) * 130
    assert all(leg["exitPx"] == exit_bid for leg in closed[0]["legs"])
    assert closed[0]["exit_at"] == stamp and closed[0]["mtmPath"][-1]["at"] == stamp


def test_worker_advances_canonical_ledger_once_and_preserves_capture_gaps(tmp_path, monkeypatch):
    from datetime import datetime
    from volatility_lab.backtest import paper_worker as worker
    from test_execution_safety_fixes import TestExecutionSafetyFixes

    class SessionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 21, 12, tzinfo=tz)

    monkeypatch.setattr(worker, "datetime", SessionClock)
    archive = tmp_path / "raw"
    archive.mkdir()
    directory = tmp_path / "worker"
    day = "2026-11-12"
    exp = "2026-11-17"

    def write(hm):
        stamp = day + "T" + hm + ":20+05:30"
        chain = [dict(TestExecutionSafetyFixes()._priced_row(k, stamp), expiry=exp) for k in range(24000, 26100, 50)]
        for row in chain:
            for key in ("call_options", "put_options"):
                book = row[key]["market_data"]
                intrinsic = max(0, (25050 - row["strike_price"]) * (1 if key == "call_options" else -1))
                for price in ("bid_price", "ask_price"):
                    book[price] = intrinsic + (book[price] - intrinsic) * 0.6
        data = dict(
            schema=1,
            provider="Upstox",
            mode="snapshot",
            status="complete",
            day=day,
            retrieved_at=stamp,
            nfo_session_validated=True,
            risk_metrics={"NIFTY": {"as_of": "2026-11-11", "hv10": 14, "hv30": 15}},
            catalogue={"indices": {"NIFTY": {"option_expiries": [exp], "futures": []}}},
            records=[
                dict(label="quotes", data={}, retrieved_at=stamp),
                dict(label="NIFTY|" + exp, data=chain, retrieved_at=stamp),
            ],
        )
        path = archive / (day + "_snapshot_" + hm.replace(":", "") + "20_complete.json.gz")
        path.write_bytes(gzip.compress(json.dumps(data).encode()))
        return path

    entry = write("09:20")
    with pytest.raises(ValueError, match="backdate"):
        worker.run_once(archive, directory, day)
    s = worker.run_once(archive, directory, day, replay=True)
    assert {p["strat"] for p in s["open_positions"]} == {"S1", "S5"}
    assert s["open_positions"][0]["entry_capture"] == entry.name
    before = (directory / engine.LEDGER_FILENAME).read_bytes()
    assert worker.run_once(archive, directory, day, replay=True)["worker_checkpoint"]["completed"] == 1
    assert (directory / engine.LEDGER_FILENAME).read_bytes() == before
    write("15:15")
    eod = worker.run_once(archive, directory, day, replay=True)
    assert {p["strat"] for p in eod["closed_trades"]} == {"S5"}
    assert {p["strat"] for p in eod["open_positions"]} == {"S1"}
    assert eod["closed_trades"][0]["provenance_status"] == "GAP_AFFECTED"
    assert eod["closed_trades"][0]["entry_capture"] == entry.name
    assert eod["closed_trades"][0]["exit_capture"].endswith("151520_complete.json.gz")
    with pytest.raises(ValueError, match="cannot publish"):
        worker.run_once(archive, directory, day, tmp_path / "public.json", replay=True)
    with pytest.raises(ValueError, match="another ledger"):
        worker.run_once(archive, directory, day)


def test_public_worker_export_is_allowlisted():
    from volatility_lab.backtest import paper_worker as worker

    state = worker.initial_state("2026-11-23", replay=True)
    state["raw_path"] = "private"
    state["worker_checkpoint"]["last_day"] = "2026-11-23"
    state["open_positions"] = [
        dict(
            id="paper",
            mtmPath=[{"raw": "private"}],
            entry_capture="private",
            legs=[{"strike": 100, "instrument_token": "private"}],
        )
    ]
    state["intraday_mtm_series"] = [
        dict(session_date="2026-11-20", net_mtm=11),
        dict(session_date="2026-11-23", net_mtm=33, raw="private"),
    ]
    result = worker.public_state(state)
    assert "private" not in json.dumps(result)
    assert "worker_checkpoint" not in result
    assert result["open_positions"] == [dict(id="paper", legs=[dict(strike=100)])]
    assert result["intraday_mtm_series"] == [dict(session_date="2026-11-23", net_mtm=33)]


def test_publication_retries_without_changing_published_closes(tmp_path, monkeypatch):
    import base64
    from scripts import run_paper_service as service

    old = dict(source_authority="python_paper_worker", cohort_kind="prospective", prospective_start="2026-11-23",
               last_capture_at=None, closed_trades=[])
    trade = {"id": "FWD-A", "net": 25}
    new = dict(old, last_capture_at="2026-11-23T10:25:00+05:30", closed_trades=[trade])
    receipt, artifact = tmp_path / "published.json", tmp_path / "paper-ledger.json"
    artifact.write_text(json.dumps(new))
    requests = []

    def api(_, *args, body=None):
        requests.append(args)
        if body:
            assert json.loads(body)["branch"] == "paper-feed"
            if len(requests) == 2:
                raise RuntimeError("Unavailable")
            return {}
        return {"sha": "123", "content": base64.b64encode(json.dumps(old).encode()).decode()}

    monkeypatch.setattr(service, "github", api)
    with pytest.raises(RuntimeError, match="Unavailable"):
        service.publish("gh", artifact, receipt)
    assert not receipt.exists()
    assert service.publish("gh", artifact, receipt)
    assert not service.publish("gh", artifact, receipt) and len(requests) == 4
    old.update(new)
    artifact.write_text(json.dumps(dict(new, closed_trades=[dict(trade, net=999)])))
    with pytest.raises(ValueError, match="published closes"):
        service.publish("gh", artifact, receipt)
