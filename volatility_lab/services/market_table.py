"""Market table, ATM Black-76 & broker IV extraction, and historical volatility alignment."""
from __future__ import annotations
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT

from datetime import datetime, time, timedelta
import io
from pathlib import Path
import time as clock
from typing import Any
import numpy as np
import pandas as pd

from forecast import fit_garch

try:
    from market_data import IST, MarketDataError, history_cutoff
except ImportError:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo('Asia/Kolkata')
    from market_data import MarketDataError, history_cutoff
from market_calendar import last_trading_day_on_or_before

import vol_lib
from option_math import (
    black76_greeks,
    parity_implied_forward,
    solve_black76_iv,
)

ROOT = PACKAGE_ROOT
DATA = runtime_data_dir()
INDICES = {
    'NIFTY': 'NIFTY 50',
    'BANKNIFTY': 'NIFTY BANK',
}

COLUMNS = [
    'Instrument',
    'Expiry',
    'Type',
    'Price',
    '% change',
    'Call IV',
    'Put IV',
    'IV',
    'HV10',
    'HV30',
    'IV-RV Spread',
    '25 Delta reversal',
    'Synthetic future',
    'Month future LTP',
    'As of',
    'Source',
]


def one_year(frame: pd.DataFrame, cutoff: Any = None) -> pd.DataFrame:
    """Restrict a validated daily OHLC frame to the trailing 1-year window up to cutoff."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=['Date', 'Open', 'High', 'Low', 'Close'])
    df = vol_lib.validate_ohlc(frame).reset_index()
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None).dt.normalize()
    if cutoff is not None:
        try:
            cut_ts = pd.Timestamp(cutoff).tz_localize(None).normalize()
            df = df[df['Date'] <= cut_ts]
        except Exception:
            pass
    if df.empty:
        return df
    end_dt = df['Date'].max()
    start_dt = end_dt - pd.DateOffset(years=1)
    return df[df['Date'] >= start_dt].copy().reset_index(drop=True)


def historical_vol(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute trailing 10D and 30D annualized realized volatility series from daily OHLC."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=['Date', 'Historical vol 10D', 'Historical vol 30D'])
    df = vol_lib.validate_ohlc(frame).reset_index()
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None).dt.normalize()
    df = df.sort_values('Date').reset_index(drop=True)
    log_ret = np.log(df['Close'] / df['Close'].shift(1))
    hv10 = log_ret.rolling(10).std(ddof=1) * np.sqrt(245.0) * 100.0
    hv30 = log_ret.rolling(30).std(ddof=1) * np.sqrt(245.0) * 100.0
    return pd.DataFrame({
        'Date': df['Date'],
        'Historical vol 10D': hv10,
        'Historical vol 30D': hv30,
    })


class Monitor:
    """Shared state and broker access for expiry and dated-futures monitors."""

    def __init__(self) -> None:
        self.master_date = None
        self.master_provider = None
        self.master_checked_at = None
        self.nfo = None
        self.nse = None
        self.histories: dict[tuple[str, object], pd.DataFrame] = {}
        self.paths: dict[tuple[object, ...], np.ndarray] = {}
        self.last_request = 0.0

    def request(self, client: Any, *args: Any, **kwargs: Any) -> Any:
        """Issue a broker request with the collector's minimum spacing."""
        clock.sleep(max(0.0, 1.05 - (clock.monotonic() - self.last_request)))
        self.last_request = clock.monotonic()
        return client._request(*args, **kwargs)

    def masters(self, client: Any, now: datetime) -> None:
        """Refresh NFO/NSE masters at most once per hour for a provider/day."""
        stale = self.master_checked_at is None or (now - self.master_checked_at).total_seconds() >= 3600
        if (
            self.master_provider == getattr(client, 'provider', 'Upstox')
            and self.master_date == now.date()
            and not stale
        ):
            return

        nfo = pd.read_csv(io.StringIO(self.request(client, '/instruments/NFO', csv_response=True)))
        nse = pd.read_csv(io.StringIO(self.request(client, '/instruments/NSE', csv_response=True)))
        nfo['expiry'] = pd.to_datetime(nfo['expiry'], errors='coerce').dt.date
        from futures_rolling import remember_listed_expiries

        remember_listed_expiries(nfo, now)
        self.master_provider = getattr(client, 'provider', 'Upstox')
        self.nfo = nfo
        self.nse = nse
        self.master_date = now.date()
        self.master_checked_at = now

    def history(self, client: Any, symbol: str, now: datetime) -> pd.DataFrame:
        """Return verified daily history through the latest eligible closing session."""
        from collector_support import atomic_csv

        cutoff = history_cutoff(now)
        cache_key = (symbol, cutoff)
        expected = last_trading_day_on_or_before(cutoff)
        cached = self.histories.get(cache_key)
        if cached is not None and not cached.empty and cached.Date.iloc[-1].date() >= expected:
            return cached

        archive = DATA / 'underlying_history'
        saved = None
        files = sorted(archive.glob(symbol.lower() + '_*.csv')) if archive.exists() else []
        if files:
            try:
                saved = vol_lib.validate_ohlc(pd.read_csv(files[-1], parse_dates=['Date'])).reset_index()
                saved = saved[saved.Date <= pd.Timestamp(cutoff)]
                view = one_year(saved, cutoff)
                if len(view) >= 201 and view.Date.iloc[-1].date() >= expected:
                    atomic_csv(view, DATA / f'{symbol.lower()}_one_year.csv', index=False, date_format='%Y-%m-%d')
                    self.histories[cache_key] = view
                    return view
            except (OSError, ValueError, KeyError):
                saved = None

        if self.nse is None:
            raise MarketDataError('Instrument master is unavailable.')
        matches = self.nse[
            (self.nse.tradingsymbol == INDICES[symbol])
            & (self.nse.segment == 'INDICES')
        ]
        if len(matches) != 1:
            raise MarketDataError('Index history instrument could not be resolved.')
        token = int(matches.instrument_token.iloc[0])
        if saved is not None and len(saved) >= 201:
            recent = client.daily_history(token, now, start_date=saved.Date.iloc[-1].date() - timedelta(days=5))
            frame = pd.concat([saved, recent]).drop_duplicates('Date', keep='last').sort_values('Date').reset_index(drop=True)
            frame = frame[frame.Date >= pd.Timestamp(cutoff) - pd.Timedelta(days=729)]
        else:
            frame = client.daily_history(token, now)

        full_history = frame.copy()
        view = one_year(frame, cutoff)
        if len(view) < 201:
            raise MarketDataError('Fewer than 201 daily candles in the one-year window.')
        if (now.date() - view.Date.iloc[-1].date()).days > 7:
            raise MarketDataError('Historical candles are stale.')

        DATA.mkdir(exist_ok=True)
        archive.mkdir(exist_ok=True)
        full_history.to_csv(
            archive / f'{symbol.lower()}_{now.strftime("%Y%m%dT%H%M%S%f")}.csv',
            index=False,
            date_format='%Y-%m-%d',
        )
        atomic_csv(view, DATA / f'{symbol.lower()}_one_year.csv', index=False, date_format='%Y-%m-%d')
        self.histories[cache_key] = view
        return view

    def rv(self, frame, symbol, now, expiry, method):
        """Return the annualized last-close forecast for the selected estimator."""
        from expiry_forecast import term_structure_forecast
        if method not in ('GARCH', 'EWMA'):
            raise MarketDataError('Unknown expiry forecast method.')
        try:
            result = term_structure_forecast(frame, expiry, now)
        except ValueError as exc:
            raise MarketDataError(str(exc)) from exc
        return float(result[f'{method} expiry RV'])


