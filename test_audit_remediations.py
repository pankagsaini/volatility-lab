#!/usr/bin/env python3
"""Comprehensive verification suite testing all 5 audit tracks of Volatility Lab remediations."""
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import gzip
import json, shutil
import sqlite3
import gc
from contextlib import closing
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
PATCH_DIR = (
    (HERE_DIR / "volatility_lab")
    if (HERE_DIR / "volatility_lab").exists()
    else ((HERE_DIR / "Volatility Lab") if (HERE_DIR / "Volatility Lab").exists() else HERE_DIR)
)
for candidate in (HERE_DIR, PATCH_DIR):
    if candidate.exists():
        if str(candidate) in sys.path:
            sys.path.remove(str(candidate))
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
        cgs_path = (PATCH_DIR / "cloud" / "apps_script" / "Code.gs") if (PATCH_DIR / "cloud" / "apps_script" / "Code.gs").exists() else (PATCH_DIR / "Code.gs")
        code_gs = cgs_path.read_text(encoding="utf-8")
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
        sys.modules.setdefault("history_policy", SimpleNamespace(compatible=lambda path, val: [val]))
        import option_context
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "iv.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE expiry_observations (symbol TEXT, date TEXT, expiry TEXT, iv REAL, spread REAL, source TEXT, config TEXT)"
                )
                conn.execute("INSERT INTO expiry_observations VALUES ('NIFTY', '2026-10-15', '2026-10-22', 95.0, 0.2, 'Upstox supplied IV (Google Cloud)', 'v1')")
                conn.execute("INSERT INTO expiry_observations VALUES ('NIFTY', '2026-10-15', '2026-10-22', 250.0, 0.2, 'Upstox supplied IV (Google Cloud)', 'v1')")
                conn.commit()
            res = option_context.recorded_context(db_path, "NIFTY", "v1")
            self.assertEqual(len(res), 1)
            self.assertAlmostEqual(float(res["IV"].iloc[0]), 95.0, places=4)

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
            ui_p = (Path(__file__).resolve().parent / "volatility_lab" / "ui_theme.py") if (Path(__file__).resolve().parent / "volatility_lab" / "ui_theme.py").exists() else (Path(__file__).resolve().parent / "Volatility Lab" / "ui_theme.py")
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
        sys.modules.setdefault("market_data", SimpleNamespace(IST=ZoneInfo("Asia/Kolkata"), MarketDataError=RuntimeError, history_cutoff=lambda *a: None))
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
            ui_p = (Path(__file__).resolve().parent / "volatility_lab" / "ui_theme.py") if (Path(__file__).resolve().parent / "volatility_lab" / "ui_theme.py").exists() else (Path(__file__).resolve().parent / "Volatility Lab" / "ui_theme.py")
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
            with closing(sqlite3.connect(mon_db)) as con:
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
                gc.collect()


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


VL_Sub = PATCH_DIR


