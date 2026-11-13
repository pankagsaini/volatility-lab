"""Focused, synthetic regressions for confirmed quantitative audit defects."""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'volatility_lab'))

import backtest_signals
import broker_charges
import data_accuracy
import forward_test
import option_math
import quote_quality
import strategy_backtest
import synthetic_future
import vol_lib


class TestQuantAuditFixes(unittest.TestCase):
    def test_yang_zhang_uses_open_to_close_variance(self):
        opens = np.array([100.0, 111.0, 107.0, 122.0, 118.0])
        closes = np.array([104.0, 109.0, 115.0, 119.0, 124.0])
        highs = np.maximum(opens, closes) + 2.0
        lows = np.minimum(opens, closes) - 2.0
        frame = pd.DataFrame({
            'Date': pd.date_range('2026-01-05', periods=len(opens), freq='B'),
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
        })
        window, annualization = 3, 252
        result = vol_lib.rolling_rv_suite(frame, window=window, ann_factor=annualization)
        open_s, close_s = pd.Series(opens), pd.Series(closes)
        overnight = np.log(open_s / close_s.shift(1))
        intraday = np.log(close_s / open_s)
        high_s, low_s = pd.Series(highs), pd.Series(lows)
        rs = np.log(high_s / close_s) * np.log(high_s / open_s) + np.log(low_s / close_s) * np.log(low_s / open_s)
        k = 0.34 / (1.34 + (window + 1) / (window - 1))
        expected = (
            overnight.rolling(window).var(ddof=1).iloc[-1]
            + k * intraday.rolling(window).var(ddof=1).iloc[-1]
            + (1.0 - k) * rs.rolling(window).mean().iloc[-1]
        )
        actual = result['yang_zhang_rv'].iloc[-1] ** 2 / annualization
        self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual(vol_lib.YANG_ZHANG_VERSION, 'yang-zhang-2000-open-close-v3')

    def test_bsm_scalar_and_vector_keep_expiry_intrinsic_and_reject_invalid_inputs(self):
        scalar = option_math.bsm_greeks(105.0, 100.0, 0.0, 0.05, 0.0, 'CE')
        self.assertEqual(float(scalar['price']), 5.0)
        self.assertEqual(float(scalar['gamma']), 0.0)
        with self.assertRaises(ValueError):
            option_math.bsm_greeks(0.0, 100.0, 0.0, 0.05, 0.0, 'CE')
        with self.assertRaises(ValueError):
            option_math.bsm_greeks(100.0, 100.0, 0.25, 0.05, 0.0, 'CE')

        vector = option_math.bsm_greeks_vec(
            [105.0, 0.0, -1.0, np.nan, 100.0],
            [100.0] * 5,
            [0.0, 0.0, 0.0, 0.0, 0.25],
            vol=[0.0] * 5,
            kind=['CE'] * 5,
        )
        self.assertEqual(vector['price'][0], 5.0)
        self.assertEqual(vector['gamma'][0], 0.0)
        self.assertTrue(np.isnan(vector['price'][1:]).all())
        self.assertTrue(np.isnan(vector['delta'][1:]).all())

    def test_black76_zero_vol_is_valid_at_or_after_expiry(self):
        scalar = option_math.black76_greeks(105.0, 100.0, 0.0, 0.05, 0.0, 'CE')
        self.assertEqual(scalar['price'], 5.0)
        self.assertEqual(scalar['gamma'], 0.0)
        vector = option_math.black76_greeks_vec(
            [105.0, 95.0], [100.0, 100.0], [0.0, -1.0],
            rate=0.05, vol=[0.0, 0.0], kind=['CE', 'PE'],
        )
        np.testing.assert_allclose(vector['price'], [5.0, 5.0])
        np.testing.assert_allclose(vector['gamma'], [0.0, 0.0])

    def test_synthetic_future_withholds_nested_broker_chain_not_normalized_quotes(self):
        raw_chain = [{
            'expiry': '2026-11-17', 'strike_price': 25000,
            'call_options': {'market_data': {'ltp': 240.0}},
            'put_options': {'market_data': {'ltp': 210.0}},
        }]
        result = synthetic_future.calculate(
            raw_chain, spot=25000.0, years=7 / 365,
            expiry='2026-11-17', as_of='2026-11-11T09:20:00+05:30',
            spot_timestamp='2026-11-11T09:20:00+05:30',
        )
        self.assertIsNone(result['Synthetic future'])
        self.assertEqual(result['Synthetic status'], 'Normalized option quotes required')

    def test_quote_freshness_accepts_explicit_epoch_milliseconds(self):
        as_of = '2026-11-11T09:20:00+05:30'
        epoch_ms = int(pd.Timestamp(as_of).timestamp() * 1000)
        rows = [
            {'expiry': '2026-11-17', 'strike': 25000, 'option_type': 'CE',
             'bid': 239.0, 'ask': 241.0, 'timestamp': epoch_ms},
            {'expiry': '2026-11-17', 'strike': 25000, 'option_type': 'PE',
             'bid': 209.0, 'ask': 211.0, 'timestamp': epoch_ms},
        ]
        result = synthetic_future.calculate(
            rows, spot=25005.0, rate=.06, years=7 / 365, expiry='2026-11-17',
            as_of=as_of, spot_timestamp=epoch_ms,
        )
        self.assertIsNotNone(result['Synthetic future'])
        quote = {
            'timestamp': epoch_ms,
            'depth': {
                'buy': [{'price': 99.9, 'quantity': 10}],
                'sell': [{'price': 100.1, 'quantity': 10}],
            },
        }
        self.assertTrue(quote_quality.assess_quote(quote, as_of, max_spread=10.0)['accepted'])

    def test_independent_check_does_not_promote_raw_ltp_to_parity_input(self):
        timestamp = '2026-11-11T09:20:00+05:30'
        payload = {
            'spot': 25000.0,
            'broker_received_at': timestamp,
            'spot_quote': {'timestamp': timestamp},
            'broker_option_chain': [{
                'expiry': '2026-11-17', 'strike_price': 25000,
                'call_options': {'market_data': {'ltp': 240.0}},
                'put_options': {'market_data': {'ltp': 210.0}},
            }],
        }
        checks = data_accuracy.check_row(
            {'Expiry': '2026-11-17', 'Synthetic future': 25030.0}, payload, None,
        )
        synthetic = next(item for item in checks if item['Check'] == 'ATM synthetic future')
        self.assertEqual(synthetic['Result'], 'Not verifiable')

    def test_bsm_scalar_and_vector_agree_for_live_contracts(self):
        scalar = option_math.bsm_greeks(100.0, 105.0, 0.25, 0.04, 0.22, 'PE', dividend=0.01)
        vector = option_math.bsm_greeks_vec(
            100.0, 105.0, 0.25, vol=0.22, rate=0.04, dividend=0.01, kind='PE'
        )
        for scalar_key, vector_key in (
            ('price', 'price'), ('delta', 'delta'), ('gamma', 'gamma'),
            ('vega', 'vega'), ('theta_daily', 'theta'),
        ):
            self.assertAlmostEqual(float(scalar[scalar_key]), float(vector[vector_key]), places=12)

    def test_forward_session_gate_uses_the_central_holiday_calendar(self):
        # 2027-03-10 is a maintained NSE holiday but was absent from the old
        # duplicate forward-test list, which would have admitted a live entry.
        status = forward_test.get_nse_market_status_ist('2027-03-10T10:00:00+05:30')
        self.assertFalse(status['open'])
        self.assertIn('2027-03-10', forward_test.NSE_HOLIDAYS)

    def test_signal_loader_does_not_fabricate_eod_availability(self):
        connection = sqlite3.connect(':memory:')
        connection.execute(
            'CREATE TABLE intraday_observations (symbol TEXT, expiry TEXT, as_of TEXT, payload TEXT)'
        )
        connection.execute(
            'CREATE TABLE expiry_observations (date TEXT, symbol TEXT, expiry TEXT, config TEXT, iv REAL, spread REAL, source TEXT)'
        )
        connection.execute(
            'INSERT INTO expiry_observations VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('2026-11-09', 'NIFTY', '2026-11-17', 'test', 15.0, 3.0, 'Upstox'),
        )
        connection.commit()
        original_exists = Path.exists

        def exists(path):
            if str(path) == 'memory_signal_db':
                return True
            return original_exists(path)

        with patch.object(Path, 'exists', exists), patch.object(
            backtest_signals.sqlite3, 'connect', return_value=connection
        ):
            rows = backtest_signals.load_signals('NIFTY', path='memory_signal_db')
        self.assertEqual(rows, [])

    @staticmethod
    def _strategy_fixture(exit_volume=2, entry_volume=2):
        day = '2026-10-16'
        expiry = '2026-10-22'
        contracts = [
            {'instrument_key': 'CE', 'trading_symbol': 'NIFTY24500CE', 'expiry': expiry,
             'instrument_type': 'CE', 'strike_price': 24500, 'lot_size': 2},
            {'instrument_key': 'PE', 'trading_symbol': 'NIFTY24500PE', 'expiry': expiry,
             'instrument_type': 'PE', 'strike_price': 24500, 'lot_size': 2},
        ]
        spot = {f'{day} 09:19': [24500, 24500, 24500, 24500, 100]}
        ticks = {'NSE_INDEX|Nifty 50': spot}
        for key, price in [('CE', 100.0), ('PE', 100.0)]:
            ticks[key] = {
                f'{day} 09:20': [price, price, price, price, entry_volume],
                f'{day} 09:21': [price + 1, price + 1, price + 1, price + 1, exit_volume],
            }
        settings = {
            'symbol': 'NIFTY', 'start': day, 'end': day, 'entry': '09:20', 'exit': '09:21',
            'template': 'Short straddle', 'lots': 1, 'estimate_margin': False,
            'use_upstox_fees': False, 'order_fee': 0.0, 'slip_bps': 0.0,
        }
        return settings, contracts, ticks

    def test_strategy_does_not_fill_zero_or_insufficient_volume_exit(self):
        settings, contracts, ticks = self._strategy_fixture(exit_volume=0)
        trades, skips = strategy_backtest.simulate(settings, contracts, ticks)
        self.assertTrue(trades.empty)
        self.assertEqual(skips.iloc[-1]['Status'], 'UNFILLED_EXIT')
        self.assertIn('marks only', skips.iloc[-1]['Reason'])
        self.assertIn('Unrealised gross', skips.columns)

        settings, contracts, ticks = self._strategy_fixture(exit_volume=1)
        trades, skips = strategy_backtest.simulate(settings, contracts, ticks)
        self.assertTrue(trades.empty)
        self.assertEqual(skips.iloc[-1]['Status'], 'UNFILLED_EXIT')

    def test_strategy_entry_volume_must_cover_requested_contract_quantity(self):
        settings, contracts, ticks = self._strategy_fixture(entry_volume=1)
        trades, skips = strategy_backtest.simulate(settings, contracts, ticks)
        self.assertTrue(trades.empty)
        self.assertIn('insufficient traded volume', skips.iloc[-1]['Reason'])

    def test_upstox_option_charge_sides_and_gst_base(self):
        buy = broker_charges.charge('2026-11-11', 'Options', 100.0, 75, 'buy')
        sell = broker_charges.charge('2026-11-11', 'Options', 100.0, 75, 'sell')
        self.assertEqual(buy['STT'], 0.0)
        self.assertAlmostEqual(buy['Stamp duty'], 0.225)
        self.assertAlmostEqual(sell['STT'], 11.25)
        self.assertEqual(sell['Stamp duty'], 0.0)
        self.assertAlmostEqual(sell['Exchange'], 100.0 * 75 * 0.0003553)
        self.assertAlmostEqual(sell['IPFT'], 100.0 * 75 * 0.000005)
        self.assertAlmostEqual(sell['SEBI'], 100.0 * 75 * 0.000001)
        self.assertAlmostEqual(sell['GST'], 0.18 * (20.0 + sell['Exchange'] + sell['IPFT']))

    def test_margin_floor_is_an_explicit_calibration_parameter(self):
        legs = [
            {'kind': 'CE', 'strike': 30000, 'side': -1, 'entry': 5.0, 'quantity': 75},
            {'kind': 'PE', 'strike': 19000, 'side': -1, 'entry': 5.0, 'quantity': 75},
        ]
        default_proxy = strategy_backtest.estimate_exchange_margin(legs, 24500.0)
        lower_proxy = strategy_backtest.estimate_exchange_margin(
            legs, 24500.0, short_option_floor_rate=0.01, exposure_add_rate=0.0,
        )
        self.assertGreater(default_proxy, lower_proxy)
        with self.assertRaises(ValueError):
            strategy_backtest.estimate_exchange_margin(legs, 24500.0, short_option_floor_rate=np.nan)

    def test_risk_ratios_use_daily_capital_returns_and_requested_sessions(self):
        trades = pd.DataFrame([
            {'Date': '2025-01-06 10:00', 'Net': 100.0, 'Capital required': 1000.0},
            {'Date': '2025-01-08', 'Net': -20.0, 'Capital required': 1000.0},
        ])
        annualization = 252.0
        summary = strategy_backtest.summarize_performance(trades, annualization=annualization)
        returns = np.array([0.10, 0.0, -0.02])
        sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(annualization)
        sortino = returns.mean() / np.sqrt(np.mean(np.square(np.minimum(returns, 0.0)))) * np.sqrt(annualization)
        self.assertAlmostEqual(summary['sharpe'], sharpe, places=12)
        self.assertAlmostEqual(summary['sortino'], sortino, places=12)
        self.assertEqual(summary['risk_metric_version'], strategy_backtest.RISK_METRIC_VERSION)


if __name__ == '__main__':
    unittest.main(verbosity=2)
