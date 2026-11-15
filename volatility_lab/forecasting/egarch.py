"""Reproducible asymmetric EGARCH forecasts in daily return variance units."""
from functools import lru_cache
import warnings

import numpy as np
from arch import arch_model

from volatility_lab.core.vol_lib import validate_ohlc
from volatility_lab.forecasting.forecast import HORIZON_CAP

TRAINING_RETURNS = 750
MIN_RETURNS = 500
SIMULATIONS = 20000
SEED = 20261115


@lru_cache(maxsize=8)
def _fit(returns_bytes):
    returns = np.frombuffer(returns_bytes, dtype=np.float64)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        model = arch_model(returns, mean='Zero', vol='EGARCH', p=1, o=1, q=1,
                           dist='normal', rescale=False)
        fitted = model.fit(disp='off', options={'maxiter': 2000})
        if fitted.convergence_flag != 0:
            raise ValueError('EGARCH optimizer did not converge.')
        parameters = fitted.params
        if not np.isfinite(parameters).all() or not 0 <= parameters['beta[1]'] < 1:
            raise ValueError('EGARCH parameters failed stability checks.')
        # EGARCH has no multi-step analytic forecast in arch. Average simulated
        # conditional variances first, then annualize and take the square root.
        rng = np.random.default_rng(SEED)
        forecast = fitted.forecast(horizon=HORIZON_CAP, method='simulation',
                                   simulations=SIMULATIONS, rng=rng.standard_normal,
                                   reindex=False)
        variance = forecast.variance.iloc[-1].to_numpy(dtype=float) / 10000
    if not np.isfinite(variance).all() or (variance <= 0).any():
        raise ValueError('EGARCH produced an invalid variance path.')
    variance.setflags(write=False)
    notes = sorted({type(w.message).__name__ for w in caught})
    return variance, '; '.join(notes) or 'Ready'


def fit_egarch(frame):
    data = validate_ohlc(frame).tail(TRAINING_RETURNS + 1)
    returns = 100 * np.log(data.Close / data.Close.shift()).dropna()
    if len(returns) < MIN_RETURNS:
        raise ValueError(f'EGARCH requires at least {MIN_RETURNS} completed daily returns.')
    if returns.std() < 1e-8:
        raise ValueError('EGARCH cannot fit constant returns.')
    variance, status = _fit(np.ascontiguousarray(returns, dtype=np.float64).tobytes())
    return variance.copy(), status, len(returns)
