"""Regression cases for observed archive, quote, ledger and fee defects."""
import copy
from contextlib import closing
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "volatility_lab"))
import backtest_engine
import backtest_signals
import broker_charges
import forward_test
import futures_data
import expiry_data
import quote_quality
import quote_status
import refresh_policy


class TestCollectorAuditFixes(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows scheduled collector')
    def test_daily_capture_uses_sourced_special_session_and_expected_day(self):
        from volatility_lab.storage import daily_capture
        cases = {
            '2026-11-08T14:00:00+05:30': None,
            '2026-11-08T14:30:00+05:30': 'history_catchup',
            '2026-11-08T15:35:00+05:30': 'closing_quotes',
            '2026-11-08T16:30:00+05:30': 'eod',
            '2025-10-21T14:40:00+05:30': 'closing_quotes',
            '2026-02-01T10:00:00+05:30': 'history_catchup',
            '2026-11-10T14:30:00+05:30': None,
            '2026-11-14T14:30:00+05:30': None,
        }
        for stamp, expected in cases.items():
            with self.subTest(stamp=stamp):
                self.assertEqual(daily_capture.capture_stage(pd.Timestamp(stamp)), expected)
        stamp = pd.Timestamp('2026-11-08T16:30:00+05:30')
        self.assertEqual(daily_capture.expected_day(stamp), stamp.date())
        with patch.object(daily_capture, 'read_json', return_value={'completed': {'2026-11-08_eod': stamp.isoformat()}}):
            self.assertEqual(daily_capture._run(stamp), 0)

    def test_overwritten_archive_invalidates_cached_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.gz"
            for payload in ({"value": 1}, {"value": "replacement"}):
                with gzip.open(path, "wt", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                self.assertEqual(backtest_engine._read_gzip_json(path), payload)

    def test_snapshot_marks_keep_real_late_session_times_and_no_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "google_archive"
            archive.mkdir()
            for index, minute in enumerate(("15:35", "15:39")):
                data = {"retrieved_at": f"2026-11-13T{minute}:00+05:30", "records": [{
                    "label": "NIFTY|2026-11-17", "data": [{
                        "strike_price": 25000, "underlying_spot_price": 25000,
                        "call_options": {"market_data": {"ltp": 100}},
                        "put_options": {"market_data": {"ltp": 95}},
                    }]}]}
                with gzip.open(archive / f"2026-11-13_snapshot_{index}_complete.json.gz", "wt") as stream:
                    json.dump(data, stream)
            with patch.object(backtest_engine, "DATA_DIR", Path(tmp)):
                _, series, _ = backtest_engine._fallback_from_monitor_sqlite("NIFTY", "2026-11-13", "2026-11-13")
            self.assertEqual(set(series["NSE_INDEX|Nifty 50"]), {"2026-11-13 15:35", "2026-11-13 15:39"})
            self.assertTrue(all(bar[4] == 0 for bars in series.values() for bar in bars.values()))

    def test_future_closing_quote_and_nonfinite_spread_limit_rejected(self):
        quote = {"timestamp": "2026-11-13T16:00:00+05:30",
                 "depth": {"buy": [{"price": 99, "quantity": 100}],
                           "sell": [{"price": 101, "quantity": 100}]}}
        result = quote_quality.assess_quote(quote, "2026-11-13T15:40:00+05:30", 3, closing=True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "stale_or_future_quote")
        quote["timestamp"] = "2026-11-13T15:40:00+05:30"
        self.assertEqual(quote_quality.assess_quote(quote, quote["timestamp"], float("nan"))["reason"], "invalid_spread_limit")

    def test_legacy_schema_two_ledger_is_retained_and_excluded_from_pnl(self):
        raw = copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER)
        raw.pop("provenance_schema")
        row = {"date": "2026-11-11", "session_date": "2026-11-11",
               "triggerTime": "2026-11-11T09:20:00+05:30", "net": 999}
        raw["closed_trades"] = [row]
        raw["intraday_mtm_series"] = [{"session_date": "2026-11-11", "net_mtm": 999}]
        clean = forward_test.sanitize_forward_ledger(raw)
        self.assertEqual(clean["closed_trades"][0]["net"], 999)
        self.assertEqual(clean["closed_trades"][0]["provenance_status"], "LEGACY_UNVERIFIED")
        self.assertIsNone(clean["intraday_mtm_series"][0]["net_mtm"])
        result = forward_test.simulate_forward_portfolio(clean)
        self.assertEqual(result["summary"]["total_net_pnl"], 0)
        self.assertEqual(len(result["unverified_records"]), 1)
        self.assertEqual(raw["closed_trades"], [row])

    def test_special_session_refresh_display_and_paper_gate_agree(self):
        stamp = pd.Timestamp("2026-11-08T14:30:00+05:30")
        self.assertTrue(refresh_policy.refresh_due(stamp))
        self.assertEqual(quote_status.snapshot_status(stamp.isoformat(), "Live midpoint", True, stamp), "Recent snapshot")
        self.assertTrue(forward_test.get_nse_market_status_ist(stamp)["open"])
        self.assertFalse(forward_test.get_nse_market_status_ist("2026-11-08T13:50:00+05:30")["open"])
        self.assertFalse(refresh_policy.refresh_due(pd.Timestamp("2026-11-10T10:00:00+05:30")))
        self.assertTrue(forward_test.get_nse_market_status_ist("2026-11-13T15:39:00+05:30")["open"])

    def test_explicit_late_availability_prevents_early_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "monitor.sqlite"
            with closing(sqlite3.connect(database)) as con:
                con.execute("CREATE TABLE intraday_observations (symbol TEXT, expiry TEXT, as_of TEXT, payload TEXT)")
                con.execute("INSERT INTO intraday_observations VALUES (?, ?, ?, ?)",
                            ("NIFTY", "2026-11-17", "2026-11-11T09:18:00+05:30",
                             json.dumps({"IV": 20, "AvailableAt": "2026-11-11T09:21:00+05:30"})))
                con.commit()
            rows = backtest_signals.load_signals("NIFTY", database)
            self.assertFalse(backtest_signals.decide(rows, "2026-11-17", "2026-11-11", "09:20", [("IV", 0, 30)])[0])

    def test_prior_eod_requires_actual_dated_close(self):
        expiry = "2026-11-17"
        early = [(expiry, pd.Timestamp("2026-11-12T15:30:00+05:30"), {"IV": 20})]
        self.assertFalse(backtest_signals.decide(early, expiry, "2026-11-13", "09:20", [("IV", 0, 30)])[0])
        late = [(expiry, pd.Timestamp("2026-11-12T15:40:00+05:30"), {"IV": 20})]
        self.assertTrue(backtest_signals.decide(late, expiry, "2026-11-13", "09:20", [("IV", 0, 30)])[0])

    def test_dated_ipft_total_does_not_duplicate_exchange_component(self):
        for segment, total in (("opt", 3553), ("fut", 183)):
            charges = broker_charges.charge("2026-03-01", segment, 10000, 1000, "buy")
            self.assertAlmostEqual(charges["IPFT"], .01)
            self.assertAlmostEqual(charges["Exchange"] + charges["IPFT"], total)
        older = broker_charges.charge("2026-02-28", "opt", 10000, 1000, "buy")
        self.assertAlmostEqual(older["IPFT"], 50)

    def test_futures_special_session_book_accepts_only_finite_depth(self):
        stamp = pd.Timestamp("2026-11-08T14:30:00+05:30")
        quote = {"timestamp": stamp.timestamp() * 1000, "depth": {
            "buy": [{"price": 24999, "quantity": 100}],
            "sell": [{"price": 25001, "quantity": 100}],
        }}
        self.assertEqual(futures_data.quote_value(quote, stamp)["Status"], "Accepted")
        quote["depth"]["buy"][0]["quantity"] = float("nan")
        self.assertIsNone(futures_data.quote_value(quote, stamp)["Price"])

    def test_expiry_listing_removes_expired_contract_at_effective_close(self):
        day = pd.Timestamp("2026-12-29").date()
        master = pd.DataFrame([
            {"name": "NIFTY", "instrument_type": kind, "expiry": day}
            for kind in ("CE", "PE", "FUT")
        ])
        self.assertEqual(expiry_data.listed_expiries(master, "NIFTY", pd.Timestamp("2026-12-29T15:31:00+05:30")), [])

    def test_snapshot_write_creates_store_and_uses_no_shared_temporary_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "new-store"
            with patch.object(expiry_data, "DATA", directory):
                saved = expiry_data.write_snapshot({"schema": 4, "rows": [], "value": 1})
            self.assertEqual(json.loads((directory / "expiry_latest.json").read_text()), saved)
            self.assertEqual([p.name for p in directory.iterdir()], ["expiry_latest.json"])

    def test_backtest_api_retains_legacy_run_without_publishing_unverified_pnl(self):
        from volatility_lab.services import web_data
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "backtest_runs"
            runs.mkdir()
            (runs / "legacy.json").write_text(json.dumps({"settings": {"symbol": "NIFTY"},
                                                         "trades": [{"Net": 999}]}))
            with patch.object(web_data, "DATA", Path(tmp)):
                result = web_data.get_backtest_overview()["runs"][0]
            self.assertEqual(result["total_trades"], 1)
            self.assertIsNone(result["total_net_pnl"])
            self.assertEqual(result["trades"], [])

    def test_stop_replay_uses_executable_book_beyond_threshold(self):
        ledger = copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER)
        trade = {"session_date": "2026-11-11", "date": "2026-11-11",
                 "triggerTime": "2026-11-11T09:20:10+05:30", "sym": "NIFTY",
                 "exp": "2026-11-17", "strat": "S2", "capital": 1000, "gross": 100,
                 "legs": [{"side": 1, "type": "CE", "strike": 25000, "entryPx": 100,
                           "exitPx": 102}], "mtmPath": [{"t": "12:30", "fresh": True, "gross": -650,
                           "exit_prices": [90], "quote_times": ["2026-11-11T12:30:00+05:30"]}]}
        ledger["closed_trades"] = [trade]
        result = forward_test.simulate_forward_portfolio(ledger, sl_rule="SL_25_CAP")
        self.assertEqual(result["closed_trades"][0]["simGross"], -650)
        trade["mtmPath"][0].pop("exit_prices")
        self.assertFalse(forward_test.simulate_forward_portfolio(ledger, sl_rule="SL_25_CAP")["closed_trades"])

    def test_saved_futures_reference_requires_exact_expiry_and_fresh_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "futures").mkdir()
            with closing(sqlite3.connect(directory / "futures/futures.sqlite")) as con:
                con.execute("CREATE TABLE snapshots(symbol TEXT, stamp TEXT, payload TEXT)")
                payload = {"legs": [
                    {"expiry": "2026-11-23", "quote": {"Price": 25100, "As of": "2026-11-13T10:00:00+05:30",
                                                        "Source": "Live midpoint", "Status": "Accepted"}},
                    {"expiry": "2026-12-29", "quote": {"Price": 25200, "As of": "2026-11-12T10:00:00+05:30",
                                                        "Source": "Live midpoint", "Status": "Accepted"}},
                ]}
                con.execute("INSERT INTO snapshots VALUES (?, ?, ?)", ("NIFTY", "2026-11-13", json.dumps(payload)))
                con.commit()
            rows = [{"Instrument": "NIFTY", "Expiry": exp, "As of": "2026-11-13T10:00:00+05:30",
                     "IV": 20, "Price": 25000, "ATM strike": 25000, "DTE": 1}
                    for exp in ("2026-11-17", "2026-11-23", "2026-12-29")]
            with patch.object(expiry_data, "DATA", directory), patch.object(expiry_data.base, "aligned_spread", return_value={"HV30": 15}):
                result = expiry_data.align_snapshot({"rows": rows})
            self.assertIsNone(result["rows"][0].get("Month future LTP"))
            self.assertEqual(result["rows"][1]["Month future LTP"], 25100)
            self.assertIsNone(result["rows"][2].get("Month future LTP"))
