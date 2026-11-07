"""Expiry variance matching; consistent trading-session horizon annualization (with fast horizon caching)."""
from datetime import datetime, time, timedelta
from functools import lru_cache
import numpy as np
import pandas as pd
from forecast import fit_garch
from vol_lib import validate_ohlc, rv_snapshot
from market_data import IST
from market_calendar import HOLIDAYS, SPECIAL_SESSIONS, last_trading_day_on_or_before

VERSION = 'expiry-v4-session-annualized-nse2026'
TERM_VERSION = 'term-v1-last-completed-close'
CALENDAR_SOURCE = 'https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf'
SPECIAL = pd.Timestamp('2026-11-08').date()
HORIZON_CAP = 265

_MODEL_BASE_CACHE: dict[tuple[str, int, float], tuple[float, np.ndarray | None, str]] = {}
_VALIDATED_SLICE_CACHE: dict[tuple[int, str, str, float], tuple[pd.DataFrame, object]] = {}


@lru_cache(maxsize=128)
def _cached_sessions(start_iso: str, end_iso: str) -> tuple:
    start_d = pd.Timestamp(start_iso).date()
    end_d = pd.Timestamp(end_iso).date()
    if start_d > end_d:
        return ()
    supported_years = {d.year for d in HOLIDAYS} | {2023, 2024, 2025, 2026, 2027, 2028}
    if start_d.year not in supported_years or end_d.year not in supported_years:
        raise ValueError(f'Verified trading calendar currently covers {sorted(supported_years)} only.')
    return tuple(
        d.date() for d in pd.date_range(start_d, end_d)
        if (d.weekday() < 5 and d.date() not in HOLIDAYS) or d.date() == SPECIAL
    )


def sessions(start, end):
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    if start_d > end_d:
        return []
    return list(_cached_sessions(start_d.isoformat(), end_d.isoformat()))


def _prepare_one_year_slice(frame: pd.DataFrame, cutoff_date) -> tuple[pd.DataFrame, object]:
    """Validate OHLC and extract the 1-year training window once per unique (frame, cutoff_date)."""
    cutoff_iso = str(cutoff_date)
    try:
        last_close = float(frame['Close'].iloc[-1])
        first_dt = str(frame['Date'].iloc[0]) if 'Date' in frame.columns else str(frame.index[0])
        cache_key = (len(frame), first_dt, cutoff_iso, last_close)
    except Exception:
        cache_key = None

    if cache_key is not None:
        cached = _VALIDATED_SLICE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    data = validate_ohlc(frame)
    cutoff_ts = pd.Timestamp(cutoff_date)
    data = data[data.index <= cutoff_ts]
    if data.empty:
        raise ValueError('No completed candles available at the quote time.')
    last = data.index[-1].date()
    elapsed = sessions(last + timedelta(days=1), cutoff_date)
    if elapsed:
        raise ValueError('Latest completed session is missing; refresh underlying history.')
    data = data[data.index > data.index[-1] - pd.DateOffset(years=1)]
    if len(data) < 201:
        raise ValueError('Expiry models need at least 200 completed returns.')
    data._ohlc_validated = True

    if cache_key is not None:
        if len(_VALIDATED_SLICE_CACHE) >= 8:
            _VALIDATED_SLICE_CACHE.clear()
        _VALIDATED_SLICE_CACHE[cache_key] = (data, last)
    return data, last


def _get_base_expiry_models(data: pd.DataFrame, last_str: str) -> tuple[float, np.ndarray | None, str]:
    """Compute or retrieve cached EWMA RV % and 245-session GARCH variance path for `data`."""
    cache_key = (last_str, len(data), float(data['Close'].iloc[-1]))
    cached = _MODEL_BASE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    data._ohlc_validated = True
    ewma_daily_var = (rv_snapshot(data)['ewma_next_session_vol'] ** 2) / 245.0
    ewma_rv_pct = float(100.0 * np.sqrt(ewma_daily_var * 245.0))

    garch_vars = None
    garch_status = 'Ready'
    try:
        _, diagnostics, path, _ = fit_garch(data, (245,), min_observations=200)
        garch_vars = (path['Daily conditional vol %'].to_numpy(dtype=float) / 100.0) ** 2 / 245.0
        garch_status = '; '.join(diagnostics['warnings']) or 'Ready'
    except (ValueError, RuntimeError, FloatingPointError, ImportError) as error:
        garch_status = str(error)

    if len(_MODEL_BASE_CACHE) >= 8:
        _MODEL_BASE_CACHE.clear()
    _MODEL_BASE_CACHE[cache_key] = (ewma_rv_pct, garch_vars, garch_status)
    return ewma_rv_pct, garch_vars, garch_status


