"""Tests for 3D Volatility Surface engine (vol_surface_3d.py)."""

import json
from pathlib import Path
import tempfile
import unittest
from datetime import date
import numpy as np

from vol_surface_3d import (
    load_expiry_chains,
    extract_surface_mesh,
    build_surface_figure
)


class TestVolSurface3D(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.today = date(2026, 11, 3)

        # Create synthetic option quote files for 3 expiries
        expiries = ['2026-11-03', '2026-11-10', '2026-11-24']
        spot = 24000.0

        for exp in expiries:
            chain = []
            for strike in range(22000, 26001, 100):
                m = strike / spot
                # Synthetic smile: IV higher for puts (lower strike)
                base_iv = 14.0 + 3.0 * (1.0 - m) + 8.0 * (1.0 - m)**2
                chain.append({
                    'strike_price': float(strike),
                    'call_options': {'option_greeks': {'iv': float(base_iv), 'delta': 0.5}},
                    'put_options': {'option_greeks': {'iv': float(base_iv + 1.5), 'delta': -0.5}}
                })
            
            payload = {
                'spot': spot,
                'valuation': f'{exp}T15:30:00+05:30',
                'broker_option_chain': chain
            }
            file_path = self.data_dir / f'nifty_option_quotes_{exp}.json'
            file_path.write_text(json.dumps(payload), encoding='utf-8')

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_expiry_chains(self):
        chains = load_expiry_chains('NIFTY', self.data_dir, today=self.today)
        self.assertEqual(len(chains), 3)
        self.assertEqual(chains[0]['expiry'], '2026-11-03')
        self.assertAlmostEqual(chains[0]['dte'], 0.2)
        self.assertEqual(chains[1]['expiry'], '2026-11-10')
        self.assertAlmostEqual(chains[1]['dte'], 7.0)
        self.assertEqual(chains[2]['expiry'], '2026-11-24')
        self.assertAlmostEqual(chains[2]['dte'], 21.0)

    def test_extract_surface_mesh_moneyness(self):
        chains = load_expiry_chains('NIFTY', self.data_dir, today=self.today)
        x_grid, y_grid, z_matrix, labels, diags = extract_surface_mesh(
            chains,
            iv_mode='otm',
            space_mode='moneyness',
            moneyness_range_pct=6.0,
            n_strikes=21
        )

        self.assertEqual(len(x_grid), 21)
        self.assertEqual(len(y_grid), 3)
        self.assertEqual(z_matrix.shape, (3, 21))
        self.assertEqual(len(labels), 3)

        # X-grid should range from 94% to 106%
        self.assertAlmostEqual(x_grid[0], 94.0)
        self.assertAlmostEqual(x_grid[-1], 106.0)

        # All IVs should be positive and finite
        self.assertTrue(np.all(z_matrix > 0))
        self.assertTrue(np.all(np.isfinite(z_matrix)))

        # Diagnostics check
        self.assertEqual(diags['num_expiries'], 3)
        self.assertEqual(diags['ref_spot'], 24000.0)

    def test_extract_surface_mesh_absolute_strike(self):
        chains = load_expiry_chains('NIFTY', self.data_dir, today=self.today)
        x_grid, y_grid, z_matrix, labels, diags = extract_surface_mesh(
            chains,
            iv_mode='call',
            space_mode='strike',
            moneyness_range_pct=5.0,
            n_strikes=15,
            include_zero_dte=False
        )

        # 0-DTE excluded, so 2 expiries remain
        self.assertEqual(len(y_grid), 2)
        self.assertEqual(len(x_grid), 15)
        self.assertEqual(z_matrix.shape, (2, 15))

        # Strike values should be near 24000
        self.assertTrue(22000 < x_grid[0] < 24000)
        self.assertTrue(24000 < x_grid[-1] < 26000)

    def test_build_surface_figure(self):
        chains = load_expiry_chains('NIFTY', self.data_dir, today=self.today)
        x_grid, y_grid, z_matrix, labels, _ = extract_surface_mesh(
            chains,
            iv_mode='otm',
            space_mode='moneyness',
            n_strikes=15
        )

        fig = build_surface_figure(x_grid, y_grid, z_matrix, labels, space_mode='moneyness', colorscale='Viridis', camera_preset='smile')
        self.assertEqual(len(fig.data), 1)
        surface = fig.data[0]
        self.assertEqual(surface.type, 'surface')
        self.assertEqual(surface.x.shape, (15,))
        self.assertEqual(surface.y.shape, (3,))
        self.assertEqual(surface.z.shape, (3, 15))


if __name__ == '__main__':
    unittest.main()
