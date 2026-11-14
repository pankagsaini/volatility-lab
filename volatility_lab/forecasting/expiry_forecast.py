"""Expiry variance matching; consistent trading-session horizon annualization (with fast horizon caching)."""
from datetime import timedelta
from functools import lru_cache
import numpy as np
import pandas as pd
from forecast import HORIZON_CAP, fit_garch
from vol_lib import validate_ohlc, rv_snapshot
try:
    from market_data import IST
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')
from market_calendar import LAST_SOURCED_HOLIDAY_YEAR, expiry_close, is_trading_day, last_trading_day_on_or_before, session_window

VERSION = 'expiry-v6-session-close-garch265'
TERM_VERSION = 'term-v1-last-completed-close'
CALENDAR_SOURCE = 'https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf'


@lru_cache(maxsize=128)
def _cached_sessions(start_iso: str, end_iso: str) -> tuple:
    start_d = pd.Timestamp(start_iso).date()
    end_d = pd.Timestamp(end_iso).date()
    if start_d > end_d:
        return ()
    return tuple(day.date() for day in pd.date_range(start_d, end_d)
                 if is_trading_day(day.date(), include_special=True))


def sessions(start, end):
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    if start_d > end_d:
        return []
    return list(_cached_sessions(start_d.isoformat(), end_d.isoformat()))


def _prepare_one_year_slice(frame: pd.DataFrame, cutoff_date) -> tuple[pd.DataFrame, object]:
    """Validate OHLC and extract the 1-year training window once per unique (frame, cutoff_date)."""
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
    return data, last


def _get_base_expiry_models(data: pd.DataFrame) -> tuple[float, np.ndarray | None, str]:
    """The GARCH MLE itself is cached on the complete return sequence."""
    ewma_rv_pct = 100 * rv_snapshot(data)['ewma_next_session_vol']

    garch_vars = None
    garch_status = 'Ready'
    try:
        _, diagnostics, path, _ = fit_garch(data, (HORIZON_CAP,), min_observations=200)
        garch_vars = (path['Daily conditional vol %'].to_numpy(dtype=float) / 100.0) ** 2 / 245.0
        garch_status = '; '.join(diagnostics['warnings']) or 'Ready'
    except (ValueError, RuntimeError, FloatingPointError, ImportError) as error:
        garch_status = str(error)

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
    end = expiry_close(expiry)
    years = (end - now).total_seconds() / (365 * 86400)
    if years <= 0:
        raise ValueError('Expiry has elapsed at the quote valuation time.')
    cutoff = completed_cutoff(now)
    data, last = _prepare_one_year_slice(frame, cutoff)

    dates = sessions(last + timedelta(days=1), expiry)
    if not dates or len(dates) > HORIZON_CAP:
        raise ValueError('Unsupported expiry forecast horizon.')
    # Only a completed-session anchor is eligible. Every later session contributes
    # one daily conditional variance. Elapsed weekends and holidays add no rows.
    total_weights = len(dates)

    ewma_rv_pct, garch_vars, garch_status = _get_base_expiry_models(data)

    result = {
        'Expiry': str(expiry),
        'As of': now.isoformat(),
        'Training through': str(last),
        'DTE': years * 365.0,
        'Trading sessions': len(dates),
        'Session equivalents': total_weights,
        'IV': iv,
        'EWMA expiry RV': ewma_rv_pct,
        'GARCH expiry RV': np.nan,
        'GARCH status': garch_status,
        'Version': VERSION,
        'Calendar status': 'Provisional future holidays' if expiry.year > LAST_SOURCED_HOLIDAY_YEAR else 'Sourced annual calendar',
    }

    if garch_vars is not None and len(garch_vars) >= len(dates):
        variances = garch_vars[:len(dates)]
        mean_session_var = float(np.mean(variances))
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


def completed_cutoff(stamp):
    window = session_window(stamp.date())
    return stamp.date() if window and stamp >= window[1] else stamp.date() - timedelta(days=1)


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
    intraday_mask = (~same_day_mask) & stamps.map(lambda stamp: completed_cutoff(stamp) != stamp.date())
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
    if completed_cutoff(stamp) != stamp.date():
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
    cutoff = completed_cutoff(quote)
    data, last = _prepare_one_year_slice(frame, cutoff)
    expected_prior = last_trading_day_on_or_before(quote.date() - timedelta(days=1))
    if last < expected_prior:
        raise ValueError('Latest completed session is missing; refresh underlying history.')
    window = session_window(last)
    if window is None:
        raise ValueError(f'No verified training close for {last}.')
    anchor = window[1]
    model = expiry_forecast(data, expiry, anchor)
    matched = last == quote.date() and quote >= anchor
    model.update({
        'Forecast as of': anchor.isoformat(),
        'Quote as of': quote.isoformat(),
        'Comparison ready': matched,
        'Forecast basis': 'Same close' if matched else 'Previous close · reference',
    })
    return model