def expiry_forecast(frame, expiry, as_of, iv=None):
    """Condition on completed daily closes only; partial day is a stated approximation."""
    now = pd.Timestamp(as_of)
    if now.tzinfo is None:
        raise ValueError('Quote valuation requires an explicit timezone.')
    now = now.tz_convert(IST).to_pydatetime()
    expiry = pd.Timestamp(expiry).date()
    reason = forecast_restriction(expiry, now)
    if reason:
        raise ValueError(reason)
    end = datetime.combine(expiry, time(15, 30), tzinfo=IST)
    years = (end - now).total_seconds() / (365 * 86400)
    if years <= 0:
        raise ValueError('Expiry has elapsed at the quote valuation time.')
    if now.date() == SPECIAL or expiry == SPECIAL:
        raise ValueError('Muhurat intraday timings require a verified special-session schedule.')
    if expiry not in sessions(expiry, expiry):
        raise ValueError('Expiry is not a verified trading date.')

    cutoff = now.date() if now.time() >= time(15, 40) else now.date() - timedelta(days=1)
    data, last = _prepare_one_year_slice(frame, cutoff)

    dates = sessions(last + timedelta(days=1), expiry)
    if not dates or len(dates) > HORIZON_CAP:
        raise ValueError('Unsupported expiry forecast horizon.')
    weights = []
    for date in dates:
        weight = 1.0
        if date < now.date():
            weight = 0.0
        elif date == now.date():
            opening = datetime.combine(date, time(9, 15), tzinfo=IST)
            closing = datetime.combine(date, time(15, 30), tzinfo=IST)
            weight = float(np.clip((closing - now).total_seconds() / (closing - opening).total_seconds(), 0, 1))
        weights.append(weight)
    weights = np.array(weights, dtype=float)
    total_weights = float(weights.sum())
    if total_weights <= 0:
        raise ValueError('No remaining trading variance horizon.')

    ewma_rv_pct, garch_vars_245, garch_status = _get_base_expiry_models(data, str(last))

    result = {
        'Expiry': str(expiry),
        'As of': now.isoformat(),
        'Training through': str(last),
        'DTE': years * 365.0,
        'Trading sessions': int((weights > 0).sum()),
        'Session equivalents': total_weights,
        'IV': iv,
        'EWMA expiry RV': ewma_rv_pct,
        'GARCH expiry RV': np.nan,
        'GARCH status': garch_status,
        'Version': VERSION,
    }

    if garch_vars_245 is not None and len(garch_vars_245) >= len(dates):
        variances = garch_vars_245[:len(dates)]
        mean_session_var = float(np.dot(variances, weights) / total_weights)
        result['GARCH expiry RV'] = float(100.0 * np.sqrt(mean_session_var * 245.0))

    for model in ('GARCH', 'EWMA'):
        result[f'IV - {model}'] = (
            float(iv - result[f'{model} expiry RV'])
            if iv is not None and np.isfinite(iv) and np.isfinite(result[f'{model} expiry RV'])
            else np.nan
        )
    return result


SAME_DAY_STATUS = 'Withheld: expiry-day RV needs a validated remaining-session model.'
FORECAST_FIELDS = ('GARCH expiry RV', 'EWMA expiry RV', 'IV - GARCH', 'IV - EWMA')


def same_day(expiry, as_of):
    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        raise ValueError('Quote valuation requires an explicit timezone.')
    return pd.Timestamp(expiry).date() == stamp.tz_convert(IST).date()


def eligible_history(frame):
    """Vectorized read-time quarantine of old same-day or pre-close estimates."""
    out = frame.copy()
    if out.empty or not {'Expiry', 'As of'}.issubset(out):
        return out
    stamps = pd.to_datetime(out['As of'], format='ISO8601', utc=True).dt.tz_convert(IST)
    expiries = pd.to_datetime(out['Expiry']).dt.date
    same_day_mask = stamps.dt.date == expiries
    intraday_mask = (~same_day_mask) & (stamps.dt.time < time(15, 40))
    mask = same_day_mask | intraday_mask

    reasons = pd.Series('', index=out.index, dtype=object)
    reasons.loc[intraday_mask] = 'Withheld: expiry RV comparison requires a completed-session snapshot; no validated intraday allocation.'
    reasons.loc[same_day_mask] = SAME_DAY_STATUS

    for field in FORECAST_FIELDS:
        if field in out:
            out.loc[mask, field] = np.nan
    for field in ('Forecast status', 'GARCH status'):
        out.loc[mask, field] = reasons.loc[mask]
    return out


def forecast_restriction(expiry, as_of):
    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        raise ValueError('Quote valuation requires an explicit timezone.')
    stamp = stamp.tz_convert(IST)
    if same_day(expiry, stamp):
        return SAME_DAY_STATUS
    if stamp.time() < time(15, 40):
        return 'Withheld: expiry RV comparison requires a completed-session snapshot; no validated intraday allocation.'
    return ''


def term_structure_forecast(frame, expiry, as_of):
    """Last-close model context, explicitly separate from a newer intraday IV."""
    quote = pd.Timestamp(as_of)
    if quote.tzinfo is None:
        raise ValueError('Quote valuation requires an explicit timezone.')
    quote = quote.tz_convert(IST)
    if same_day(expiry, quote):
        raise ValueError(SAME_DAY_STATUS)
    cutoff = quote.date() if quote.time() >= time(15, 40) else quote.date() - timedelta(days=1)
    data, last = _prepare_one_year_slice(frame, cutoff)
    expected_prior = last_trading_day_on_or_before(quote.date() - timedelta(days=1))
    if last < expected_prior:
        raise ValueError('Latest completed session is missing; refresh underlying history.')
    anchor = datetime.combine(last, time(15, 40), tzinfo=IST)
    model = expiry_forecast(data, expiry, anchor)
    matched = last == quote.date() and quote.time() >= time(15, 40)
    model.update({
        'Forecast as of': anchor.isoformat(),
        'Quote as of': quote.isoformat(),
        'Comparison ready': matched,
        'Forecast basis': 'Same close' if matched else 'Previous close · reference',
    })
    return model