def atm_values(
    chain: pd.DataFrame,
    strikes: list[float] | None,
    spot: float,
    forward: float | None = None,
    years: float | None = None,
    rate: float | None = None,
    use_black76: bool = False,
    *, as_of=None, expiry=None,
) -> tuple[float, float, float]:
    """Return ATM IVs in percent. Model IV requires contemporaneous option books."""
    from option_math import black76_chain_greeks
    missing = (np.nan, np.nan, np.nan)
    if chain is None or chain.empty or not np.isfinite(float(spot)) or spot <= 0:
        return missing
    df = chain.rename(columns={'Strike':'strike', 'strike_price':'strike', 'Type':'option_type', 'Kind':'option_type'}).copy()
    if not {'strike', 'option_type'}.issubset(df):
        return missing
    df['strike'] = pd.to_numeric(df['strike'], errors='coerce')
    candidates = pd.to_numeric(pd.Series(strikes if strikes is not None and len(strikes) else df['strike']), errors='coerce')
    candidates = candidates[np.isfinite(candidates) & (candidates > 0)]
    if candidates.empty:
        return missing
    ref = forward if forward is not None and np.isfinite(forward) and forward > 0 else spot
    atm = min(candidates, key=lambda k: (abs(k - ref), k))
    if use_black76:
        if years is None or rate is None:
            return missing
        df = black76_chain_greeks(df, spot=spot, forward=forward, years=years, rate=rate, as_of=as_of, expiry=expiry)
        iv_column = 'iv_b76'
    else:
        iv_column = 'iv'  # normalized broker decimals, including values over 100%
    if iv_column not in df:
        return missing
    def leg(kind):
        values = pd.to_numeric(df.loc[(df.strike == atm) & (df.option_type == kind), iv_column], errors='coerce')
        return float(values.iloc[0]) * 100 if len(values) == 1 and np.isfinite(values.iloc[0]) and values.iloc[0] > 0 else np.nan
    call, put = leg('CE'), leg('PE')
    return call, put, (call + put) / 2


