#!/usr/bin/env python3
"""Comprehensive verification suite testing all 5 audit tracks of Volatility Lab remediations."""
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import gzip
import json, shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
import numpy as np
import pandas as pd

# Provide lightweight headless stubs if streamlit/plotly/msvcrt are not installed in headless CI
for mod_name in ("streamlit", "plotly", "plotly.graph_objects", "plotly.subplots"):
    if mod_name not in sys.modules:
        m = SimpleNamespace()
        if mod_name == "streamlit":
            m.fragment = lambda *a, **kw: (lambda fn: fn)
            m.cache_data = lambda *a, **kw: (lambda fn: fn)
            m.cache_resource = lambda *a, **kw: (lambda fn: fn)
            m.session_state = {}
        sys.modules[mod_name] = m
sys.modules["plotly.graph_objects"].Figure = object
sys.modules["plotly.graph_objects"].Surface = object
sys.modules["plotly.graph_objects"].Scatter = object
sys.modules["plotly"].graph_objects = sys.modules["plotly.graph_objects"]
sys.modules["plotly"].subplots = sys.modules["plotly.subplots"]
if "msvcrt" not in sys.modules:
    sys.modules["msvcrt"] = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=lambda *a, **kw: None)


# Add patched directory to sys.path when running in sandbox or local repo
HERE_DIR = Path(__file__).resolve().parent
PATCH_DIR = Path("/working_dir/c_e735eeeacddd5439/artifacts/file_generation/ttl=63d/intermediate/patched")
for candidate in (PATCH_DIR, HERE_DIR, HERE_DIR / "Volatility Lab"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
if not PATCH_DIR.exists():
    PATCH_DIR = HERE_DIR / "Volatility Lab"

import vol_lib
import option_math
import smile
import term_structure
import forecast
import expiry_forecast
import relative_value
import pricing_inputs
import vol_surface_3d
import futures_backtest_download
import backtest_download
import backtest_engine
import strategy_backtest
import backtest_jobs
import refresh_policy
import background_refresh
import market_calendar


def _synthetic_ohlc(n_days=260, daily_ret=0.01, end_date="2026-10-16"):
    """Build a deterministic n_days OHLC DataFrame on NSE trading days with constant daily magnitude."""
    end_d = pd.Timestamp(end_date).date()
    all_days = [
        d.date() for d in pd.date_range("2025-01-01", end_d)
        if d.weekday() < 5 and d.date() not in market_calendar.HOLIDAYS
    ]
    dates = all_days[-n_days:]
    prices = [24000.0]
    for i in range(1, len(dates)):
        sign = 1.0 if (i % 2 == 0) else -1.0
        prices.append(prices[-1] * np.exp(sign * daily_ret))
    closes = np.array(prices)
    opens = closes * 0.999
    highs = np.maximum(opens, closes) * 1.002
    lows = np.minimum(opens, closes) * 0.998
    return pd.DataFrame({
        "Date": pd.to_datetime(dates),
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
    })


class TestTrack1MathAndForecasting(unittest.TestCase):
    """Track 1: Quantitative Math, Volatility Surface & Forecasting tests."""

    def test_validate_ohlc_idempotent_flag_and_rv_estimators(self):
        df = _synthetic_ohlc(60)
        validated = vol_lib.validate_ohlc(df)
        self.assertTrue(getattr(validated, "_ohlc_validated", False))
        # Second call returns the exact same validated object without re-copying
        self.assertIs(vol_lib.validate_ohlc(validated), validated)

        snap = vol_lib.rv_snapshot(validated, window=20)
        for key in (
            "close_to_close_rv",
            "parkinson_rv",
            "garman_klass_rv",
            "rogers_satchell_rv",
            "yang_zhang_rv",
            "ewma_next_session_vol",
        ):
            self.assertIn(key, snap)
            self.assertTrue(np.isfinite(snap[key]) and snap[key] > 0.0)

        # Verify forecast.rolling_rv delegates cleanly and matches rv_snapshot tail
        roll = forecast.rolling_rv(validated, window=20)
        self.assertAlmostEqual(roll["Close-to-close RV %"].iloc[-1], snap["close_to_close_rv"] * 100.0, places=6)
        self.assertAlmostEqual(roll["Yang-Zhang RV %"].iloc[-1], snap["yang_zhang_rv"] * 100.0, places=6)
        self.assertAlmostEqual(roll["EWMA next-session %"].iloc[-1], snap["ewma_next_session_vol"] * 100.0, places=6)

    def test_bsm_greeks_and_iv_solver_roundtrip(self):
        spot, strike, years, rate, true_vol, div = 24500.0, 24600.0, 14.0 / 365.0, 0.066, 0.145, 0.012
        for kind in ("CE", "PE"):
            greeks = option_math.bsm_greeks(spot, strike, years, rate, true_vol, kind, dividend=div)
            self.assertTrue(greeks["gamma"] > 0)
            self.assertTrue(greeks["vega"] > 0)
            price, delta = option_math.price_delta(spot, strike, years, rate, true_vol, kind, dividend=div)
            recovered_iv = option_math.solve_iv(price, spot, strike, years, rate, kind, dividend=div)
            self.assertAlmostEqual(recovered_iv, true_vol, places=6)

    def test_smile_and_reversal_formatting_and_empty_strikes(self):
        chain = pd.DataFrame([
            {"strike": 24400, "option_type": "PE", "iv": 0.16, "delta": -0.25, "source": "Upstox"},
            {"strike": 24400, "option_type": "PE", "iv": 0.18, "delta": -0.10, "source": "Upstox"},
            {"strike": 24500, "option_type": "CE", "iv": 0.14, "delta": 0.52, "source": "Upstox"},
            {"strike": 24500, "option_type": "PE", "iv": 0.14, "delta": -0.48, "source": "Upstox"},
            {"strike": 24600, "option_type": "CE", "iv": 0.13, "delta": 0.25, "source": "Upstox"},
            {"strike": 24700, "option_type": "CE", "iv": 0.125, "delta": 0.10, "source": "Upstox"},
        ])
        # Even when strikes=[] is empty, ATM is still resolved from paired strikes in chain
        res = smile.smile_points(chain, strikes=[], spot=24500.0)
        pts = {p["Point"]: p for p in res["points"]}
        self.assertAlmostEqual(pts["ATM"]["IV"], 14.0, places=5)
        self.assertEqual(pts["ATM"]["Source"], "Upstox")
        self.assertFalse(pts["ATM"]["Strikes"].startswith(":"))
        self.assertAlmostEqual(res["metrics"]["25 RR · put minus call"], 3.0, places=5)

        # Incomplete chain: reversal_values must return NaN for used deltas when one leg is missing
        incomplete = chain[chain["option_type"] == "PE"]
        rev = option_math.reversal_values(incomplete, "NIFTY")
        self.assertTrue(np.isnan(rev["25 Delta reversal"]))
        self.assertTrue(np.isnan(rev["Reversal put delta"]))
        self.assertTrue(np.isnan(rev["Reversal call delta"]))

    def test_term_structure_forward_iv_and_calendar_arbitrage(self):
        rows = [
            {"Instrument": "NIFTY", "Expiry": "2026-10-22", "Type": "Weekly", "IV": 14.0, "Source": "Upstox", "As of": "2026-10-15T15:45:00+05:30"},
            {"Instrument": "NIFTY", "Expiry": "2026-10-29", "Type": "Monthly", "IV": 16.0, "Source": "Upstox", "As of": "2026-10-15T15:45:00+05:30"},
        ]
        ts = term_structure.term_structure(rows, "NIFTY")
        self.assertIn("Forward IV", ts.columns)
        self.assertIn("Calendar Arbitrage", ts.columns)
        # Total variance at T2 (14d, 16%) > T1 (7d, 14%), so Forward IV > 16% and no calendar arbitrage
        self.assertTrue(ts.loc[0, "Forward IV"] > 16.0)
        self.assertFalse(bool(ts.loc[0, "Calendar Arbitrage"]))

        # Invert IV sharply so T2 total variance < T1 total variance -> Calendar Arbitrage = True
        rows_arb = [
            {"Instrument": "NIFTY", "Expiry": "2026-10-22", "Type": "Weekly", "IV": 25.0, "Source": "Upstox", "As of": "2026-10-15T15:45:00+05:30"},
            {"Instrument": "NIFTY", "Expiry": "2026-10-29", "Type": "Monthly", "IV": 12.0, "Source": "Upstox", "As of": "2026-10-15T15:45:00+05:30"},
        ]
        ts_arb = term_structure.term_structure(rows_arb, "NIFTY")
        self.assertTrue(bool(ts_arb.loc[0, "Calendar Arbitrage"]))
        self.assertTrue(np.isnan(ts_arb.loc[0, "Forward IV"]))

    def test_expiry_forecast_variance_bridge_and_sessions_boundary(self):
        # 1. Verify start > end returns [] even across 2026->2027 boundary
        self.assertEqual(expiry_forecast.sessions("2027-01-01", "2026-12-31"), [])

        # 2. Verify Friday-to-Monday weekend forecast is NOT crushed by -29.5% calendar ratio
        df = _synthetic_ohlc(260, daily_ret=0.01, end_date="2026-10-16")  # Friday Oct 16, 2026
        snap_vol_pct = vol_lib.rv_snapshot(df)["ewma_next_session_vol"] * 100.0
        # Evaluate at Friday 15:45 IST for Monday Oct 19, 2026 expiry (1 trading session, 3 calendar days)
        res_mon = expiry_forecast.expiry_forecast(df, "2026-10-19", "2026-10-16T15:45:00+05:30", iv=16.0)
        self.assertAlmostEqual(res_mon["EWMA expiry RV"], snap_vol_pct, places=4)

        # 3. Verify relative_value.observe() calls expiry_forecast with correct argument order
        obs = relative_value.observe(
            {"Instrument": "NIFTY", "Expiry": "2026-10-22", "As of": "2026-10-16T15:45:00+05:30", "IV": 16.0},
            history_frame=df,
        )
        self.assertAlmostEqual(obs["EWMA expiry RV"], snap_vol_pct, places=4)
        self.assertIsNotNone(obs.get("GARCH expiry RV"))


class TestTrack2CloudAndDownloaders(unittest.TestCase):
    """Track 2: Cloud Capture (Code.gs) and Background Downloaders tests."""

    def test_code_gs_weekend_guard_and_budget_fixes(self):
        code_gs_path = (PATCH_DIR / "cloud" / "apps_script" / "Code.gs") if (PATCH_DIR / "cloud" / "apps_script" / "Code.gs").exists() else (PATCH_DIR / "Code.gs")
        code_gs = code_gs_path.read_text(encoding="utf-8")
        # 1. Weekend guard appears before reconcilePriorSession() in scheduledCapture()
        sched_idx = code_gs.index("function scheduledCapture()")
        sched_body = code_gs[sched_idx:sched_idx + 1200]
        self.assertLess(sched_body.index("if (weekday > 5)"), sched_body.index("reconcilePriorSession()"))
        # 2. Artificial + 1000ms budget padding removed
        self.assertNotIn("Date.now() - started + 1000", code_gs)
        # 3. 401/403 retry storm prevention present
        self.assertIn("res.error === 'Broker HTTP 401'", code_gs)
        # 4. Optional WEBHOOK_KEY guard in doGet
        self.assertIn("WEBHOOK_KEY", code_gs)

    def test_futures_backtest_download_cache_hit_has_data_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            futures_backtest_download.ROOT = Path(tmp)
            calls = []
            def fake_api(path, params):
                calls.append((path, params))
                return {"candles": [["2026-10-16T00:00:00+05:30", 100, 110, 95, 105, 1000]]}
            first = futures_backtest_download.fetch("upstox", fake_api, "/v2/test", {"k": "v"})
            second = futures_backtest_download.fetch("upstox", fake_api, "/v2/test", {"k": "v"})
            self.assertEqual(len(calls), 1)  # Second call served from gzip cache without KeyError: 'data'
            self.assertEqual(first, second)

    def test_backtest_download_db_pragmas_and_3_strike_isolation(self):
        btd_code = (PATCH_DIR / "backtest_download.py").read_text(encoding="utf-8")
        self.assertIn("PRAGMA busy_timeout=30000", btd_code)
        self.assertIn('WHERE state IN ("failed","running")', btd_code)
        self.assertNotIn("if attempts>=2:status(c,'paused_error',error_type=type(exc).__name__);return", btd_code)


class TestTrack3BacktestEngines(unittest.TestCase):
    """Track 3: Options & Futures Backtesting Engines & Execution Realism tests."""

    def test_strategy_backtest_quiet_minute_carry_forward_and_som_margin(self):
        # 1. Verify unknown template raises ValueError instead of silently returning Iron condor
        with self.assertRaises(ValueError):
            strategy_backtest.template("NonExistentStrategy")

        # 2. Verify Short Option Minimum (5% SOM) margin floor on deep OTM short strangle
        legs = [
            {"kind": "CE", "strike": 30000, "side": -1, "entry": 5.0, "quantity": 75},
            {"kind": "PE", "strike": 19000, "side": -1, "entry": 5.0, "quantity": 75},
        ]
        margin = strategy_backtest.estimate_exchange_margin(legs, spot_price=24500.0, symbol="NIFTY")
        self.assertGreaterEqual(margin, 0.05 * 24500.0 * 75)

        # 3. Verify simulate() carries forward last_close across a missing monitoring minute
        # and backtest_jobs._options() dispatches arguments in the right order
        contracts = [
            {"instrument_key": "NFO|CE24500", "trading_symbol": "NIFTY24500CE", "expiry": "2026-10-22", "instrument_type": "CE", "strike_price": 24500, "lot_size": 75},
            {"instrument_key": "NFO|PE24500", "trading_symbol": "NIFTY24500PE", "expiry": "2026-10-22", "instrument_type": "PE", "strike_price": 24500, "lot_size": 75},
        ]
        # Build 1-minute bars from 09:19 to 09:25, deliberately omitting 09:22 on the PE leg
        spot_series = {f"2026-10-16 09:{m:02d}": [24500, 24510, 24490, 24500, 1000] for m in range(19, 26)}
        ce_series = {f"2026-10-16 09:{m:02d}": [120, 125, 118, 121, 500] for m in range(20, 26)}
        pe_series = {f"2026-10-16 09:{m:02d}": [115, 118, 112, 114, 500] for m in range(20, 26) if m != 22}
        series = {
            "NSE_INDEX|Nifty 50": spot_series,
            "NFO|CE24500": ce_series,
            "NFO|PE24500": pe_series,
        }
        settings = {
            "symbol": "NIFTY",
            "start": "2026-10-16",
            "end": "2026-10-16",
            "entry": "09:20",
            "exit": "09:25",
            "template": "Short straddle",
            "slip_bps": 5.0,
            "stop": 50000.0,
            "target": 50000.0,
            "use_upstox_fees": True,
        }
        trades, skips = backtest_jobs._options({
            "settings": settings,
            "contracts": contracts,
            "series": series,
            "signals": None,
        })
        self.assertEqual(len(trades), 1)
        self.assertEqual(len(skips), 0)


class TestTrack4UIAndCloudCaching(unittest.TestCase):
    """Track 4: UI/UX, Streamlit Cloud IST Refresh Policy & Background Refresh tests."""

    def test_refresh_policy_utc_to_ist_normalization(self):
        # 04:30 UTC is 10:00 IST (inside 09:15-15:40 IST live market session!)
        utc_morning = datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc)  # Friday
        self.assertTrue(refresh_policy.refresh_due(utc_morning, result={"ok": True}, elapsed=65.0))
        self.assertFalse(refresh_policy.refresh_due(utc_morning, result={"ok": True}, elapsed=15.0))

        # 14:00 UTC is 19:30 IST (outside both live 09:15-15:40 and EOD 16:30-18:30 windows)
        utc_night = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)
        self.assertFalse(refresh_policy.refresh_due(utc_night, result=None, elapsed=999.0))

    def test_background_refresh_ttl_and_atomic_collect(self):
        state = {}
        self.assertTrue(background_refresh.is_stale(state, "quotes", ttl=60.0))
        submitted = background_refresh.submit(state, "quotes", lambda x: x + 1, 41)
        self.assertTrue(submitted)
        # Wait briefly for worker completion
        time.sleep(0.05)
        ok, val = background_refresh.take(state, "quotes")
        self.assertTrue(ok)
        self.assertEqual(val, 42)
        # Now state should NOT be stale within the 60s TTL window
        self.assertFalse(background_refresh.is_stale(state, "quotes", ttl=60.0))
        self.assertTrue(background_refresh.is_stale(state, "quotes", ttl=0.0))




