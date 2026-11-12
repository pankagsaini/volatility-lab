"""Execution safety & lot-size schedule verification suite (NIFTY=65, BANKNIFTY=30)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = (HERE / "volatility_lab") if (HERE / "volatility_lab").is_dir() else (HERE / "Volatility Lab")
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import backtest_common
import broker_charges
import forward_test
import strategy_backtest


class TestExecutionSafetyFixes(unittest.TestCase):
    def test_01_nse_revised_lot_sizes_nifty_65_banknifty_30(self):
        self.assertEqual(backtest_common.dated_lot_size("NIFTY", "2026-11-11"), 65)
        self.assertEqual(backtest_common.dated_lot_size("BANKNIFTY", "2026-11-11"), 30)
        self.assertEqual(backtest_common.dated_lot_size("NIFTY", "2024-01-15"), 50)
        self.assertEqual(backtest_common.dated_lot_size("BANKNIFTY", "2024-01-15"), 15)

    def test_02_forward_test_charges_use_65_and_30_lots(self):
        legs = [
            {"side": -1, "mult": 1, "strike": 25250, "type": "CE", "entryPx": 202.10, "curPx": 180.0},
            {"side": -1, "mult": 1, "strike": 25250, "type": "PE", "entryPx": 260.45, "curPx": 240.0},
        ]
        chg_n = forward_test.compute_position_charges("2026-11-11", "NIFTY", legs, lots=1, include_exit=True)
        chg_b = forward_test.compute_position_charges("2026-11-11", "BANKNIFTY", legs, lots=1, include_exit=True)
        self.assertGreater(chg_n, chg_b)
        self.assertGreater(chg_n, 150.0)

    def test_03_forward_only_gate_blocks_pre_nov11_and_off_hours(self):
        res_old = forward_test.process_live_upstox_snapshot(
            {"day": "2026-11-09", "records": []},
            now_ist="2026-11-09T10:00:00+05:30",
        )
        self.assertTrue(res_old.get("gate_blocked"))
        res_closed = forward_test.process_live_upstox_snapshot(
            {"day": "2026-11-11", "records": []},
            now_ist="2026-11-11T18:00:00+05:30",
        )
        self.assertTrue(res_closed.get("gate_blocked"))

    def test_04_saved_2026_11_11_forward_ledger_integrity(self):
        ledger = forward_test.load_forward_test_ledger(PKG_DIR / "data")
        self.assertEqual(ledger.get("inception_date"), "2026-11-11")
        self.assertEqual(len(ledger.get("closed_trades", [])), 13)
        self.assertEqual(len(ledger.get("open_positions", [])), 0)
        for tr in ledger["closed_trades"]:
            self.assertEqual(tr["date"], "2026-11-11")
            self.assertEqual(tr["exitTime"], "15:15 IST")


if __name__ == "__main__":
    unittest.main(verbosity=2)
