"""GARCH(1,1) forecasts: zero mean, normal innovations, daily returns."""
import warnings
import numpy as np
import pandas as pd
from vol_lib import validate_ohlc


def fit_garch(frame, horizons=(5, 10, 20, 30, 60), ann_factor=252, min_observations=250):
    from arch import arch_model
    if not horizons or any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 252 for h in horizons):
        raise ValueError('Forecast horizons must be integers from 1 to 252 sessions.')
    if not np.isfinite(ann_factor) or ann_factor <= 0:
        raise ValueError('Annualization must be positive.')
    df = validate_ohlc(frame)
    returns = 100 * np.log(df.Close / df.Close.shift()).dropna()
    if len(returns) < min_observations:
        raise ValueError(f'GARCH requires at least {min_observations} daily returns.')
    if returns.std() < 1e-8:
        raise ValueError('GARCH cannot fit a constant or near-constant return series.')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        fit = arch_model(returns, mean='Zero', vol='GARCH', p=1, q=1,
                         dist='normal', rescale=False).fit(disp='off', options={'maxiter': 1000})
    if fit.convergence_flag != 0:
        raise ValueError('GARCH optimizer did not converge; forecasts are withheld.')
    parameters = {k: float(v) for k, v in fit.params.items()}
    daily_variance = fit.forecast(horizon=max(horizons), method='analytic', reindex=False).variance.iloc[-1].to_numpy() / 10000
    if not np.isfinite(daily_variance).all() or (daily_variance <= 0).any():
        raise ValueError('GARCH produced invalid forecast variance.')
    persistence = parameters['alpha[1]'] + parameters['beta[1]']
    notes = sorted(set(type(w.message).__name__ for w in caught))
    if persistence >= .999:
        notes.append('Persistence is near one; long-run variance is not reported.')
    forecasts = pd.DataFrame([{'Sessions': h, 'GARCH forecast %': float(np.sqrt(daily_variance[:h].mean()*ann_factor)*100)} for h in horizons])
    diagnostics = {'observations': int(fit.nobs), 'converged': True,
                   'omega_pct_squared': parameters['omega'], 'alpha': parameters['alpha[1]'],
                   'beta': parameters['beta[1]'], 'persistence': persistence,
                   'log_likelihood': float(fit.loglikelihood), 'AIC': float(fit.aic),
                   'BIC': float(fit.bic), 'warnings': notes,
                   'long_run_vol_pct': float(np.sqrt(parameters['omega']/(1-persistence)*ann_factor)) if persistence < .999 else None}
    path = pd.DataFrame({'Future session': np.arange(1, len(daily_variance)+1),
                         'Daily conditional vol %': np.sqrt(daily_variance*ann_factor)*100,
                         'Horizon-average vol %': np.sqrt(np.cumsum(daily_variance)/np.arange(1,len(daily_variance)+1)*ann_factor)*100})
    history = pd.DataFrame({'Date': returns.index, 'Fitted conditional vol %': np.asarray(fit.conditional_volatility)*np.sqrt(ann_factor)})
    return forecasts, diagnostics, path, history


def rolling_rv(frame, window=20, lam=.94):
    df = validate_ohlc(frame)
    r = np.log(df.Close/df.Close.shift())
    gap = np.log(df.Open/df.Close.shift())
    oc = np.log(df.Close/df.Open)
    rs = np.log(df.High/df.Close)*np.log(df.High/df.Open)+np.log(df.Low/df.Close)*np.log(df.Low/df.Open)
    k = .34/(1.34+(window+1)/(window-1))
    yz = gap.rolling(window).var()+k*oc.rolling(window).var()+(1-k)*rs.rolling(window).mean()
    return pd.DataFrame({'Date':df.index, 'Close-to-close RV %':r.rolling(window).std().to_numpy()*np.sqrt(252)*100,
                         'Yang–Zhang RV %':np.sqrt(yz.clip(lower=0).to_numpy()*252)*100,
                         'EWMA next-session %':np.sqrt(r.pow(2).ewm(alpha=1-lam,adjust=False).mean().to_numpy()*252)*100})