class TestTrack5SecondaryModulesAndClassicModernUI(unittest.TestCase):
    """Track 5: Secondary Modules (google_archive_sync, futures_view_cache, option_context,
    options_context_cache, web_server), requirements.txt, and Classic & Modern UI Theme Engine.
    """

    def test_futures_view_cache_no_st_ino_and_defensive_copy(self):
        import futures_view_cache
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sample.txt"
            p.write_text("hello", encoding="utf-8")
            rev = futures_view_cache.file_revision(p)
            self.assertEqual(len(rev), 2)  # (st_mtime_ns, st_size) without st_ino

            rev1 = futures_view_cache.rolling_revision(tmp, "NIFTY")
            rev2 = futures_view_cache.rolling_revision(tmp, "NIFTY")
            self.assertEqual(rev1, rev2)
            self.assertEqual(rev1[0], "missing")

    def test_option_context_configurable_150_iv_ceiling(self):
        from types import SimpleNamespace
        sys.modules.setdefault("history_policy", SimpleNamespace(compatible=lambda path, val: [val], register=lambda *a, **kw: None))
        import option_context
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "iv.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE expiry_observations (symbol TEXT, date TEXT, expiry TEXT, iv REAL, spread REAL, source TEXT, config TEXT)"
                )
                conn.execute("INSERT INTO expiry_observations VALUES ('NIFTY', '2026-10-15', '2026-10-22', 95.0, 0.2, 'Upstox supplied IV (Google Cloud)', 'v1')")
                conn.execute("INSERT INTO expiry_observations VALUES ('NIFTY', '2026-10-15', '2026-10-22', 250.0, 0.2, 'Upstox supplied IV (Google Cloud)', 'v1')")
            res = option_context.recorded_context(db_path, "NIFTY", "v1")
            self.assertEqual(len(res), 1)
            self.assertAlmostEqual(float(res["IV"].iloc[0]), 95.0, places=4)
            del conn, res
            import gc; gc.collect()

    def test_options_context_cache_lock_free_disk_read(self):
        import options_context_cache
        from types import SimpleNamespace
        sys.modules["corporate_event_calendar"] = SimpleNamespace(
            _read_cache=lambda p: {"sources": {"rbi": {"events": [{"Date": "2026-10-20", "Event": "RBI MPC", "Category": "Macro", "Source": "RBI"}]}}},
            _filter=lambda rows, s, e: rows,
            _bank_results=lambda rows: rows,
            _index_macro_events=lambda rows: rows,
            automatic_events=lambda root, sym, s, e: ([], "Ready"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache = options_context_cache.CalendarCache(fetcher=lambda r, s, st, en: ([], "Ready"))
            rows, status = cache.get(tmp, "NIFTY", "2026-10-01", "2026-10-31")
            self.assertTrue(len(rows) >= 1)
            self.assertEqual(rows[0]["Event"], "RBI MPC")
            cache.close()

    def test_ui_theme_figure_styling_and_session_badge(self):
        import importlib.util
        ui_p = Path(__file__).resolve().parent / "ui_theme.py"
        if not ui_p.exists():
            ui_p = Path(__file__).resolve().parent / "Volatility Lab" / "ui_theme.py"
        spec = importlib.util.spec_from_file_location("ui_theme", ui_p)
        ui_theme = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui_theme)
        badge_live = ui_theme.market_session_badge_html(datetime(2026, 10, 16, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata")))
        self.assertIn("NSE LIVE", badge_live)
        badge_closed = ui_theme.market_session_badge_html(datetime(2026, 10, 18, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
        self.assertIn("NSE CLOSED", badge_closed)

        called = {}
        class DummyFig:
            layout = SimpleNamespace(margin=SimpleNamespace(l=10, r=10, t=20, b=15))
            data = []
            def update_layout(self, **kw):
                called["layout"] = kw
            def update_xaxes(self, **kw):
                called["xaxes"] = kw
            def update_yaxes(self, **kw):
                called["yaxes"] = kw
        fig = DummyFig()
        ui_theme.style_plotly_figure(fig)
        self.assertIn("layout", called)
        self.assertGreaterEqual(called["layout"]["margin"]["t"], 38)
        self.assertEqual(called["xaxes"]["griddash"], "dot")

    def test_requirements_txt_valid_pypi_specifiers(self):
        req_path = Path(__file__).resolve().parent / "requirements.txt"
        if req_path.exists():
            req_text = req_path.read_text(encoding="utf-8")
            self.assertNotIn("streamlit==1.64.0", req_text)
            self.assertNotIn("plotly==7.1.0", req_text)
            self.assertNotIn("pypdf==6.20.1", req_text)

    def test_data_consistency_and_sync_integrity(self):
        from expiry_data import load_snapshot
        from smile import load_smile, has_usable_points
        from market_table import atm_values

        DATA = Path(__file__).resolve().parent / "Volatility Lab" / "data"
        snap = load_snapshot("", allow_saved=True)
        self.assertIsNotNone(snap)
        for r in snap.get("rows", []):
            self.assertIsNotNone(r.get("DTE"), f"Missing DTE in {r.get('Instrument')} {r.get('Expiry')}")
            self.assertGreater(float(r["DTE"]), 0)

        r0 = snap["rows"][0]
        self.assertIsNotNone(r0.get("Month future LTP"))
        self.assertIsNotNone(r0.get("Synthetic future"))
        diff = abs(float(r0["Synthetic future"]) - float(r0["Month future LTP"]))
        self.assertLess(diff, 100.0)

        # Guard against dict config in load_smile
        smile_dict = load_smile(DATA / "monitor.sqlite", "NIFTY", r0["Expiry"], {"dict": 1}, r0.get("As of"))
        self.assertIsNotNone(smile_dict)
        self.assertTrue(has_usable_points(smile_dict))

        # Test single-sided option liquidity
        dummy_chain = pd.DataFrame([
            {"strike": 25050.0, "option_type": "CE", "iv": 0.155},
            {"strike": 25050.0, "option_type": "PE", "iv": 0.0}
        ])
        c_iv, p_iv, atm_iv = atm_values(dummy_chain, [25050.0], 25050.0)
        self.assertEqual(atm_iv, 15.5)

    def test_straddle_net_delta_neutral_and_atm_selection(self):
        if "history_policy" in sys.modules and not hasattr(sys.modules["history_policy"], "__file__"): del sys.modules["history_policy"]
        from restored_lab import compute_straddle_greeks
        from expiry_data import load_snapshot

        # 1. Mathematical neutrality of true ATM straddles
        nifty_greeks = compute_straddle_greeks(spot=25030.35, strike=25050.0, dte=3.0, vol_pct=16.0)
        self.assertIsNotNone(nifty_greeks)
        self.assertAlmostEqual(nifty_greeks['net_delta'], 0.0, delta=0.05)
        self.assertAlmostEqual(nifty_greeks['call_delta'], 0.50, delta=0.05)
        self.assertAlmostEqual(nifty_greeks['put_delta'], -0.50, delta=0.05)

        banknifty_greeks = compute_straddle_greeks(spot=59086.0, strike=59100.0, dte=17.0, vol_pct=14.0)
        self.assertIsNotNone(banknifty_greeks)
        self.assertAlmostEqual(banknifty_greeks['net_delta'], 0.0, delta=0.15)

        # 2. Document the stale strike failure mode: deep ITM call causes net delta ~ 1.0
        stale_greeks = compute_straddle_greeks(spot=25030.35, strike=23950.0, dte=3.0, vol_pct=16.0)
        self.assertGreater(stale_greeks['net_delta'], 0.90)

        # 3. Snapshot loader sanitization guarantees ATM strike matches spot
        snap = load_snapshot({}, allow_saved=True)
        self.assertIsNotNone(snap)
        for r in snap.get('rows', []):
            sym = r.get('Instrument')
            price = r.get('Price')
            atm = r.get('ATM strike')
            step = 50.0 if sym == 'NIFTY' else 100.0
            if price and atm:
                self.assertLessEqual(abs(float(atm) - float(price)), step * 1.5)


    def test_v4_vectorized_intraday_greeks_and_garch_horizon_cache(self):
        from types import SimpleNamespace
        sys.modules["plotly"] = SimpleNamespace(graph_objects=SimpleNamespace(Figure=object, Scatter=object))
        sys.modules["plotly.graph_objects"] = SimpleNamespace(Figure=object, Scatter=object)
        sys.modules["streamlit"] = SimpleNamespace(
            cache_data=lambda *a, **k: (lambda f: f),
            cache_resource=lambda *a, **k: (lambda f: f),
            fragment=lambda *a, **k: (lambda f: f),
            session_state={},
        )
        sys.modules["trade_review_ui"] = SimpleNamespace(table=lambda *a, **k: None)
        import restored_lab as rl
        import expiry_forecast as ef
        prices = np.array([24400.0, 24450.0, 24500.0])
        strikes = np.array([24400.0, 24450.0, 24500.0])
        dtes = np.array([6.0, 6.0, 6.0])
        ivs = np.array([14.1, 14.3, 14.5])
        vec_s, vec_d = rl._compute_straddle_series_vectorized(prices, strikes, dtes, ivs)
        for idx in range(len(prices)):
            g = rl.compute_straddle_greeks(prices[idx], strikes[idx], dtes[idx], ivs[idx])
            self.assertAlmostEqual(float(vec_s[idx]), float(g["straddle"]), places=10)
            self.assertAlmostEqual(float(vec_d[idx]), float(g["net_delta"]), places=10)
        s1 = ef.sessions("2026-11-01", "2026-11-30")
        s2 = ef.sessions("2026-11-01", "2026-11-30")
        self.assertEqual(s1, s2)


    def test_v5_six_trade_scenario_presets_and_signal_filters(self):
        import strategy_backtest as sb
        self.assertEqual(len(sb.SCENARIO_PRESETS), 6)
        for scen_name, meta in sb.SCENARIO_PRESETS.items():
            self.assertIn(scen_name, sb.TEMPLATES)
            legs = sb.template(scen_name)
            self.assertTrue(len(legs) >= 2)
            for leg in legs:
                self.assertIn("Side", leg)
                self.assertIn("Type", leg)
                self.assertIn("Offset", leg)
                self.assertIn("Ratio", leg)
                self.assertIn("side", leg)
                self.assertIn("kind", leg)
            info = sb.scenario_preset(scen_name)
            self.assertTrue(bool(info["hypothesis"]))
            self.assertTrue(bool(info["streamlit_triggers"]))
            self.assertTrue(sb.validate({
                "name": scen_name,
                "symbol": "NIFTY",
                "start": "2024-10-01",
                "end": "2024-10-31",
                "entry": info["entry"],
                "exit": info["exit"],
                "lots": 1,
                "dte": info["dte"],
            }))


    def test_v6_any_device_network_binding_and_responsive_theme(self):
        cfg_path = Path(__file__).resolve().parent / ".streamlit" / "config.toml"
        if cfg_path.exists():
            cfg_txt = cfg_path.read_text(encoding="utf-8")
            self.assertIn('address = "0.0.0.0"', cfg_txt)
            self.assertIn("headless = true", cfg_txt)
        import importlib.util
        ui_p = Path(__file__).resolve().parent / "ui_theme.py"
        if not ui_p.exists():
            ui_p = Path(__file__).resolve().parent / "Volatility Lab" / "ui_theme.py"
        spec = importlib.util.spec_from_file_location("ui_theme", ui_p)
        ui_theme = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui_theme)
        self.assertTrue(hasattr(ui_theme, "get_lan_ip"))
        self.assertTrue(hasattr(ui_theme, "render_any_device_access_popover"))
        self.assertIn("@media (max-width: 768px)", ui_theme.CLASSIC_MODERN_CSS)


    def test_v7_end_to_end_backtest_with_monitor_sqlite_cloud_fallback_and_header_actions(self):
        import backtest_engine as be
        import backtest_signals as bs
        import strategy_backtest as sb
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            tmp_data.mkdir(parents=True, exist_ok=True)
            mon_db = tmp_data / "monitor.sqlite"
            con = sqlite3.connect(mon_db)
            try:
                con.execute(
                    "CREATE TABLE intraday_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT)"
                )
                for minute, spot_px, iv_val in [("09:15:00", 24450.0, 14.5), ("09:19:00", 24455.0, 14.4), ("09:20:00", 24460.0, 14.2), ("15:15:00", 24452.0, 13.6)]:
                    payload = {
                        "Instrument": "NIFTY",
                        "Expiry": "2026-11-12",
                        "Price": spot_px,
                        "IV": iv_val,
                        "Call IV": iv_val,
                        "Put IV": iv_val,
                        "IV-RV Spread": 2.6,
                        "HV30": 11.6,
                        "25 Delta reversal": 2.8,
                        "As of": f"2026-11-06T{minute}+05:30",
                        "Retrieved at": f"2026-11-06T{minute}+05:30",
                    }
                    con.execute(
                        "INSERT INTO intraday_observations VALUES (?, ?, ?, ?, ?)",
                        ("NIFTY", "2026-11-12", "cfg1", payload["As of"], json.dumps(payload)),
                    )
                con.commit()
            finally:
                con.close()
            orig_data_dir, orig_root, orig_sig_path = be.DATA_DIR, be.ROOT, bs.PATH
            try:
                be.DATA_DIR = tmp_data
                be.ROOT = tmp_data / "backtest_1minute"
                bs.PATH = mon_db
                contracts, series, conflicts = be.load("NIFTY", "2026-11-06", "2026-11-06")
                self.assertTrue(len(contracts) > 0)
                self.assertIn("NSE_INDEX|Nifty 50", series)
                signals = bs.load_signals("NIFTY")
                self.assertTrue(len(signals) >= 4)
                scen = "Scenario 1 · VRP Harvest Iron Condor (Calm HV)"
                info = sb.scenario_preset(scen)
                settings = {
                    "name": scen,
                    "template": scen,
                    "symbol": "NIFTY",
                    "start": "2026-11-06",
                    "end": "2026-11-06",
                    "entry": info["entry"],
                    "exit": info["exit"],
                    "lots": 1,
                    "dte": info["dte"],
                    "legs": sb.template(scen),
                    "filters": info["filters"],
                }
                trades, skips = sb.simulate(settings, contracts, series, signals)
                self.assertEqual(len(trades), 1, f"Expected 1 executed trade, got skips={skips.to_dict('records')}")
                self.assertIn("Net", trades.columns)
            finally:
                be.DATA_DIR, be.ROOT, bs.PATH = orig_data_dir, orig_root, orig_sig_path


    def test_v8_year_long_offline_end_to_end_audit(self):
        import smile
        import relative_value as rv
        import market_calendar as mc
        import expiry_forecast as ef
        from datetime import date

        # 1. Verify 2027 and 2028 calendar horizon works without CalendarHorizonError
        d_2027 = mc.last_trading_day_on_or_before(date(2027, 11, 15))
        self.assertLessEqual(d_2027, date(2027, 11, 15))
        s_2027 = ef.sessions("2027-01-01", "2027-12-31")
        self.assertGreater(len(s_2027), 235)
        s_2028 = ef.sessions("2028-01-01", "2028-03-31")
        self.assertGreater(len(s_2028), 55)

        # 2. Verify smile.load_smile (with config fallback) and smile.load_latest_smile
        with tempfile.TemporaryDirectory() as tmp:
            db_p = Path(tmp) / "monitor.sqlite"
            payload = {
                "version": smile.VERSION,
                "points": [{"Point": "ATM", "IV": 14.25, "Source": "Upstox", "Method": "Exact", "Strikes": "24500"}],
                "metrics": {"25 RR · put minus call": 2.4, "25 BF · wings minus ATM": 0.65},
            }
            smile.save_smile(db_p, "NIFTY", "2027-01-28", "cfg_old", "2027-01-15T15:30:00+05:30", payload, capture_source="Google Drive")
            # Exact config load
            s_exact = smile.load_smile(db_p, "NIFTY", "2027-01-28", "cfg_old", "2027-01-15T15:30:00+05:30")
            self.assertIsNotNone(s_exact)
            # Fallback config load (when rate/dividend config hash updates over the next year)
            s_fallback = smile.load_smile(db_p, "NIFTY", "2027-01-28", "cfg_new", "2027-01-15T15:30:00+05:30")
            self.assertIsNotNone(s_fallback)
            # Latest smile load
            s_latest = smile.load_latest_smile(db_p, "NIFTY", "2027-01-28", "cfg_new")
            self.assertIsNotNone(s_latest)
            self.assertEqual(s_latest.get("as_of"), "2027-01-15T15:30:00+05:30")

            # 3. Verify relative_value.capture, relative_value.history, and dual-mode maturity_stats
            snap = {
                "config": "cfg_new",
                "rows": [
                    {
                        "Instrument": "NIFTY",
                        "Expiry": f"2027-01-{10 + i:02d}",
                        "As of": f"2027-01-05T15:40:00+05:30",
                        "DTE": 5,
                        "IV": 13.0 + 0.2 * i,
                        "IV-RV Spread": 1.5 + 0.1 * i,
                        "25 Delta reversal": 2.0 + 0.05 * i,
                        "Source": "Upstox supplied IV (Google Cloud)",
                    }
                    for i in range(15)
                ],
            }
            count = rv.capture(db_p, snap)
            self.assertEqual(count, 15)
            hist_df = rv.history(db_p, "NIFTY", "cfg_new", comparable=True)
            self.assertEqual(len(hist_df), 15)
            # UI 3-arg call: maturity_stats(saved, current, metric)
            m_ui = rv.maturity_stats(hist_df, snap["rows"][-1], "IV-RV Spread")
            self.assertEqual(m_ui["Status"], "Ready")
            self.assertIsNotNone(m_ui["Percentile"])
            # Unit test 3-arg call: maturity_stats(saved, current, bounds=...)
            m_bounds = rv.maturity_stats(hist_df, snap["rows"][-1], bounds=(0, 7, 15, 35, 65, 120, 366))
            self.assertEqual(m_bounds["status"], "Ready")
            self.assertTrue(np.isfinite(m_bounds["percentile"]))



if __name__ == "__main__":
    unittest.main(verbosity=2)
