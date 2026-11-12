"""Runtime safety verification suite: offline HTML fallbacks, NaN/Inf guards, and SQLite schema integrity."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
PKG_DIR = (HERE / "volatility_lab") if (HERE / "volatility_lab").is_dir() else (HERE / "Volatility Lab")
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import option_math


class TestQuantRuntimeSafety(unittest.TestCase):
    def test_01_docs_html_prepopulated_black76_and_offline_ledger(self):
        for rel in ("docs/index.html", "index.html"):
            p = HERE / rel
            self.assertTrue(p.exists(), f"Missing {rel}")
            html = p.read_text(encoding="utf-8")
            self.assertIn("11 Nov 15:30 IST · MARKET CLOSED", html)
            self.assertIn('"b76AtmIv":17.86', html)
            self.assertIn('"b76AtmIv":18.17', html)
            self.assertIn("const DEFAULT_FORWARD_LEDGER =", html)
            self.assertIn("CLOUD LEDGER · SYNCED (11 NOV 15:30 IST)", html)
            self.assertNotIn("Live data unavailable: cloud request failed.", html)
            self.assertIn("NIFTY (Lot 65)", html)

    def test_02_expiry_latest_and_sqlite_updated_to_2026_11_11(self):
        exp_p = PKG_DIR / "data" / "expiry_latest.json"
        exp_obj = json.loads(exp_p.read_text(encoding="utf-8"))
        self.assertTrue(str(exp_obj["timestamp"]).startswith("2026-11-11"))
        self.assertEqual(len(exp_obj["rows"]), 9)
        self.assertAlmostEqual(float(exp_obj["india_vix"]["value"]), 17.54, places=2)

        with sqlite3.connect(str(PKG_DIR / "data" / "upstox" / "research.sqlite")) as con:
            rows = con.execute("SELECT instrument, close FROM candles WHERE stamp LIKE '2026-11-11%'").fetchall()
            closes = dict(rows)
            self.assertAlmostEqual(closes["NSE_INDEX|Nifty 50"], 25255.05, places=2)
            self.assertAlmostEqual(closes["NSE_INDEX|Nifty Bank"], 60792.40, places=2)
            self.assertAlmostEqual(closes["NSE_INDEX|India VIX"], 17.54, places=2)

    def test_03_black76_nan_and_zero_tte_runtime_safety(self):
        g_nan = option_math.black76_greeks(float("nan"), 25250.0, 0.02, 0.066, 0.18, "CE")
        self.assertTrue(np.isnan(g_nan["price"]))
        g_exp = option_math.black76_greeks(25300.0, 25250.0, 0.0, 0.066, 0.18, "CE")
        self.assertAlmostEqual(g_exp["price"], 50.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
