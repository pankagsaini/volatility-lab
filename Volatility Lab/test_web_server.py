"""Unit tests for the Opstra-inspired Web Server and Data API (Option 2)."""

import json
from pathlib import Path
import unittest
import numpy as np

import web_data
import web_server


class TestWebServer(unittest.TestCase):

    def test_market_summary(self):
        summary = web_data.get_market_summary()
        self.assertIn('ist_time', summary)
        self.assertIn('market_status', summary)
        self.assertIn('nifty', summary)
        self.assertIn('banknifty', summary)
        self.assertIn('india_vix', summary)
        self.assertIn('cloud_sync', summary)
        
        n = summary['nifty']
        self.assertEqual(n['symbol'], 'NIFTY 50')
        if n['price'] is not None:
            self.assertGreater(n['price'], 10000.0)

    def test_available_expiries(self):
        expiries = web_data.get_available_expiries('NIFTY')
        self.assertIsInstance(expiries, list)
        if expiries:
            self.assertIn('expiry', expiries[0])
            self.assertIn('dte', expiries[0])
            self.assertGreaterEqual(expiries[0]['dte'], 0.2)

    def test_option_chain_table(self):
        chain = web_data.get_option_chain_table('NIFTY')
        self.assertEqual(chain['symbol'], 'NIFTY')
        if 'rows' in chain:
            self.assertGreater(len(chain['rows']), 5)
            self.assertIn('spot', chain)
            self.assertIn('atm_strike', chain)
            self.assertIn('pcr', chain)
            self.assertIn('max_pain', chain)
            
            # Verify ATM row exists
            atm_rows = [r for r in chain['rows'] if r['is_atm']]
            self.assertTrue(len(atm_rows) >= 1)
            
            # Verify row keys
            sample = chain['rows'][0]
            self.assertIn('strike', sample)
            self.assertIn('call', sample)
            self.assertIn('put', sample)
            self.assertIn('ltp', sample['call'])
            self.assertIn('iv', sample['call'])
            self.assertIn('delta', sample['call'])

    def test_vol_surface_payload(self):
        surf = web_data.get_vol_surface_payload('NIFTY', iv_mode='otm', space_mode='strike')
        if 'error' not in surf:
            self.assertEqual(surf['symbol'], 'NIFTY')
            self.assertIn('z_matrix', surf)
            self.assertIn('x_grid', surf)
            self.assertIn('y_grid', surf)
            self.assertIn('atm_ridge', surf)
            self.assertIn('smile_curves', surf)
            self.assertIn('term_structure', surf)
            self.assertIn('diagnostics', surf)
            
            # Dimensions check
            self.assertEqual(len(surf['z_matrix']), len(surf['y_grid']))
            self.assertEqual(len(surf['z_matrix'][0]), len(surf['x_grid']))

    def test_trade_radar(self):
        radar = web_data.get_trade_radar('NIFTY')
        if 'error' not in radar:
            self.assertEqual(radar['symbol'], 'NIFTY')
            self.assertIn('spot', radar)
            self.assertIn('expected_move_pts', radar)
            self.assertIn('expected_move_pct', radar)
            self.assertIn('expected_range_low', radar)
            self.assertIn('expected_range_high', radar)
            self.assertIn('bias_title', radar)
            self.assertIn('bias_action', radar)

    def test_futures_carry(self):
        carry = web_data.get_futures_carry_payload()
        self.assertIn('rate_percent', carry)
        self.assertIn('indices', carry)

    def test_historical_candles(self):
        candles = web_data.get_historical_candles('NIFTY')
        self.assertEqual(candles['symbol'], 'NIFTY')
        self.assertIn('candles', candles)
        self.assertIn('volumes', candles)
        self.assertGreater(len(candles['candles']), 200)
        c0 = candles['candles'][0]
        self.assertIn('time', c0)
        self.assertIn('open', c0)
        self.assertIn('high', c0)
        self.assertIn('low', c0)
        self.assertIn('close', c0)

    def test_backtest_overview(self):
        bt = web_data.get_backtest_overview()
        self.assertIn('runs', bt)
        if bt['runs']:
            r = bt['runs'][0]
            self.assertIn('name', r)
            self.assertIn('win_rate_pct', r)
            self.assertIn('total_net_pnl', r)



if __name__ == '__main__':
    unittest.main()