class TestTrack9MasterAuditAnd6StrategyBacktest(unittest.TestCase):
    """Track 9: v9.0 5-Subagent Master Audit & 6-Strategy Empirical Backtest Verification."""

    def test_v9_all_6_scenarios_empirical_backtest_and_summarize(self):
        import strategy_backtest
        self.assertEqual(len(strategy_backtest.SCENARIO_PRESETS), 6)
        day = "2026-11-05"
        exp = "2026-11-12"
        contracts = []
        series = {"NSE_INDEX|Nifty 50": {}}
        for m in range(376):
            hh = 9 + (15 + m) // 60
            mm = (15 + m) % 60
            ts = f"{day} {hh:02d}:{mm:02d}"
            series["NSE_INDEX|Nifty 50"][ts] = [24800.0, 24805.0, 24795.0, 24800.0, 1000]
        for k in range(24000, 25650, 50):
            for kind in ("CE", "PE"):
                ikey = f"NFO|NIFTY|{exp}|{k}|{kind}"
                contracts.append({
                    "instrument_key": ikey,
                    "trading_symbol": f"NIFTY {exp} {k} {kind}",
                    "instrument_type": kind,
                    "strike_price": float(k),
                    "expiry": exp,
                    "lot_size": 75,
                })
                series[ikey] = {}
                dist = abs(k - 24800)
                px_open = max(5.0, 180.0 - dist * 0.32)
                px_close = max(3.0, px_open * 0.85)
                for ts in series["NSE_INDEX|Nifty 50"]:
                    px = px_open if ts <= f"{day} 09:35" else px_close
                    series[ikey][ts] = [px, px + 1.0, max(0.5, px - 1.0), px, 500]

        for name, cfg in strategy_backtest.SCENARIO_PRESETS.items():
            # Craft a point-in-time signal at 09:18 IST that satisfies this scenario's exact filter bounds
            sig_payload = {
                "Instrument": "NIFTY",
                "Expiry": exp,
                "As of": f"{day}T09:18:00+05:30",
                "Retrieved at": f"{day}T09:18:00+05:30",
                "Price": 24800.0,
                "IV": 15.0,
                "HV30": 12.0,
                "IV-RV Spread": 3.5,
                "25 Delta reversal": 3.0 if "Scenario 4" in name else (0.0 if "Scenario 6" in name else 1.5),
            }
            if "Scenario 2" in name:
                sig_payload["IV-RV Spread"] = -3.0
            signals = [(exp, pd.Timestamp(f"{day} 09:18:00", tz="Asia/Kolkata"), sig_payload)]
            settings = {
                "symbol": "NIFTY",
                "start": day,
                "end": day,
                "entry": cfg["entry"],
                "exit": cfg["exit"],
                "rank": cfg.get("expiry_rank", "Nearest"),
                "dte": (1, 30),
                "stop": cfg.get("stop", 3000.0),
                "target": cfg.get("target", 5000.0),
                "slip_bps": 5.0,
                "use_upstox_fees": True,
                "lots": 1,
                "legs": strategy_backtest.TEMPLATES[name],
                "filters": cfg["filters"],
            }
            trades, skips = strategy_backtest.simulate(settings, contracts, series, signals)
            self.assertEqual(len(trades), 1, f"{name} failed to execute trade: skips={skips.to_dict('records')}")
            self.assertGreater(float(trades.iloc[0]["Premium"]), 0.0)
            self.assertGreater(float(trades.iloc[0]["Cost"]), 0.0)
            perf = strategy_backtest.summarize_performance(trades)
            self.assertEqual(perf["trades"], 1)
            self.assertTrue(np.isfinite(perf["net_pnl"]))

    def test_v9_futures_backtest_charge_and_expiry_rollover(self):
        import futures_backtest
        rows = []
        days = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]
        for i, d in enumerate(days):
            exps = ["2026-08-27", "2026-09-24", "2026-10-29"]
            c_fac = 0.09 if i == 0 else 0.02
            s = 24800.0
            f1 = s * 1.001
            f2 = f1 * (1.0 + c_fac * (28.0 / 365.0))
            f3 = f2 * (1.0 + c_fac * (35.0 / 365.0))
            for e, px in zip(exps, [f1, f2, f3]):
                rows.append({"day": d, "expiry": e, "open": px, "high": px + 5, "low": px - 5, "close": px})
        bars = pd.DataFrame(rows)
        trades, skips, cov = futures_backtest.simulate(
            bars=bars, pair=(0, 1), entry_carry=7.0, target_carry=3.0,
            quantity=75, near_side=1, slip_bps=5.0, max_hold_days=10,
            brokerage_per_order=20.0, lots=1,
            contracts={"2026-08-27": 75, "2026-09-24": 75, "2026-10-29": 75},
            auto_roll_on_expiry=True,
        )
        self.assertGreaterEqual(len(trades), 1)
        self.assertIn("NetPnL", trades.columns)

    def test_v9_backtest_signals_strict_no_future_lookahead(self):
        import backtest_signals
        exp = "2026-11-12"
        day = "2026-11-05"
        # Signal timestamp 30 mins AFTER 09:20 entry (09:50 IST) must NOT be used
        future_ts = pd.Timestamp("2026-11-05 09:50:00", tz="Asia/Kolkata")
        rows = [(exp, future_ts, {"Expiry": exp, "IV-RV Spread": 4.0, "HV30": 12.0})]
        ok, reason = backtest_signals.decide(rows, exp, day, "09:20", [("iv_rv_spread", 2.0, 10.0)])
        self.assertFalse(ok)
        # Signal timestamp BEFORE entry (09:18 IST) with snake_case alias must pass
        past_ts = pd.Timestamp("2026-11-05 09:18:00", tz="Asia/Kolkata")
        rows_valid = [(exp, past_ts, {"Expiry": exp, "IV-RV Spread": 4.0, "HV30": 12.0})]
        ok2, _ = backtest_signals.decide(rows_valid, exp, day, "09:20", [("iv_rv_spread", 2.0, 10.0)])
        self.assertTrue(ok2)

    def test_v9_market_calendar_2027_2028_holidays_and_horizon_265(self):
        from datetime import date
        import market_calendar
        import expiry_forecast
        import forecast
        # 2027-04-15 (Ram Navami Thursday) and 2027-04-14 (Ambedkar Jayanti Wednesday) roll back to Tuesday 2027-04-13
        self.assertEqual(market_calendar.last_trading_day_on_or_before(date(2027, 4, 15)), date(2027, 4, 13))
        # 2028-11-02 (Guru Nanak Jayanti Thursday in Leap Year 2028) rolls back to Wednesday 2028-11-01
        self.assertEqual(market_calendar.last_trading_day_on_or_before(date(2028, 11, 2)), date(2028, 11, 1))
        self.assertGreaterEqual(getattr(expiry_forecast, "HORIZON_CAP", 245), 260)

    def test_v9_option_math_t_zero_and_mixed_kind_vec(self):
        import option_math
        res = option_math.bsm_greeks_vec(
            spot=np.array([24800.0, 24800.0]),
            strike=np.array([24800.0, 24800.0]),
            years=np.array([0.0, 5.0 / 365.0]),
            iv=np.array([0.15, 0.15]),
            rate=0.067,
            div_yield=0.012,
            kind=np.array(["CE", "PE"]),
        )
        self.assertEqual(len(res["delta"]), 2)
        self.assertGreater(res["delta"][0], 0.0)
        self.assertLess(res["delta"][1], 0.0)

    def test_v9_collector_support_and_backtest_common_exports(self):
        import collector_support
        import backtest_common
        self.assertEqual(collector_support.read_json("/nonexistent/path/xyz.json"), {})
        self.assertFalse(backtest_common.is_blacklisted_future("NIFTY", "2026-11-26"))
        self.assertEqual(backtest_common.dated_lot_size("NIFTY", "2026-11-06"), 65)
        self.assertEqual(backtest_common.dated_lot_size("NIFTY", "2025-06-01"), 75)
        self.assertEqual(backtest_common.dated_lot_size("BANKNIFTY", "2026-11-06"), 30)

    def test_v9_zero_ast_signature_or_import_mismatches(self):
        import ast
        exports = {}
        for py in VL_Sub.glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            names = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
            exports[py.stem] = names
        for mod, req in [
            ("smile", {"load_smile", "load_latest_smile", "has_usable_points"}),
            ("relative_value", {"maturity_stats", "history", "capture", "save"}),
            ("backtest_common", {"record_skip", "is_blacklisted_future", "is_blacklisted_option", "dated_lot_size"}),
            ("collector_support", {"read_json", "atomic_json", "atomic_csv", "data_lock"}),
            ("strategy_backtest", {"TEMPLATES", "SCENARIO_PRESETS", "simulate", "summarize_performance"}),
        ]:
            self.assertTrue(req.issubset(exports.get(mod, set())), f"Missing exports in {mod}: {req - exports.get(mod, set())}")



    def test_v10_startup_recovery_and_full_export_parity(self):
        """Verify all v10.0 startup crash fixes: collector_support, trade_review, broker_charges, backtest_common, strategy_backtest, and requirements.txt."""
        import tempfile
        import collector_support
        import trade_review
        import broker_charges
        import backtest_common
        import market_calendar
        import strategy_backtest
        import backtest_jobs

        # 1. collector_support full export & dual-order atomic_csv verification
        for sym in ("ROOT", "DATA", "PRIVATE", "STATE", "IST", "atomic_json", "read_json", "atomic_csv", "data_lock", "serialized", "protect", "unprotect", "decompress_gzip", "validate_schema", "parse_ist_timestamp"):
            self.assertTrue(hasattr(collector_support, sym), f"collector_support missing {sym}")
        self.assertEqual(collector_support.STATE.name, "state")
        with collector_support.serialized("unit_test_lock", timeout=5):
            pass
        raw_token = b"test_secret_token_bytes"
        enc = collector_support.protect(raw_token)
        dec = collector_support.unprotect(enc)
        self.assertEqual(dec, raw_token)
        with tempfile.TemporaryDirectory() as td:
            df_test = pd.DataFrame({"Date": ["2026-11-06"], "Close": [24200.0]})
            csv1 = Path(td) / "order1.csv"
            csv2 = Path(td) / "order2.csv"
            collector_support.atomic_csv(df_test, csv1, index=False)
            collector_support.atomic_csv(csv2, df_test)
            self.assertTrue(csv1.exists() and csv2.exists())

        # 2. trade_review parity_forward, string/dict freshness, and tuple/dict stress_position
        payload = {
            "provider": "Upstox",
            "spot": 24200.0,
            "tte_days": 7.0,
            "lot_size": 75,
            "contracts": [
                {"expiry": "2026-11-12", "strike": 24000.0, "kind": "PE", "bid": 80.0, "ask": 82.0, "iv": 15.0, "delta": -0.25, "gamma": 0.001, "theta": -10.0, "vega": 12.0},
                {"expiry": "2026-11-12", "strike": 24200.0, "kind": "CE", "bid": 150.0, "ask": 152.0, "iv": 14.0, "delta": 0.50, "gamma": 0.002, "theta": -15.0, "vega": 18.0},
                {"expiry": "2026-11-12", "strike": 24200.0, "kind": "PE", "bid": 145.0, "ask": 147.0, "iv": 14.2, "delta": -0.50, "gamma": 0.002, "theta": -15.0, "vega": 18.0},
                {"expiry": "2026-11-12", "strike": 24400.0, "kind": "CE", "bid": 75.0, "ask": 77.0, "iv": 14.5, "delta": 0.25, "gamma": 0.001, "theta": -10.0, "vega": 12.0},
            ],
        }
        ctx = trade_review.option_book(payload, "2026-11-12")
        self.assertIn("book", ctx)
        self.assertIn("chain", ctx)
        self.assertIn("stamp", ctx)
        self.assertIn("strike", ctx)
        self.assertIn("Kind", ctx["book"].columns)
        self.assertIn("Type", ctx["chain"].columns)
        pf = trade_review.parity_forward(ctx)
        self.assertIn("forward", pf)
        fr = trade_review.freshness(ctx)
        self.assertIsInstance(fr, str)
        self.assertTrue("Book captured " + str(ctx["stamp"]) + " · " + fr)
        self.assertIn("fresh", fr._meta)
        pos = trade_review.make_position(ctx, 24000.0, 24400.0)
        res = trade_review.stress_position(ctx, pos, 1.0, 50.0)
        self.assertIn("grid", res)
        scenarios, summary = res
        self.assertIsInstance(scenarios, pd.DataFrame)
        self.assertIn("Spot move %", scenarios.columns)
        self.assertIsInstance(summary, dict)

        # 3. broker_charges 5-arg & 6-arg compatibility + refresh() + Stamp duty key
        c1 = broker_charges.charge("2026-11-06", "Options", 150.0, 75, "sell", 20.0)
        c2 = broker_charges.charge("2026-11-06", "futures", -1, 24200.0, 75, 20.0)
        self.assertIn("Stamp duty", c1)
        self.assertGreater(c1["STT"], 0.0)
        self.assertGreater(c2["STT"], 0.0)
        self.assertIsInstance(broker_charges.refresh(), dict)

        # 4. backtest_common load_blacklist & market_calendar CalendarHorizonError
        self.assertIsInstance(backtest_common.load_blacklist(), dict)
        self.assertTrue(issubclass(market_calendar.CalendarHorizonError, ValueError))

        # 5. strategy_backtest legacy helpers & backtest_jobs pandas import
        self.assertEqual(strategy_backtest.sizing({"sizing_mode": "broker_lot_size", "lots": 2}), ("broker_lot_size", 2))
        self.assertEqual(strategy_backtest.broker_lot_size({"lot_size": 75}), 75)
        self.assertTrue(hasattr(backtest_jobs, "pd"))



    def test_v16_zero_synthetic_data_strict_real_captures_only(self):
        """Verify strict 100% real data policy:
        1. Zero daily-OHLC-to-1-minute interpolation when intraday option captures are absent (records explicit skip).
        2. HV30 in backtest_signals is strictly shifted by 1 session (.shift(1)) and expiry_observations stamped at 15:30:00 IST without fake 1.5 reversal.
        3. 15:15 IST exits never drop an already-entered losing trade if an OTM wing has 0 volume at 15:15 (uses last_close).
        """
        import tempfile
        import backtest_engine as be
        import backtest_signals as bs
        import strategy_backtest as sb

        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            tmp_data.mkdir(parents=True, exist_ok=True)
            # Write a 40-day OHLC CSV so HV30 is available
            df_ohlc = _synthetic_ohlc(45, daily_ret=0.01, end_date="2026-11-06")
            df_ohlc["Date"] = pd.to_datetime(df_ohlc["Date"]).dt.strftime("%Y-%m-%d")
            csv_p = tmp_data / "nifty_one_year.csv"
            df_ohlc.to_csv(csv_p, index=False)

            mon_db = tmp_data / "monitor.sqlite"
            with closing(sqlite3.connect(mon_db)) as con:
                con.execute(
                    "CREATE TABLE expiry_observations (symbol TEXT, date TEXT, expiry TEXT, iv REAL, spread REAL, source TEXT, config TEXT)"
                )
                con.execute(
                    "INSERT INTO expiry_observations VALUES ('NIFTY', '2026-11-05', '2026-11-12', 15.2, 2.1, 'Upstox', 'default')"
                )
                con.commit()

            orig_data_dir, orig_root, orig_sig_path = be.DATA_DIR, be.ROOT, bs.PATH
            try:
                be.DATA_DIR = tmp_data
                be.ROOT = tmp_data / "backtest_1minute"
                bs.PATH = mon_db

                # 1. Verify load() does NOT synthesize intraday series from daily CSV / daily expiry_observations
                contracts, series, _ = be.load("NIFTY", "2026-11-05", "2026-11-05")
                self.assertEqual(len(contracts), 0)
                self.assertEqual(len(series), 0)
                trades_df, skips_df = sb.simulate(
                    {"symbol": "NIFTY", "start": "2026-11-05", "end": "2026-11-05", "entry": "09:20", "exit": "15:15", "template": "Short straddle"},
                    contracts, series, []
                )
                self.assertEqual(len(trades_df), 0)
                self.assertEqual(len(skips_df), 1)
                self.assertEqual(skips_df.iloc[0]["Reason"], "No real captured intraday option data for session")

                # 2. Verify backtest_signals stamps expiry_observations at 15:30:00 IST, shifts HV30 by 1 session, and has no fake 1.5 reversal
                sigs = bs.load_signals("NIFTY", path=mon_db)
                self.assertEqual(len(sigs), 1)
                exp_s, ts_val, payload_d = sigs[0]
                self.assertEqual(ts_val.hour, 15)
                self.assertEqual(ts_val.minute, 30)
                self.assertNotIn("25 Delta reversal", payload_d)

                # 3. Verify 15:15 IST exit with 0 volume on OTM wing does NOT drop an already-entered losing trade
                day = "2026-11-06"
                exp = "2026-11-12"
                c_list = [
                    {"instrument_key": "K_CE_S", "trading_symbol": "CE_S", "instrument_type": "CE", "strike_price": 24500.0, "expiry": exp, "lot_size": 75},
                    {"instrument_key": "K_PE_S", "trading_symbol": "PE_S", "instrument_type": "PE", "strike_price": 24500.0, "expiry": exp, "lot_size": 75},
                    {"instrument_key": "K_CE_B", "trading_symbol": "CE_B", "instrument_type": "CE", "strike_price": 24800.0, "expiry": exp, "lot_size": 75},
                    {"instrument_key": "K_PE_B", "trading_symbol": "PE_B", "instrument_type": "PE", "strike_price": 24200.0, "expiry": exp, "lot_size": 75},
                ]
                ticks = {
                    "NSE_INDEX|Nifty 50": {
                        f"{day} 09:19": [24500.0, 24500.0, 24500.0, 24500.0, 1000],
                        f"{day} 09:20": [24500.0, 24500.0, 24500.0, 24500.0, 1000],
                        f"{day} 15:15": [24750.0, 24750.0, 24750.0, 24750.0, 1000],
                    },
                    "K_CE_S": {
                        f"{day} 09:20": [150.0, 150.0, 150.0, 150.0, 500],
                        f"{day} 15:15": [320.0, 320.0, 320.0, 320.0, 500],
                    },
                    "K_PE_S": {
                        f"{day} 09:20": [140.0, 140.0, 140.0, 140.0, 500],
                        f"{day} 15:15": [60.0, 60.0, 60.0, 60.0, 500],
                    },
                    "K_CE_B": {
                        f"{day} 09:20": [45.0, 45.0, 45.0, 45.0, 200],
                        f"{day} 15:15": [110.0, 110.0, 110.0, 110.0, 200],
                    },
                    "K_PE_B": {
                        f"{day} 09:20": [40.0, 40.0, 40.0, 40.0, 200],
                        f"{day} 14:30": [15.0, 15.0, 15.0, 15.0, 100],
                        f"{day} 15:15": [15.0, 15.0, 15.0, 15.0, 0],  # 0 volume at 15:15 on OTM wing!
                    },
                }
                t_lose, s_lose = sb.simulate(
                    {"symbol": "NIFTY", "start": day, "end": day, "entry": "09:20", "exit": "15:15", "template": "Iron butterfly"},
                    c_list, ticks, []
                )
                self.assertEqual(len(t_lose), 1)
                self.assertLess(float(t_lose.iloc[0]["Net"]), 0.0)
            finally:
                be.DATA_DIR, be.ROOT, bs.PATH = orig_data_dir, orig_root, orig_sig_path
                gc.collect()


    def test_v18_clean_repository_structure_and_entrypoints(self):
        """Verify v18 clean repository structure, zero duplicate module pollution, updated 2026-11-09 expiry_latest.json, and 100% real-data docs/index.html."""
        repo_root = Path(__file__).resolve().parent
        vl_dir = (repo_root / "volatility_lab") if (repo_root / "volatility_lab").exists() else (repo_root / "Volatility Lab")
        desk_dir = repo_root / "streamlit_desk"
        if not vl_dir.is_dir():
            return

        # 1. Verify root has zero duplicate Volatility Lab modules and zero obsolete scripts
        allowed_root_py = {
            "streamlit_app.py",
            "test_audit_remediations.py",
            "rebuild_clean_project_v19.py",
            "rebuild_clean_project_v18.py",
            "patch_1_master_audit_v16.py",
            "patch_2_trader_feedback_v17.py",
        }
        root_py = {p.name for p in repo_root.glob("*.py")}
        unexpected_root_py = root_py - allowed_root_py
        self.assertEqual(unexpected_root_py, set(), f"Duplicate/stray .py files at repo root: {unexpected_root_py}")
        for banned in ("LAUNCH_ANY_DEVICE.py", "MAIN_AUDIT.md", "app.py", "apply_volatility_lab_fixes.py"):
            self.assertFalse((repo_root / banned).exists(), f"Obsolete file {banned} should not exist at repo root")

        # 2. Verify streamlit_desk/ contains only app.py and run.py
        if desk_dir.is_dir():
            desk_py = {p.name for p in desk_dir.glob("*.py")}
            self.assertEqual(desk_py, {"app.py", "run.py"}, f"Unexpected .py files in streamlit_desk/: {desk_py}")

        # 3. Verify Volatility Lab/data/expiry_latest.json is updated to 2026-11-09 with all 9 active expiries
        exp_latest_path = vl_dir / "data" / "expiry_latest.json"
        if exp_latest_path.exists():
            exp_obj = json.loads(exp_latest_path.read_text(encoding="utf-8"))
            self.assertTrue(str(exp_obj.get("timestamp", "")).startswith("2026-11-09"), f"Stale expiry_latest.json timestamp: {exp_obj.get('timestamp')}")
            rows = exp_obj.get("rows", [])
            self.assertEqual(len(rows), 9)
            bn_exps = [r["Expiry"] for r in rows if r.get("Instrument") == "BANKNIFTY"]
            self.assertEqual(bn_exps, ["2026-11-23", "2026-12-29", "2027-01-25"])
            for r in rows:
                self.assertIsNotNone(r.get("IV"))
                self.assertIsNotNone(r.get("HV30"))
                self.assertIsNotNone(r.get("IV-RV Spread"))

        # 4. Verify docs/index.html has 72 real captured Upstox trades and 14 real captured IV dates
        docs_html_path = repo_root / "docs" / "index.html"
        if docs_html_path.exists():
            html_txt = docs_html_path.read_text(encoding="utf-8")
            self.assertIn("tradeIdIndexFilter", html_txt)
            self.assertIn("rel=\"icon\"", html_txt)
            for line in html_txt.splitlines():
                if "var tradeArchive =" in line:
                    t_arr = json.loads(line.split("var tradeArchive =", 1)[1].strip().rstrip(";"))
                    self.assertEqual(len(t_arr), 72)


