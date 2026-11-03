import unittest
import numpy as np
import pandas as pd
from model_view import model_tables, horizon_grid, fit_candidates
from har_research import har_components_summary


class ResearchModelsTests(unittest.TestCase):
    def setUp(self):
        # Generate 250 days of synthetic price data
        np.random.seed(42)
        dates = pd.date_range('2025-01-01', periods=250, freq='B')
        returns = np.random.normal(0.0005, 0.012, size=250)
        prices = 24000.0 * np.exp(np.cumsum(returns))
        self.frame = pd.DataFrame({
            'Date': dates,
            'Open': prices * (1 - 0.002),
            'High': prices * (1 + 0.005),
            'Low': prices * (1 - 0.005),
            'Close': prices,
            'Volume': 100000
        })

    def test_fit_candidates_produces_gjr_and_garch_t(self):
        candidates = fit_candidates(self.frame, horizons=(5, 10, 20))
        self.assertIsInstance(candidates, dict)
        # Should contain entries for GJR-GARCH and GARCH-t
        self.assertIn(('GJR-GARCH', 20), candidates)
        self.assertIn(('GARCH-t', 20), candidates)
        self.assertGreater(candidates[('GJR-GARCH', 20)], 0)
        self.assertGreater(candidates[('GARCH-t', 20)], 0)

    def test_horizon_grid_with_and_without_candidates(self):
        forecasts, realized, annual_ewma, _ = model_tables(self.frame, horizons=(5, 10, 20))
        # Baseline grid without candidates
        base_grid = horizon_grid(forecasts, realized, annual_ewma)
        self.assertIn('GARCH projection %', base_grid.columns)
        self.assertIn('EWMA projection %', base_grid.columns)
        self.assertIn('Yang–Zhang historical %', base_grid.columns)
        self.assertNotIn('GJR-GARCH projection %', base_grid.columns)

        # Extended grid with candidates
        candidates = {('GJR-GARCH', 5): 18.5, ('GJR-GARCH', 10): 18.2, ('GJR-GARCH', 20): 18.0,
                      ('GARCH-t', 5): 19.1, ('GARCH-t', 10): 18.8, ('GARCH-t', 20): 18.5}
        ext_grid = horizon_grid(forecasts, realized, annual_ewma, candidates=candidates)
        self.assertIn('GJR-GARCH projection %', ext_grid.columns)
        self.assertIn('GARCH-t projection %', ext_grid.columns)
        self.assertEqual(len(ext_grid), 3)

    def test_har_components_summary(self):
        summary = har_components_summary(self.frame)
        self.assertIsInstance(summary, pd.DataFrame)
        self.assertIn('Daily RV %', summary.columns)
        self.assertIn('Weekly (5d) RV %', summary.columns)
        self.assertIn('Monthly (22d) RV %', summary.columns)
        self.assertFalse(summary.empty)
        # Values should be positive numbers
        self.assertTrue((summary['Daily RV %'] > 0).all())
        self.assertTrue((summary['Weekly (5d) RV %'] > 0).all())
        self.assertTrue((summary['Monthly (22d) RV %'] > 0).all())


if __name__ == '__main__':
    unittest.main()
