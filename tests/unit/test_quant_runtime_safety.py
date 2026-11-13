import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "volatility_lab"))

import option_math
import strategy_backtest


class Black76BoundaryTests(unittest.TestCase):
    def test_spot_alias_uses_intrinsic_for_expired_scalar_and_vector_lanes(self):
        scalar = option_math.black76_greeks(
            strike=100, years=-0.25, rate=0.05, vol=0.2, kind="CE", spot=100
        )
        self.assertEqual(scalar["price"], 0.0)
        self.assertEqual(scalar["delta"], 0.5)
        self.assertEqual(scalar["gamma"], 0.0)

        vector = option_math.black76_greeks_vec(
            spot=100,
            strike=[90, 110],
            years=[-0.1, 0.0],
            rate=0.05,
            vol=[0.2, 0.2],
            kind=["CE", "PE"],
        )
        np.testing.assert_allclose(vector["price"], [10.0, 10.0])
        np.testing.assert_allclose(vector["delta"], [1.0, -1.0])
        np.testing.assert_allclose(vector["gamma"], [0.0, 0.0])

    def test_zero_volatility_positive_time_is_deterministic_and_finite_except_kink_gamma(self):
        out = option_math.black76_greeks(
            forward=100, strike=100, years=0.25, rate=0.05, vol=0.0, kind="CE"
        )
        self.assertEqual(out["price"], 0.0)
        self.assertAlmostEqual(out["delta"], 0.5 * math.exp(-0.05 * 0.25))
        self.assertTrue(math.isnan(out["gamma"]))
        self.assertGreater(out["vega"], 0.0)


class ChainQuoteValidationTests(unittest.TestCase):
    def setUp(self):
        self.as_of = "2026-11-11T10:00:00+05:30"
        years = 30.0 / 365.0
        call = option_math.black76_greeks(100, 100, years, 0.05, 0.2, "CE")["price"]
        put = option_math.black76_greeks(100, 100, years, 0.05, 0.2, "PE")["price"]
        self.rows = pd.DataFrame([
            {
                "expiry": "2026-12-11", "strike": 100, "option_type": "CE",
                "ltp": 999.0, "bid_price": call - 0.01, "ask_price": call + 0.01,
                "quote_timestamp": self.as_of,
            },
            {
                "expiry": "2026-12-11", "strike": 100, "option_type": "PE",
                "ltp": 999.0, "bid_price": put - 0.01, "ask_price": put + 0.01,
                "quote_timestamp": self.as_of,
            },
        ])

    def test_fresh_mid_quotes_drive_iv_instead_of_ltp(self):
        out = option_math.black76_chain_greeks(
            self.rows, years=30.0 / 365.0, rate=0.05,
            expiry="2026-12-11", as_of=self.as_of,
        )
        np.testing.assert_allclose(out["iv_b76"].to_numpy(), [0.2, 0.2], atol=0.002)

    def test_unvalidated_or_stale_quotes_do_not_produce_iv(self):
        no_reference = option_math.black76_chain_greeks(
            self.rows, forward=100, years=30.0 / 365.0, rate=0.05,
        )
        self.assertTrue(no_reference["iv_b76"].isna().all())

        stale = self.rows.copy()
        stale["quote_timestamp"] = "2026-11-11T09:00:00+05:30"
        out = option_math.black76_chain_greeks(
            stale, forward=100, years=30.0 / 365.0, rate=0.05,
            expiry="2026-12-11", as_of=self.as_of,
        )
        self.assertTrue(out["iv_b76"].isna().all())

    def test_string_epoch_timestamps_and_expiry_scope_are_checked(self):
        epoch_rows = self.rows.copy()
        epoch = str(int(pd.Timestamp(self.as_of).timestamp()))
        epoch_rows["quote_timestamp"] = epoch
        out = option_math.black76_chain_greeks(
            epoch_rows, forward=100, years=30.0 / 365.0, rate=0.05,
            expiry="2026-12-11", as_of=self.as_of,
        )
        self.assertTrue(np.isfinite(out["iv_b76"]).all())

        mixed = pd.concat([
            self.rows,
            self.rows.assign(expiry="2026-12-18"),
        ], ignore_index=True)
        no_expiry_selection = option_math.black76_chain_greeks(
            mixed, forward=100, years=30.0 / 365.0, rate=0.05, as_of=self.as_of,
        )
        self.assertTrue(no_expiry_selection["iv_b76"].isna().all())


class BacktestAssumptionAndRiskMetricTests(unittest.TestCase):
    def test_invalid_slippage_and_margin_inputs_are_rejected(self):
        base = {"start": "2026-11-11", "end": "2026-11-11"}
        for key, value in (("slip_bps", -1), ("slip_bps", float("nan")), ("margin_per_lot", -1)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    strategy_backtest.validate({**base, key: value})

    def test_sharpe_sortino_use_daily_returns_and_zero_return_sessions(self):
        trades = pd.DataFrame([
            {"Date": "2026-11-11", "Net": 10.0, "Capital required": 1000.0},
            {"Date": "2026-11-13", "Net": -20.0, "Capital required": 1000.0},
        ])
        result = strategy_backtest.summarize_performance(trades, annualization=1.0)
        daily_returns = np.array([0.01, 0.0, -0.02])
        expected_sharpe = daily_returns.mean() / daily_returns.std(ddof=1)
        expected_downside = np.sqrt(np.mean(np.square(np.minimum(daily_returns, 0.0))))
        expected_sortino = daily_returns.mean() / expected_downside
        self.assertAlmostEqual(result["sharpe"], expected_sharpe)
        self.assertAlmostEqual(result["sortino"], expected_sortino)
        self.assertEqual(result["risk_metric_version"], "daily-capital-return-v2")


if __name__ == "__main__":
    unittest.main()
