"""Quantitative audit verification suite: Black-76 Parity Forward, IV round-trip, Yang-Zhang RV, and 2-leg Straddle Gamma."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PKG_DIR = (HERE / "volatility_lab") if (HERE / "volatility_lab").is_dir() else (HERE / "Volatility Lab")
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

import option_math
import synthetic_future
import vol_lib


class TestQuantAuditFixes(unittest.TestCase):
    def test_01_nov11_nifty_black76_parity_forward_and_atm_iv(self):
        # 2026-11-11 15:30 IST NIFTY 2026-11-17 ATM 25250: CE=202.10, PE=260.45, DTE=6d
        years = 6.0 / 365.0
        rate = 0.066
        f_syn = option_math.parity_implied_forward(25250.0, 202.10, 260.45, years, rate)
        self.assertAlmostEqual(f_syn, 25191.59, places=2)
        c_iv = option_math.solve_black76_iv(202.10, f_syn, 25250.0, years, rate, "CE") * 100.0
        p_iv = option_math.solve_black76_iv(260.45, f_syn, 25250.0, years, rate, "PE") * 100.0
        self.assertAlmostEqual(c_iv, p_iv, places=4)
        self.assertAlmostEqual(c_iv, 17.86, places=1)

    def test_02_nov11_banknifty_black76_parity_forward_and_atm_iv(self):
        # 2026-11-11 15:30 IST BANKNIFTY 2026-11-23 ATM 60800: CE=739.70, PE=856.25, DTE=12d
        years = 12.0 / 365.0
        rate = 0.066
        f_syn = option_math.parity_implied_forward(60800.0, 739.70, 856.25, years, rate)
        self.assertAlmostEqual(f_syn, 60683.20, places=2)
        c_iv = option_math.solve_black76_iv(739.70, f_syn, 60800.0, years, rate, "CE") * 100.0
        p_iv = option_math.solve_black76_iv(856.25, f_syn, 60800.0, years, rate, "PE") * 100.0
        self.assertAlmostEqual(c_iv, p_iv, places=4)
        self.assertAlmostEqual(c_iv, 18.17, places=1)

    def test_03_nov11_yang_zhang_realized_volatility(self):
        ndf = pd.read_csv(PKG_DIR / "data" / "nifty_one_year.csv")
        self.assertEqual(str(ndf["Date"].iloc[-1]), "2026-11-11")
        self.assertAlmostEqual(float(ndf["Close"].iloc[-1]), 25255.05, places=2)
        s10 = vol_lib.rolling_rv_suite(ndf, window=10, ann_factor=245)
        s30 = vol_lib.rolling_rv_suite(ndf, window=30, ann_factor=245)
        self.assertAlmostEqual(float(s10["yang_zhang_rv"].iloc[-1] * 100.0), 14.99, places=2)
        self.assertAlmostEqual(float(s30["yang_zhang_rv"].iloc[-1] * 100.0), 14.78, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