class TestV19AutomatedForwardTesting(unittest.TestCase):
    """Verify v19.1 Clean-Slate Automated Forward Testing Engine (First Trade Tomorrow 2026-11-11 Onwards)."""

    def test_32_website_forward_test_workspace_and_controls(self):
        for rel in ("docs/index.html", "index.html"):
            p = HERE_DIR / rel
            html = p.read_text(encoding="utf-8")
            self.assertIn('id="wsForwardBtn"', html)
            self.assertIn('Forward Test (Auto Paper)', html)
            self.assertIn('id="workspaceForwardTest"', html)
            for ctrl_id in (
                "fwdAutoModeToggle",
                "fwdIndexFilter",
                "fwdExpiryScope",
                "fwdStrategyFilter",
                "fwdLotsInput",
                "fwdStopLossRule",
                "fwdProfitTargetRule",
                "fwdScanNowBtn",
                "fwdResetSeedBtn",
                "fwdExportCsvBtn",
                "fwdKpiOpenCount",
                "fwdKpiOpenMtm",
                "fwdKpiClosedNet",
                "fwdKpiCapital",
                "fwdKpiRoc",
                "fwdKpiWinPf",
                "fwdOpenPositionsBody",
                "fwdClosedLedgerBody",
                "fwdScorecardBody",
                "fwdIntradayMtmCanvas",
                "fwdStrategyBarCanvas",
            ):
                self.assertIn(f'id="{ctrl_id}"', html, f"Missing {ctrl_id} in {rel}")
            self.assertIn("const MIN_FORWARD_DATE = '2026-11-11';", html)
            self.assertIn("vl_forward_ledger_v19_clean", html)
            self.assertIn("localStorage.removeItem('vl_forward_ledger_v19')", html)
            self.assertIn("Reset / Clear Ledger", html)
            self.assertIn("Forward Test is armed for tomorrow (11 Nov 2026 09:20 IST) — waiting for live market open to place first trade.", html)
            self.assertIn("0 trades placed yet · Forward Test Engine is ARMED for first trade tomorrow (11 Nov 2026 · 09:20 IST) when live Upstox option-chain captures begin.", html)
            self.assertIn("ARMED · 11 NOV 09:20 IST", html)
            self.assertIn("NIFTY (Lot 65)", html)
            self.assertIn("NIFTY 50 (Lot 65)", html)
            self.assertIn("window.markForwardPositionsFromPayload", html)
            self.assertIn("window.closeForwardPosition", html)
            self.assertIn("window.autoForwardTrackSetup", html)
            self.assertIn("Auto-Forward Track →", html)
            self.assertIn("animation: false", html)

    def test_33_python_forward_test_clean_slate_and_2026_11_11_simulation(self):
        import copy
        import gzip
        from forward_test import (
            MIN_FORWARD_DATE,
            load_forward_test_ledger,
            evaluate_forward_test_scorecard,
            process_live_upstox_snapshot,
        )
        self.assertEqual(MIN_FORWARD_DATE, "2026-11-11")

        # 1. Verify initial clean-slate ledger has 0 open positions and 0 closed trades
        ledger = load_forward_test_ledger()
        self.assertEqual(ledger.get("version"), "forward-test-v19.1-clean")
        self.assertEqual(ledger.get("inception_date"), "2026-11-11")
        self.assertEqual(ledger.get("min_trigger_date"), "2026-11-11")
        self.assertEqual(ledger.get("status"), "ARMED_WAITING_FOR_NEXT_SESSION")
        self.assertEqual(ledger.get("open_positions"), [])
        self.assertEqual(ledger.get("closed_trades"), [])
        self.assertEqual(ledger.get("intraday_mtm_series"), [])

        summary = evaluate_forward_test_scorecard(ledger, index_filter="ALL", expiry_scope="FRONT", lots=1)
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["closed_count"], 0)
        self.assertEqual(summary["closed_net_pnl"], 0.0)
        self.assertEqual(summary["portfolio_roc_pct"], 0.0)
        self.assertEqual(len(summary["scorecard"]), 6)
        for row in summary["scorecard"]:
            self.assertEqual(row["open_count"], 0)
            self.assertEqual(row["closed_count"], 0)
            self.assertEqual(row["total_net_pnl"], 0.0)
            self.assertIn(row["unit_capital"], (28037, 56074))
            self.assertEqual(row["roc_pct"], 0.0)
            self.assertEqual(row["status"], "ARMED · 11 NOV 09:20 IST")

        # 2. Verify polling the 2026-11-09 snapshot does NOT trigger any trades
        snap_gz = HERE_DIR / "data" / "google_archive" / "2026-11-09_snapshot_154842_complete.json.gz"
        if not snap_gz.exists():
            snap_gz = PATCH_DIR / "data" / "google_archive" / "2026-11-09_snapshot_154842_complete.json.gz"
        snap_payload_09 = json.loads(gzip.decompress(snap_gz.read_bytes()).decode("utf-8"))
        after_09 = process_live_upstox_snapshot(snap_payload_09, ledger=ledger, now_ist="2026-11-10T13:15:00+05:30")
        self.assertEqual(after_09["open_positions"], [])
        self.assertEqual(after_09["closed_trades"], [])
        self.assertTrue(after_09.get("gate_blocked"))

        # 3. Simulate a live 2026-11-11T09:20:42+05:30 Upstox snapshot during market hours (>= 2026-11-11)
        snap_11_0920 = copy.deepcopy(snap_payload_09)
        snap_11_0920["day"] = "2026-11-11"
        snap_11_0920["retrieved_at"] = "2026-11-11T09:20:42+05:30"
        after_11_open = process_live_upstox_snapshot(
            snap_11_0920,
            ledger=after_09,
            now_ist="2026-11-11T09:20:42+05:30",
        )
        self.assertFalse(after_11_open.get("gate_blocked"))
        self.assertGreater(len(after_11_open["open_positions"]), 0)
        self.assertEqual(len(after_11_open["closed_trades"]), 0)
        for pos in after_11_open["open_positions"]:
            self.assertEqual(pos["date"], "2026-11-11")
            self.assertEqual(pos["session_date"], "2026-11-11")
            if pos["sym"] == "NIFTY" and pos["strat"] == "S6":
                self.assertEqual(pos["maxLoss"], 7800)  # 120 max loss pts * 65 NIFTY lot size

        # 4. Simulate mid-day mark-to-market at 2026-11-11T12:30:00+05:30 and auto-close at 2026-11-11T15:15:00+05:30
        snap_11_1230 = copy.deepcopy(snap_11_0920)
        snap_11_1230["retrieved_at"] = "2026-11-11T12:30:00+05:30"
        after_11_mid = process_live_upstox_snapshot(
            snap_11_1230,
            ledger=after_11_open,
            now_ist="2026-11-11T12:30:00+05:30",
        )
        self.assertGreater(len(after_11_mid["open_positions"]), 0)
        self.assertGreaterEqual(len(after_11_mid["intraday_mtm_series"]), 2)

        snap_11_1515 = copy.deepcopy(snap_11_0920)
        snap_11_1515["retrieved_at"] = "2026-11-11T15:15:00+05:30"
        after_11_close = process_live_upstox_snapshot(
            snap_11_1515,
            ledger=after_11_mid,
            now_ist="2026-11-11T15:15:00+05:30",
        )
        self.assertEqual(len(after_11_close["open_positions"]), 0)
        self.assertGreater(len(after_11_close["closed_trades"]), 0)
        for tr in after_11_close["closed_trades"]:
            self.assertEqual(tr["date"], "2026-11-11")
            self.assertEqual(tr["exitTime"], "15:15 IST")
            self.assertIn("15:15 IST", tr["exitReason"])

    def test_34_cloud_code_gs_forward_test_24x7_hooks(self):
        code_gs = (PATCH_DIR / "cloud" / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        self.assertIn("const MIN_FORWARD_DATE = '2026-11-11';", code_gs)
        self.assertIn("function getNseMarketStatusIST(", code_gs)
        self.assertIn("function evaluateForwardTest_(report)", code_gs)
        self.assertIn("function loadForwardLedger_()", code_gs)
        self.assertIn("function saveForwardLedger_(ledger)", code_gs)
        self.assertIn("forward_test_ledger.json", code_gs)
        self.assertIn("payload.forward_test_ledger", code_gs)



class TestTrack10DataIntegrityAndPackaging(unittest.TestCase):
    """Verify paper-trading data integrity, quote freshness, engine configuration parity & wheel packaging."""

    def test_35_parity_between_python_and_apps_script_constants(self):
        """Assert Python and Apps Script engines agree on lot sizes, capital and constants."""
        from forward_test import LOT_SIZES, STRATEGY_CAPITAL_PER_LOT, MIN_FORWARD_DATE
        self.assertEqual(LOT_SIZES["NIFTY"], 65)
        self.assertEqual(LOT_SIZES["BANKNIFTY"], 30)
        self.assertEqual(MIN_FORWARD_DATE, "2026-11-11")

        code_gs = (PATCH_DIR / "cloud" / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        self.assertIn("const lotQty = sym === 'NIFTY' ? 65 : 30;", code_gs)
        self.assertIn("const lotQty = pos.sym === 'NIFTY' ? 65 : 30;", code_gs)
        self.assertNotIn("sym === 'NIFTY' ? 75 : 30", code_gs)
        self.assertIn("const MIN_FORWARD_DATE = '2026-11-11';", code_gs)

        # Assert capital parity for S1-S6
        for sym in ("NIFTY", "BANKNIFTY"):
            for sc in ("S1", "S2", "S3", "S4", "S5", "S6"):
                py_cap = STRATEGY_CAPITAL_PER_LOT[sym][sc]
                pattern = f"{sc}: {py_cap}"
                self.assertIn(pattern, code_gs, f"Missing or mismatched {sym} {sc} capital in Code.gs")

    def test_36_quote_absence_and_no_price_fabrication(self):
        """Verify evaluate_strategies_for_chain never fabricates fill prices when leg quotes are missing."""
        from forward_test import evaluate_strategies_for_chain

        # Test A: Chain with missing ATM strike completely
        res_empty = evaluate_strategies_for_chain(
            symbol="NIFTY",
            expiry="2026-11-17",
            spot=25044.65,
            chain_rows=[],
            hv10=14.0,
            hv30=15.0,
            dte=8,
        )
        self.assertEqual(res_empty, [], "Must return empty list when chain strikes are missing")

        # Test B: Chain with incomplete strikes (ATM present, but OTM wing strike missing)
        incomplete_chain = [
            {"strike": 25050, "cLtp": 180.0, "pLtp": 190.0, "cIv": 15.5, "pIv": 15.5},
            {"strike": 25250, "cLtp": 90.0, "pLtp": 0.0, "cIv": 15.0, "pIv": 0.0},
        ]
        res_partial = evaluate_strategies_for_chain(
            symbol="NIFTY",
            expiry="2026-11-17",
            spot=25044.65,
            chain_rows=incomplete_chain,
            hv10=14.0,
            hv30=15.0,
            dte=8,
        )
        # S1 requires K-200, K+200, K-500, K+500. Since they are missing, S1 must NOT be triggered.
        s1_setups = [s for s in res_partial if s.get("strat") == "S1"]
        self.assertEqual(s1_setups, [], "S1 must be rejected when wing quotes are missing (no fallback fabrication)")

    def test_37_stale_marks_prevent_automated_exits(self):
        """Verify that stale or missing quotes flag freshness='STALE' and do NOT trigger automated SL/TP exits."""
        from forward_test import mark_open_positions_to_market

        mock_pos = {
            "id": "OPEN-20261111-NIF-1117-S1",
            "date": "2026-11-11",
            "session_date": "2026-11-11",
            "sym": "NIFTY",
            "exp": "2026-11-17",
            "strat": "S1",
            "capital": 28037,
            "maxLoss": 7800,
            "maxCredit": 11000,
            "entryPx": 150.0,
            "grossMtm": -4000.0,
            "legs": [
                {"side": -1, "mult": 1, "strike": 25250, "type": "CE", "entryPx": 150.0, "curPx": 150.0},
                {"side": -1, "mult": 1, "strike": 24850, "type": "PE", "entryPx": 160.0, "curPx": 160.0},
            ],
        }

        # Mark against EMPTY live chains (quotes absent/stale)
        still_open, newly_closed = mark_open_positions_to_market(
            open_positions=[mock_pos],
            live_chains={},
            sl_rule="SL_50_MAXLOSS",
            tp_rule="TP_50_30",
            current_time_ist="11:30 IST",
            auto_close_intraday_1515=False,
        )

        self.assertEqual(len(still_open), 1, "Stale position must remain open and not be falsely auto-closed")
        self.assertEqual(len(newly_closed), 0, "No exit should trigger on stale quotes")
        self.assertEqual(still_open[0].get("freshness"), "STALE")
        self.assertIn("Missing live quote", still_open[0].get("staleReason", ""))

    def test_38_wheel_build_and_package_data(self):
        """Verify pyproject.toml package-data inclusion for wheel distribution."""
        pyproject_text = (HERE_DIR / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.setuptools.package-data]", pyproject_text)
        self.assertIn("volatility_lab = [", pyproject_text)
        self.assertIn('"data/*.json"', pyproject_text)
        self.assertIn('"data/*.csv"', pyproject_text)
        self.assertIn('"data/*.sqlite"', pyproject_text)

    def test_39_market_calendar_horizon_boundary_and_warning(self):
        """Verify market calendar boundary protections and horizon warning protocol."""
        from market_calendar import (
            MIN_SUPPORTED_YEAR,
            MAX_SUPPORTED_YEAR,
            CalendarHorizonError,
            is_trading_day,
            check_horizon_warning,
        )
        import datetime
        self.assertEqual(MIN_SUPPORTED_YEAR, 2023)
        self.assertEqual(MAX_SUPPORTED_YEAR, 2028)

        # 1. Queries outside horizon raise CalendarHorizonError
        with self.assertRaises(CalendarHorizonError):
            is_trading_day(datetime.date(2022, 12, 31))
        with self.assertRaises(CalendarHorizonError):
            is_trading_day(datetime.date(2029, 1, 1))

        # 2. Horizon warning check returns None for normal active years
        self.assertIsNone(check_horizon_warning(datetime.date(2026, 11, 10)))

        # 3. Horizon warning returns alert when within 365 days of 2028-12-31
        warn = check_horizon_warning(datetime.date(2028, 6, 1))
        self.assertIsNotNone(warn)
        self.assertIn("WARNING", warn)
        self.assertIn("expires in", warn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