def aligned_spread(iv: float, as_of: Any, source: str, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    """Align an option snapshot's IV against trailing HV10 and HV30 from daily OHLC candles."""
    if frame is None or frame.empty:
        return {
            'HV10': np.nan,
            'HV30': np.nan,
            'IV-RV Spread': np.nan,
            'RV date': None,
            'RV status': 'Daily price history unavailable',
        }
    hv_df = historical_vol(frame)
    if hv_df.empty:
        return {
            'HV10': np.nan,
            'HV30': np.nan,
            'IV-RV Spread': np.nan,
            'RV date': None,
            'RV status': 'Insufficient daily candles',
        }
    from expiry_forecast import completed_cutoff
    try:
        stamp = pd.Timestamp(as_of)
        if stamp.tzinfo is None:
            raise ValueError('A timezone-aware valuation is required.')
        stamp = stamp.tz_convert(IST)
        quote_day = stamp.tz_localize(None).normalize()
        cutoff = pd.Timestamp(completed_cutoff(stamp))
    except (TypeError, ValueError, OverflowError):
        return {'HV10': np.nan, 'HV30': np.nan, 'IV-RV Spread': np.nan,
                'RV date': None, 'RV status': 'Valuation time unavailable'}
    eligible = hv_df[hv_df['Date'] <= cutoff].dropna(subset=['Historical vol 10D'])
    if eligible.empty:
        return {
            'HV10': np.nan,
            'HV30': np.nan,
            'IV-RV Spread': np.nan,
            'RV date': None,
            'RV status': 'HV30 warm-up incomplete',
        }
    last_row = eligible.iloc[-1]
    hv10_val = float(last_row['Historical vol 10D']) if pd.notna(last_row['Historical vol 10D']) else np.nan
    hv30_val = float(last_row['Historical vol 30D'])
    rv_date = pd.Timestamp(last_row['Date']).strftime('%Y-%m-%d')
    rv_status = 'Same-session close' if pd.Timestamp(last_row['Date']) == quote_day else 'Prior completed session'
    spread_val = (float(iv) - hv30_val) if (iv is not None and np.isfinite(float(iv)) and np.isfinite(hv30_val)) else np.nan
    return {
        'HV10': hv10_val,
        'HV30': hv30_val,
        'IV-RV Spread': spread_val,
        'RV date': rv_date,
        'RV status': rv_status,
    }


def chart_iv_spread(iv_history: pd.DataFrame, hv_frame: pd.DataFrame | None) -> pd.DataFrame:
    """Merge historical IV observations with daily HV30 to compute IV-RV Spread and 5-session relative spread."""
    if iv_history is None or iv_history.empty:
        return pd.DataFrame(columns=['Date', 'IV', 'IV-RV Spread', 'Relative spread · 5-session %', 'RV status', 'Source'])
    iv_df = iv_history.copy()
    iv_df['Date'] = pd.to_datetime(iv_df['Date']).dt.tz_localize(None).dt.normalize()
    iv_df['IV'] = pd.to_numeric(iv_df['IV'], errors='coerce')
    iv_df = iv_df.sort_values('Date').reset_index(drop=True)

    if hv_frame is not None and not hv_frame.empty:
        hv_clean = hv_frame[['Date', 'Historical vol 30D']].copy()
        hv_clean['Date'] = pd.to_datetime(hv_clean['Date']).dt.tz_localize(None).dt.normalize()
        merged = pd.merge_asof(iv_df, hv_clean.sort_values('Date'), on='Date', direction='backward')
        merged['IV-RV Spread'] = np.where(
            merged['IV'].notna() & merged['Historical vol 30D'].notna(),
            merged['IV'] - merged['Historical vol 30D'],
            pd.to_numeric(merged.get('Spread'), errors='coerce'),
        )
        merged['RV status'] = np.where(merged['Historical vol 30D'].notna(), 'Same-session close', 'HV30 unavailable')
    else:
        merged = iv_df.copy()
        merged['IV-RV Spread'] = pd.to_numeric(merged.get('Spread'), errors='coerce')
        merged['RV status'] = 'HV30 unavailable'

    base_iv = merged['IV'].rolling(5, min_periods=1).mean()
    merged['Relative spread · 5-session %'] = (merged['IV-RV Spread'] / base_iv.replace(0, np.nan)) * 100.0
    return merged
