"""EGARCH horizon aggregation, availability and model identity regressions."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from arch import arch_model

from volatility_lab.forecasting import egarch
from volatility_lab.forecasting.expiry_forecast import term_structure_forecast, sessions


def history():
    path = Path(__file__).parents[2] / 'volatility_lab/data/nifty_one_year.csv'
    return pd.read_csv(path).query('Date <= "2026-11-11"').tail(751)


def test_actual_egarch_one_step_matches_analytic_and_seed_repeats(monkeypatch):
    monkeypatch.setattr(egarch, 'HORIZON_CAP', 20)
    egarch._fit.cache_clear()
    frame = history()
    variance, status, count = egarch.fit_egarch(frame)
    returns = 100 * np.log(frame.Close / frame.Close.shift()).dropna()
    fitted = arch_model(returns, mean='Zero', vol='EGARCH', p=1, o=1, q=1,
                        dist='normal', rescale=False).fit(disp='off', options={'maxiter': 2000})
    expected = fitted.forecast(horizon=1, reindex=False).variance.iloc[-1, 0] / 10000
    assert count == 750
    assert status == 'Ready'
    assert variance[0] == pytest.approx(expected)
    assert len(variance) == 20
    assert np.isfinite(variance).all() and (variance > 0).all()
    egarch._fit.cache_clear()
    repeated, _, _ = egarch.fit_egarch(frame)
    np.testing.assert_array_equal(variance, repeated)
    egarch._fit.cache_clear()


def test_expiry_averages_variance_and_excludes_future_closes(monkeypatch):
    frame = history()
    future = frame.iloc[-1:].copy()
    future['Date'] = '2026-11-13'
    received = []
    path = np.arange(1, 266, dtype=float) / 10000

    def fit(training):
        received.append(training.index.max().strftime('%Y-%m-%d'))
        return path, 'Ready', 750

    monkeypatch.setattr(egarch, 'fit_egarch', fit)
    result = term_structure_forecast(pd.concat([frame, future]), '2026-11-17',
                                     '2026-11-12T10:00:00+05:30')
    count = len(sessions('2026-11-12', '2026-11-17'))
    assert received == ['2026-11-11']
    assert result['Trading sessions'] == count
    assert result['EGARCH expiry RV'] == pytest.approx(100 * np.sqrt(245 * path[:count].mean()))
    assert result['Comparison ready'] is False
    assert 'GARCH expiry RV' not in result


def test_insufficient_history_withholds_egarch_but_keeps_ewma():
    result = term_structure_forecast(history().tail(300), '2026-11-17',
                                     '2026-11-11T16:15:00+05:30')
    assert np.isnan(result['EGARCH expiry RV'])
    assert '500' in result['EGARCH status']
    assert result['EWMA expiry RV'] > 0


def test_expiry_day_requires_remaining_session_model():
    with pytest.raises(ValueError, match='expiry-day'):
        term_structure_forecast(history(), '2026-11-17', '2026-11-17T10:00:00+05:30')
