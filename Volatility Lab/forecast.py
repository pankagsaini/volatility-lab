"""GARCH(1,1) forecasts: zero mean, normal innovations, daily returns (with LRU-cached MLE)."""
from functools import lru_cache
import hashlib
import warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from vol_lib import validate_ohlc, rolling_rv_suite


def _scipy_garch11_fallback(r: np.ndarray, max_h: int):
    """Exact Gaussian zero-mean GARCH(1,1) MLE via SciPy L-BFGS-B when `arch` package is absent."""
    r2 = r * r
    n = len(r)
    var0 = float(np.var(r))

    def neg_loglik(params):
        omega, alpha, beta = params
        if omega <= 1e-8 or alpha < 0 or beta < 0 or (alpha + beta) >= 0.9995:
            return 1e12
        sigma2 = np.empty(n, dtype=float)
        sigma2[0] = var0
        for t in range(1, n):
            sigma2[t] = omega + alpha * r2[t - 1] + beta * sigma2[t - 1]
        return 0.5 * float(np.sum(np.log(2.0 * np.pi) + np.log(sigma2) + r2 / sigma2))

    x0 = np.array([max(var0 * 0.05, 1e-4), 0.08, 0.87])
    bounds = [(1e-6, var0 * 5.0 + 10.0), (1e-4, 0.50), (0.10, 0.995)]
    res = minimize(neg_loglik, x0, method='L-BFGS-B', bounds=bounds)
    omega, alpha, beta = (float(v) for v in res.x)
    persistence = min(alpha + beta, 0.999)
    sigma2 = np.empty(n, dtype=float)
    sigma2[0] = var0
    for t in range(1, n):
        sigma2[t] = omega + alpha * r2[t - 1] + beta * sigma2[t - 1]

    f_var = np.empty(max_h, dtype=float)
    f_var[0] = omega + alpha * r2[-1] + beta * sigma2[-1]
    for h in range(1, max_h):
        f_var[h] = omega + persistence * f_var[h - 1]

    ll = -float(res.fun)
    aic = 2 * 3 - 2 * ll
    bic = 3 * np.log(n) - 2 * ll
    return {
        'nobs': n,
        'params': {'omega': omega, 'alpha[1]': alpha, 'beta[1]': beta},
        'daily_variance': f_var / 10000.0,
        'cond_vol': np.sqrt(sigma2),
        'll': ll,
        'aic': float(aic),
        'bic': float(bic),
        'notes': [],
    }


@lru_cache(maxsize=32)
def _cached_garch_mle(returns_digest: str, returns_bytes: bytes, n_obs: int):
    """Fit GARCH(1,1) once per unique return series and project all 245 forward sessions."""
    r_arr = np.frombuffer(returns_bytes, dtype=np.float64)
    r_series = pd.Series(r_arr)
    max_h = 245
    try:
        from arch import arch_model
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            fit = arch_model(r_series, mean='Zero', vol='GARCH', p=1, q=1,
                             dist='normal', rescale=False).fit(disp='off', options={'maxiter': 1000})
        if fit.convergence_flag != 0:
            raise ValueError('GARCH optimizer did not converge; forecasts are withheld.')
        parameters = {k: float(v) for k, v in fit.params.items()}
        daily_variance = fit.forecast(horizon=max_h, method='analytic', reindex=False).variance.iloc[-1].to_numpy(dtype=float) / 10000.0
        nobs = int(fit.nobs)
        ll, aic, bic = float(fit.loglikelihood), float(fit.aic), float(fit.bic)
        cond_vol = np.asarray(fit.conditional_volatility, dtype=float)
        notes = sorted(set(type(w.message).__name__ for w in caught))
        return {
            'nobs': nobs,
            'params': parameters,
            'daily_variance': daily_variance,
            'cond_vol': cond_vol,
            'll': ll,
            'aic': aic,
            'bic': bic,
            'notes': notes,
        }
    except ImportError:
        return _scipy_garch11_fallback(r_arr, max_h)


def fit_garch(frame, horizons=(5, 10, 20, 30, 60), ann_factor=245, min_observations=250):
    if not horizons or any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 245 for h in horizons):
        raise ValueError('Forecast horizons must be integers from 1 to 245 sessions.')
    if not np.isfinite(ann_factor) or ann_factor <= 0:
        raise ValueError('Annualization must be positive.')
    df = validate_ohlc(frame)
    returns = 100 * np.log(df.Close / df.Close.shift()).dropna()
    if len(returns) < min_observations:
        raise ValueError(f'GARCH requires at least {min_observations} daily returns.')
    if returns.std() < 1e-8:
        raise ValueError('GARCH cannot fit a constant or near-constant return series.')

    r_contig = np.ascontiguousarray(returns.to_numpy(dtype=np.float64))
    r_bytes = r_contig.tobytes()
    r_digest = hashlib.blake2b(r_bytes, digest_size=16).hexdigest()

    cached = _cached_garch_mle(r_digest, r_bytes, len(r_contig))
    parameters = cached['params']
    max_req = max(horizons)
    daily_variance = cached['daily_variance'][:max_req]
    if not np.isfinite(daily_variance).all() or (daily_variance <= 0).any():
        raise ValueError('GARCH produced invalid forecast variance.')

    persistence = parameters['alpha[1]'] + parameters['beta[1]']
    notes = list(cached['notes'])
    if persistence >= .999:
        notes.append('Persistence is near one; long-run variance is not reported.')

    forecasts = pd.DataFrame([
        {'Sessions': h, 'GARCH forecast %': float(np.sqrt(daily_variance[:h].mean() * ann_factor) * 100)}
        for h in horizons
    ])
    diagnostics = {
        'observations': cached['nobs'],
        'converged': True,
        'omega_pct_squared': parameters['omega'],
        'alpha': parameters['alpha[1]'],
        'beta': parameters['beta[1]'],
        'persistence': persistence,
        'log_likelihood': cached['ll'],
        'AIC': cached['aic'],
        'BIC': cached['bic'],
        'warnings': notes,
        'long_run_vol_pct': float(np.sqrt(parameters['omega'] / (1 - persistence) * ann_factor)) if persistence < .999 else None,
    }
    path = pd.DataFrame({
        'Future session': np.arange(1, len(daily_variance) + 1),
        'Daily conditional vol %': np.sqrt(daily_variance * ann_factor) * 100,
        'Horizon-average vol %': np.sqrt(np.cumsum(daily_variance) / np.arange(1, len(daily_variance) + 1) * ann_factor) * 100,
    })
    history = pd.DataFrame({
        'Date': returns.index,
        'Fitted conditional vol %': cached['cond_vol'] * np.sqrt(ann_factor),
    })
    return forecasts, diagnostics, path, history


def rolling_rv(frame, window=20, lam=.94, ann_factor=245):
    suite = rolling_rv_suite(frame, window=window, lam=lam, ann_factor=ann_factor)
    return pd.DataFrame({
        'Date': suite.index,
        'Close-to-close RV %': suite['close_to_close_rv'].to_numpy() * 100.0,
        'Yang-Zhang RV %': suite['yang_zhang_rv'].to_numpy() * 100.0,
        'EWMA next-session %': suite['ewma_next_session_vol'].to_numpy() * 100.0,
    })
