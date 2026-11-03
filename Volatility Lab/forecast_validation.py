"""Offline, rolling one-year forecast validation. Variances use decimal returns."""
from dataclasses import asdict, dataclass
import json
import numpy as np
import pandas as pd
from forecast import fit_garch
from vol_lib import rv_snapshot, validate_ohlc

VERSION = '1.0'
MODELS = ('GARCH', 'EWMA', 'HV30')


@dataclass(frozen=True)
class ValidationConfig:
    horizons: tuple = (5, 10, 20, 30)
    min_returns: int = 200
    annualization: int = 252
    ewma_lambda: float = .94

    def __post_init__(self):
        if (not self.horizons or len(set(self.horizons)) != len(self.horizons)
                or any(type(h) is not int or not 1 <= h <= 252 for h in self.horizons)):
            raise ValueError('Use unique integer horizons from 1 to 252.')
        if type(self.min_returns) is not int or self.min_returns < 30:
            raise ValueError('At least 30 training returns are required.')
        if not np.isfinite(self.annualization) or self.annualization <= 0:
            raise ValueError('Annualization must be positive.')
        if not 0 < self.ewma_lambda < 1:
            raise ValueError('EWMA lambda must be between zero and one.')


def forecast_origin(training, config=ValidationConfig()):
    """No future data accepted here. Reuse the production models and conventions."""
    snapshot = rv_snapshot(training, window=30, lam=config.ewma_lambda,
                           ann_factor=config.annualization)
    values = {'EWMA': snapshot['ewma_next_session_vol']**2/config.annualization,
              'HV30': snapshot['close_to_close_rv']**2/config.annualization}
    result = {(model, h): (variance, '') for model, variance in values.items()
              for h in config.horizons}
    try:
        forecasts, diagnostics, _, _ = fit_garch(
            training, config.horizons, ann_factor=config.annualization,
            min_observations=config.min_returns)
        for _, row in forecasts.iterrows():
            result['GARCH', int(row['Sessions'])] = (
                (row['GARCH forecast %']/100)**2/config.annualization,
                json.dumps(diagnostics, allow_nan=False))
    except (ValueError, RuntimeError, FloatingPointError) as error:
        for h in config.horizons:
            result['GARCH', h] = (np.nan, str(error))
    return result


def walk_forward(frame, symbol, config=ValidationConfig(), progress=None, predictor=None, models=MODELS, origin_start=None):
    """Targets are t+1..t+H observations, never included in the training slice.

    Full calendar-year source coverage is mandatory. Exchange-calendar
    completeness is not established; do not describe row counts as calendar days.
    """
    data = validate_ohlc(frame)
    if data.empty:
        raise ValueError('No input candles.')
    returns = np.log(data.Close/data.Close.shift())
    rows = []
    for position, origin in enumerate(data.index):
        if origin_start is not None and origin<pd.Timestamp(origin_start):continue
        cutoff = origin - pd.DateOffset(years=1)
        if data.index[0] > cutoff:
            continue
        training = data.loc[(data.index > cutoff) & (data.index <= origin)]
        if len(training)-1 < config.min_returns:
            continue
        predictions = (predictor or forecast_origin)(training.reset_index(), config)
        if progress:
            progress(origin)
        for horizon in config.horizons:
            matured = position+horizon < len(data)
            future = returns.iloc[position+1:position+horizon+1]
            actual = float(np.mean(future.to_numpy()**2)) if matured else np.nan
            for model in models:
                predicted, note = predictions[model, horizon]
                valid = np.isfinite(predicted) and predicted > 0
                rows.append(dict(symbol=symbol, origin=origin, origin_position=position,
                    train_start=training.index[0], train_end=training.index[-1],
                    train_returns=len(training)-1, horizon=horizon, model=model,
                    target_start=data.index[position+1] if position+1 < len(data) else pd.NaT,
                    target_end=data.index[position+horizon] if matured else pd.NaT,
                    matured=matured, forecast_variance=predicted,
                    realized_variance=actual,
                    forecast_vol_pct=100*np.sqrt(config.annualization*predicted) if valid else np.nan,
                    realized_vol_pct=100*np.sqrt(config.annualization*actual) if matured else np.nan,
                    status='ok' if valid else 'failed', diagnostics=note))
    return pd.DataFrame(rows)


def score(rows, models=MODELS):
    """Common-cohort scores, plus deterministic disjoint target windows.

    QLIKE = log(f)+y/f; volatility errors are annualized percentage points.
    Nonoverlapping origins are chosen before model-success filtering.
    """
    summaries = []
    if rows.empty:
        return pd.DataFrame()
    for (symbol, horizon), group in rows.groupby(['symbol', 'horizon']):
        usable = group[(group.status == 'ok') & group.matured
                       & np.isfinite(group.forecast_variance) & (group.forecast_variance > 0)
                       & np.isfinite(group.realized_variance) & (group.realized_variance >= 0)]
        common = usable.groupby('origin').filter(
            lambda g: len(g) == len(models) and set(g.model) == set(models))
        first = group.origin_position.min()
        for sample in ('daily_overlapping', 'nonoverlapping'):
            selected = common if sample == 'daily_overlapping' else common[
                (common.origin_position-first) % horizon == 0]
            for model in models:
                all_model = group[group.model == model]
                part = selected[selected.model == model]
                errors = part.forecast_vol_pct-part.realized_vol_pct
                summaries.append(dict(symbol=symbol, horizon=horizon, model=model,
                    sample=sample, attempted=len(all_model),
                    matured=int(all_model.matured.sum()),
                    failed=int((all_model.status != 'ok').sum()), scored=len(part),
                    evaluation_start=part.origin.min(), evaluation_end=part.origin.max(),
                    qlike=float((np.log(part.forecast_variance)+
                                 part.realized_variance/part.forecast_variance).mean()),
                    rmse_pp=float(np.sqrt((errors**2).mean())),
                    mae_pp=float(errors.abs().mean()), bias_pp=float(errors.mean())))
    return pd.DataFrame(summaries)
