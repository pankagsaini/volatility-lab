"""Focused regressions for live quote integrity and tradable backtest inputs."""
import copy
import json
from pathlib import Path
import inspect
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "volatility_lab"))

import backtest_engine
import forward_test


class TestExecutionSafetyFixes(unittest.TestCase):
    @staticmethod
    def _read_docs_range(start, end):
        path = ROOT / "docs" / "index.html"
        with path.open(encoding="utf-8") as stream:
            for _ in range(start - 1):
                next(stream, None)
            return "".join(next(stream, "") for _ in range(end - start + 1))

    @staticmethod
    def _chain_row(
        strike, call_bid=99.0, call_ask=101.0, put_bid=89.0, put_ask=91.0,
        depth=1000, quote_time="2026-11-11T15:15:00+05:30",
    ):
        return {
            "strike_price": strike,
            "call_options": {
                "market_data": {
                    "ltp": 100.0, "bid_price": call_bid, "ask_price": call_ask,
                    "bid_qty": depth, "ask_qty": depth,
                    "quote_timestamp": quote_time,
                },
                "option_greeks": {"iv": 20.0},
            },
            "put_options": {
                "market_data": {
                    "ltp": 90.0, "bid_price": put_bid, "ask_price": put_ask,
                    "bid_qty": depth, "ask_qty": depth,
                    "quote_timestamp": quote_time,
                },
                "option_greeks": {"iv": 20.0},
            },
        }

    def _priced_row(self, strike, timestamp):
        time_value = 200 / (1 + abs(strike - 25050) / 150)
        call = time_value + max(0, 25050 - strike)
        put = time_value + max(0, strike - 25050)
        row = self._chain_row(strike, call - .5, call + .5, put - .5, put + .5, quote_time=timestamp)
        return dict(row, underlying_spot_price=25050.0)

    @staticmethod
    def _position():
        return {
            "id": "OPEN-20261111-NIF-1117-S1",
            "date": "2026-11-11",
            "session_date": "2026-11-11",
            "triggerTime": "2026-11-11T09:20:00+05:30",
            "trigTime": "09:20 IST",
            "sym": "NIFTY",
            "exp": "2026-11-17",
            "strat": "S1",
            "capital": 28037,
            "maxLoss": 7800,
            "maxCredit": 11000,
            "legs": [
                {"side": -1, "mult": 1, "strike": 25250, "type": "CE", "entryPx": 99.0, "curPx": 99.0},
                {"side": -1, "mult": 1, "strike": 24850, "type": "PE", "entryPx": 89.0, "curPx": 89.0},
            ],
        }

    def test_forward_entry_rejects_ltp_without_executable_book(self):
        rows = [
            {
                "strike": strike, "cLtp": 100.0, "pLtp": 90.0,
                "cIv": 20.0, "pIv": 20.0,
            }
            for strike in range(24000, 26100, 50)
        ]
        setups = forward_test.evaluate_strategies_for_chain(
            "NIFTY", "2026-11-17", 25050.0, rows, hv10=14.0, hv30=15.0, dte=6,
        )
        self.assertEqual(setups, [])

    def test_forward_quote_helper_rejects_nonfinite_prices_and_depth(self):
        invalid_books = [
            {"cBid": float("inf"), "cAsk": float("inf"), "cBidQty": 100, "cAskQty": 100},
            {"cBid": 99, "cAsk": 101, "cBidQty": 100, "cAskQty": float("nan")},
        ]
        for book in invalid_books:
            self.assertEqual(forward_test._executable_quote(book, "CE", -1, 65, True), 0.0)
        normalized = forward_test._build_strike_quote_map([
            {"strike_price": 25050, "call_options": {"market_data": {"bid_price": "invalid"}}},
            {"strike_price": "invalid", "cLtp": 1},
        ])
        self.assertEqual(normalized[25050]["cBid"], 0.0)
        self.assertEqual(len(normalized), 1)

    def test_live_forward_gate_entries_mtm_and_scheduled_exit_are_consistent(self):
        ledger = copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER)

        def payload(timestamp):
            rows = [
                self._priced_row(strike, timestamp)
                for strike in range(24000, 26100, 50)
            ]
            return {
                "schema": 1,
                "provider": "Upstox",
                "mode": "snapshot",
                "status": "complete",
                "day": "2026-11-11",
                "retrieved_at": timestamp,
                "nfo_session_validated": True,
                "risk_metrics": {"NIFTY": {"as_of": "2026-11-11", "hv10": 14.0, "hv30": 15.0}},
                "records": [{"label": "NIFTY|2026-11-17", "retrieved_at": timestamp, "data": rows}],
            }

        opened = forward_test.process_live_upstox_snapshot(
            payload("2026-11-11T09:20:00+05:30"), ledger=ledger, now_ist="2026-11-11T09:20:00+05:30",
        )
        self.assertGreater(len(opened["open_positions"]), 0)
        midday = forward_test.process_live_upstox_snapshot(
            payload("2026-11-11T12:30:00+05:30"), ledger=opened, now_ist="2026-11-11T12:30:00+05:30",
        )
        self.assertGreater(len(midday["open_positions"]), 0)
        self.assertGreaterEqual(len(midday["intraday_mtm_series"]), 2)
        closed = forward_test.process_live_upstox_snapshot(
            payload("2026-11-11T15:15:00+05:30"), ledger=midday, now_ist="2026-11-11T15:15:00+05:30",
        )
        self.assertEqual(closed["open_positions"], [])
        self.assertGreater(len(closed["closed_trades"]), 0)
        self.assertTrue(all(tr["exitTime"] == "15:15 IST" for tr in closed["closed_trades"]))

    def test_partial_snapshot_cannot_create_or_mark_forward_positions(self):
        timestamp = "2026-11-11T09:20:00+05:30"
        rows = [
            self._priced_row(strike, timestamp)
            for strike in range(24000, 26100, 50)
        ]
        complete_payload = {
            "schema": 1,
            "provider": "Upstox",
            "mode": "snapshot",
            "status": "complete",
            "day": "2026-11-11",
            "retrieved_at": timestamp,
            "nfo_session_validated": True,
            "risk_metrics": {"NIFTY": {"as_of": "2026-11-11", "hv10": 14.0, "hv30": 15.0}},
            "records": [{"label": "NIFTY|2026-11-17", "retrieved_at": timestamp, "data": rows}],
        }
        complete_state = forward_test.process_live_upstox_snapshot(
            complete_payload,
            ledger=copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER),
            now_ist=timestamp,
        )
        self.assertGreater(len(complete_state["open_positions"]), 0)

        partial_payload = copy.deepcopy(complete_payload)
        partial_payload["status"] = "partial"
        partial_payload["errors"] = [{"item": "BANKNIFTY|2026-11-17", "reason": "Upstream request failed"}]
        withheld_state = forward_test.process_live_upstox_snapshot(
            partial_payload,
            ledger=copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER),
            now_ist=timestamp,
        )
        self.assertEqual(withheld_state["open_positions"], [])
        self.assertTrue(withheld_state["gate_blocked"])
        self.assertIn("incomplete", withheld_state["gate_message"].lower())

    def test_stale_quote_defers_1515_close_until_fresh_two_sided_depth(self):
        chain = [self._chain_row(25250), self._chain_row(24850)]
        stale_open, stale_closed = forward_test.mark_open_positions_to_market(
            [self._position()], {"NIFTY|2026-11-17": chain},
            current_time_ist="15:15 IST", auto_close_intraday_1515=True,
            live_chain_timestamps={"NIFTY|2026-11-17": "2026-11-11T09:00:00+05:30"},
            reference_timestamp="2026-11-11T15:15:00+05:30",
        )
        self.assertEqual(len(stale_open), 1)
        self.assertEqual(stale_closed, [])
        self.assertTrue(stale_open[0]["exit_pending"])
        self.assertEqual(stale_open[0]["freshness"], "STALE")

        fresh_open, fresh_closed = forward_test.mark_open_positions_to_market(
            stale_open, {"NIFTY|2026-11-17": chain},
            current_time_ist="15:16 IST", auto_close_intraday_1515=False,
            live_chain_timestamps={"NIFTY|2026-11-17": "2026-11-11T15:16:00+05:30"},
            reference_timestamp="2026-11-11T15:16:00+05:30",
        )
        self.assertEqual(fresh_open, [])
        self.assertEqual(len(fresh_closed), 1)
        self.assertEqual(fresh_closed[0]["legs"][0]["exitPx"], 101.0)
        self.assertIn("delayed until fresh quote", fresh_closed[0]["exitReason"])

    def test_empty_close_snapshot_marks_open_position_exit_pending(self):
        ledger = copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER)
        ledger["open_positions"] = [self._position()]
        state = forward_test.process_live_upstox_snapshot(
            {
                "schema": 1, "provider": "Upstox", "mode": "snapshot", "status": "complete",
                "day": "2026-11-11", "retrieved_at": "2026-11-11T15:15:00+05:30",
                "nfo_session_validated": True, "records": [],
            },
            ledger=ledger,
            now_ist="2026-11-11T15:15:00+05:30",
        )
        self.assertEqual(len(state["open_positions"]), 1)
        self.assertTrue(state["open_positions"][0]["exit_pending"])
        self.assertEqual(state["open_positions"][0]["freshness"], "STALE")
        self.assertEqual(state["status"], "EXIT_PENDING_MISSING_QUOTES")
        mtm_point = state["intraday_mtm_series"][-1]
        self.assertIsNone(mtm_point["gross_mtm"])
        self.assertIsNone(mtm_point["net_mtm"])
        self.assertEqual(mtm_point["integrity_status"], "WITHHELD_NO_LIVE_OPTION_CHAIN")

    def test_stale_open_position_withholds_aggregate_mtm_instead_of_carrying_a_mark(self):
        ledger = copy.deepcopy(forward_test.CLEAN_SLATE_LEDGER)
        position = self._position()
        position.update({"grossMtm": 4200.0, "netMtm": 3950.0, "estCharges": 250.0})
        ledger["open_positions"] = [position]
        stale_rows = [self._chain_row(25250, quote_time="2026-11-11T09:00:00+05:30")]
        payload = {
            "schema": 1,
            "provider": "Upstox",
            "mode": "snapshot",
            "status": "complete",
            "day": "2026-11-11",
            "retrieved_at": "2026-11-11T12:30:00+05:30",
            "nfo_session_validated": True,
            "records": [{
                "label": "NIFTY|2026-11-17",
                "retrieved_at": "2026-11-11T09:00:00+05:30",
                "data": stale_rows,
            }],
        }
        state = forward_test.process_live_upstox_snapshot(
            payload, ledger=ledger, now_ist="2026-11-11T12:30:00+05:30",
        )
        self.assertEqual(state["open_positions"][0]["freshness"], "STALE")
        mtm_point = state["intraday_mtm_series"][-1]
        self.assertIsNone(mtm_point["gross_mtm"])
        self.assertIsNone(mtm_point["net_mtm"])
        self.assertEqual(mtm_point["integrity_status"], "WITHHELD_STALE_OR_UNVERIFIED_POSITION")

    def test_backtest_snapshot_marks_cannot_pass_execution_volume_gate(self):
        mark = backtest_engine._nontradable_mark_bar(100.0)
        self.assertEqual(mark, [100.0, 100.0, 100.0, 100.0, 0.0])
        self.assertIsNone(backtest_engine._nontradable_mark_bar(float("nan")))
        self.assertIsNone(backtest_engine._nontradable_mark_bar(float("inf")))

        day = "2026-11-11"
        contracts = [
            {"instrument_key": "CE", "trading_symbol": "NIFTY CE", "instrument_type": "CE", "strike_price": 25050, "expiry": "2026-11-17"},
            {"instrument_key": "PE", "trading_symbol": "NIFTY PE", "instrument_type": "PE", "strike_price": 25050, "expiry": "2026-11-17"},
        ]
        series = {
            "NSE_INDEX|Nifty 50": {f"{day} 09:19": [25050.0, 25050.0, 25050.0, 25050.0, 0.0]},
            "CE": {f"{day} 09:20": mark, f"{day} 15:15": mark},
            "PE": {f"{day} 09:20": mark, f"{day} 15:15": mark},
        }
        with patch.object(backtest_engine, "_expected_trading_days", return_value=[day]):
            trades, skips = backtest_engine.simulate(
                contracts, series, "NSE_INDEX|Nifty 50", day, day,
            )
        self.assertTrue(trades.empty)
        self.assertIn("Insufficient traded minute volume", skips["Reason"].tolist())

        source = Path(backtest_engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("for m in range(376)", source)
        self.assertNotIn("max(vol, 1.0)", source)
        self.assertNotIn("[px, px, px, px, 500]", source)

    def test_backtest_defaults_to_dated_upstox_fee_schedule(self):
        parameters = inspect.signature(backtest_engine.simulate).parameters
        self.assertTrue(parameters["use_upstox_fees"].default)
        self.assertEqual(parameters["order_fee"].default, 25)

        day = "2026-11-11"
        contracts = [
            {"instrument_key": "CE", "trading_symbol": "NIFTY CE", "instrument_type": "CE", "strike_price": 25050, "expiry": "2026-11-17"},
            {"instrument_key": "PE", "trading_symbol": "NIFTY PE", "instrument_type": "PE", "strike_price": 25050, "expiry": "2026-11-17"},
        ]
        traded = [100.0, 101.0, 99.0, 100.0, 1000.0]
        series = {
            "NSE_INDEX|Nifty 50": {f"{day} 09:19": [25050.0, 25050.0, 25050.0, 25050.0, 0.0]},
            "CE": {f"{day} 09:20": traded, f"{day} 15:15": traded},
            "PE": {f"{day} 09:20": traded, f"{day} 15:15": traded},
        }
        with patch.object(backtest_engine, "_expected_trading_days", return_value=[day]):
            trades, skips = backtest_engine.simulate(
                contracts, series, "NSE_INDEX|Nifty 50", day, day,
            )
        self.assertTrue(skips.empty)
        self.assertEqual(trades.loc[0, "Fee model"], "upstox_statutory")
        self.assertEqual(trades.loc[0, "Execution model"], backtest_engine.EXECUTION_MODEL_VERSION)
        self.assertGreater(trades.loc[0, "Charges"], 0.0)

    def test_package_collectors_import_shared_monitor_base(self):
        """Facade imports must not fail after the market-table collector rewrite."""
        script = "\n".join([
            "from volatility_lab.market_table import INDICES, Monitor",
            "from volatility_lab.expiry_data import ExpiryMonitor",
            "from volatility_lab.futures_data import FuturesMonitor",
            "assert set(INDICES) == {'NIFTY', 'BANKNIFTY'}",
            "assert issubclass(ExpiryMonitor, Monitor)",
            "assert issubclass(FuturesMonitor, Monitor)",
        ])
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_apps_script_guards_property_size_and_never_fabricates_entry_price(self):
        code = (ROOT / "volatility_lab" / "cloud" / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        self.assertIn("EOD_PENDING_DAY_", code)
        self.assertIn("function loadPendingEod_()", code)
        self.assertIn("function savePendingEod_(pending)", code)
        self.assertIn("const SNAPSHOT_RETENTION_DAYS = 0", code)
        self.assertIn("retention: 'indefinite'", code)
        self.assertNotIn("retentionDays === undefined", code)
        self.assertIn("function forwardQuoteFresh_(timestamp, referenceTimestamp)", code)
        self.assertIn("const FORWARD_SESSION_WINDOWS_ = {}", code)
        self.assertIn("session = marketWindow_(d)", code)
        self.assertIn("customSpecial[today] || SPECIAL_SESSIONS[today]", code)
        self.assertNotIn("isoDay === '2026-11-10'", code)
        self.assertIn("function executableQuote_(node, type, side, quantity, isExit, referenceTimestamp)", code)
        self.assertIn("[bid, ask, bidQty, askQty].every(Number.isFinite)", code)
        self.assertIn("Number.isFinite(ch.spot) || ch.spot <= 0", code)
        self.assertIn("const debitInr = Math.round(legs.reduce", code)
        self.assertIn("mins >= 915 || Boolean(pos.exit_pending)", code)
        self.assertIn("delayed until fresh quote", code)
        self.assertIn("function listDriveFilesPage_(folderId, pageToken, orderBy)", code)
        self.assertIn("'modifiedTime desc'", code)
        self.assertNotIn("method: 'delete'", code)
        self.assertIn("timeoutSeconds: 8", code)
        self.assertIn("timeoutSeconds: 30", code)
        self.assertIn("timeoutSeconds: 15", code)
        self.assertIn("timeoutSeconds: 60", code)
        self.assertNotIn("timeoutSeconds: 240", code)
        self.assertIn("Date.now() - started > CAPTURE_SAFE_DEADLINE_MS", code)
        self.assertIn("Date.now() - started > 60000", code)
        self.assertIn("if (budget.ms >= 75 * 60000)", code)
        self.assertIn("function readRuntimeBudget_(properties, key, nowMs)", code)
        self.assertIn("windowEndMs", code)
        self.assertIn("Preserve the prior calendar-day counter during migration", code)
        self.assertIn("properties.setProperty('SNAPSHOT_GAP_DAY', today)", code)
        self.assertIn("properties.getProperty('SNAPSHOT_GAP_DAY') === String(payload.day || '').slice(0, 10)", code)
        self.assertNotIn("budget.day !== today", code)
        self.assertNotIn("file.setTrashed(true)", code)
        self.assertNotIn("|| 25.0", code)
        self.assertNotIn("close_price || 0", code)
        self.assertIn("WITHHELD_STALE_OR_UNVERIFIED_POSITION", code)
        self.assertIn("WITHHELD_NO_LIVE_OPTION_CHAIN", code)
        self.assertIn("mode === 'snapshot' && report.status !== 'complete'", code)
        self.assertIn("withheld_incomplete_snapshot", code)
        self.assertIn("Forward test requires a complete snapshot", code)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the Apps Script helper")
    def test_apps_script_runtime_budget_uses_a_rolling_24_hour_window(self):
        code = (ROOT / "volatility_lab" / "cloud" / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        start = code.index("function readJsonProperty_(")
        end = code.index("\nfunction addRuntimeBudget_(", start)
        helper = code[start:end]
        script = "\n".join([
            "const RUNTIME_BUDGET_WINDOW_MS = 24 * 60 * 60 * 1000;",
            helper,
            "const props = value => ({getProperty: () => JSON.stringify(value), setProperty: () => {}});",
            "const active = readRuntimeBudget_(props({windowStartMs: 1000, windowEndMs: 86401000, ms: 1200}), 'x', 86400999);",
            "if (active.ms !== 1200) throw new Error('active window lost accumulated runtime');",
            "const expired = readRuntimeBudget_(props({windowStartMs: 1000, windowEndMs: 86401000, ms: 1200}), 'x', 86401000);",
            "if (expired.ms !== 0 || expired.windowStartMs !== 86401000) throw new Error('expired window did not roll');",
            "const migrated = readRuntimeBudget_(props({day: '2026-11-11', ms: 3400}), 'x', 500);",
            "if (migrated.ms !== 3400) throw new Error('legacy runtime was discarded');",
        ])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the Apps Script helper")
    def test_apps_script_option_gst_matches_python_charge_schedule(self):
        code = (ROOT / "volatility_lab" / "cloud" / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        start = code.index("function upstoxOptionCharge_(")
        end = code.index("\nfunction positionCharges_(", start)
        helper = code[start:end]
        script = helper + "\nprocess.stdout.write(String(upstoxOptionCharge_(100, 75, true)));"
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        js_total = float(result.stdout)

        import broker_charges

        python_parts = broker_charges.charge("2026-11-11", "Options", 100.0, 75, "sell")
        self.assertAlmostEqual(js_total, sum(python_parts.values()), places=9)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the browser Black-76 helpers")
    def test_browser_black76_handles_expiry_and_keeps_missing_inputs_unpriced(self):
        html = self._read_docs_range(1180, 1400)

        def function_source(name, next_name):
            start = html.index(f"function {name}(")
            next_function = html.index(f"function {next_name}(", start)
            end = html.rfind("\n", start, next_function)
            return html[start:end]

        all_html = (ROOT / "index.html").read_text(encoding="utf-8")
        def live_helper(name):
            start = all_html.index("      function " + name + "(")
            end = all_html.index("\n      }", start) + 8
            return all_html[start:end]

        helpers = "\n".join([
            live_helper("liveTimestamp"),
            live_helper("liveBookValid"),
            function_source("computeBlack76ParityForward", "black76Greeks"),
            function_source("black76Greeks", "black76Price"),
            function_source("enrichItemWithBlack76", "populateExpiryDropdown"),
        ])
        script = "\n".join([
            "var B76_RISK_FREE_RATE = 0.05;",
            "var seenTimes = [];",
            "var window = {};",
            "function normPdf() { return 0; }",
            "function normCdf() { return 0.5; }",
            "function black76Price() { return NaN; }",
            "function black76IvDec(px, fwd, strike, years) { seenTimes.push(years); return NaN; }",
            "function black76Iv() { return NaN; }",
            helpers,
            "var expiry = black76Greeks(105, 100, 0, 0.05, 0, true);",
            "if (expiry.price !== 5 || expiry.gamma !== 0) throw new Error('expiry intrinsic value failed');",
            "var unverified = enrichItemWithBlack76({dte: 0.01, atm: 100, chain: [{k: 100, cLtp: 10, pLtp: 9, uCallIv: 22, uPutIv: 23}]});",
            "if (!Number.isNaN(unverified.syn) || unverified.chain[0].b76CallIv !== null || seenTimes.length !== 0) throw new Error('unverified LTPs were promoted to Black-76 inputs');",
            "var subday = enrichItemWithBlack76({quoteIntegrity: 'VALIDATED_EVENT_TIME_TWO_SIDED_BOOK', snapshotId: '2026-11-13T04:30:00Z', dte: 0.01, atm: 100, chain: [{k: 100, cQuoteAt: '2026-11-13T04:30:00Z', pQuoteAt: '2026-11-13T04:30:00Z', cBidQty: 100, cAskQty: 100, pBidQty: 100, pAskQty: 100, cBid: 9.9, cAsk: 10.1, pBid: 8.9, pAsk: 9.1}]});",
            "if (Math.abs(seenTimes[0] - (0.01 / 365)) > 1e-15) throw new Error('sub-day DTE was floored');",
            "if (subday.chain[0].b76CallIv !== null || subday.chain[0].b76PutIv !== null) throw new Error('unpriced IV was substituted');",
            "var zeroDte = enrichItemWithBlack76({quoteIntegrity: 'VALIDATED_EVENT_TIME_TWO_SIDED_BOOK', snapshotId: '2026-11-13T04:30:00Z', dte: 0, atm: 100, chain: [{k: 100, cQuoteAt: '2026-11-13T04:30:00Z', pQuoteAt: '2026-11-13T04:30:00Z', cBidQty: 100, cAskQty: 100, pBidQty: 100, pAskQty: 100, cBid: 9.9, cAsk: 10.1, pBid: 8.9, pAsk: 9.1}]});",
            "if (!Number.isNaN(zeroDte.syn) || zeroDte.b76AtmIv !== null) throw new Error('date-only zero DTE was treated as a precise expiry time');",
            "var missing = enrichItemWithBlack76({dte: 1, atm: 100, syn: 110, spot: 111, chain: [{k: 100, cLtp: 0, pLtp: 0, uCallIv: 22, uPutIv: 23}]});",
            "if (!Number.isNaN(missing.syn) || missing.chain[0].b76CallIv !== null || missing.chain[0].b76OtmIv !== null) throw new Error('spot or upstream IV fallback was used');",
        ])
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_browser_forward_workspace_never_recalculates_or_persists_cloud_ledger(self):
        html = self._read_docs_range(675, 3400)
        self.assertIn("CLOUD-AUTHORITATIVE FORWARD-TEST LEDGER", html)
        self.assertIn("MTM WITHHELD — STALE MARK", html)
        self.assertIn("isFreshValidatedCloudSnapshot(payload)", html)
        self.assertIn("isFreshCloudForwardLedger(cloudLedger, snapshot)", html)
        self.assertIn("Browser-side forward-trade construction is disabled", html)
        self.assertNotIn("forwardOpenPositions.push", html)
        self.assertNotIn("forwardClosedTrades.unshift", html)
        self.assertNotIn("calcPositionUpstoxCharges", html)
        self.assertNotIn("localStorage.setItem(FWD_STORAGE_KEY", html)


if __name__ == "__main__":
    unittest.main()
