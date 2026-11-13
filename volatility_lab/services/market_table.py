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

    def rv(self, frame: pd.DataFrame, symbol: str, now: datetime, expiry: object, method: str) -> float:
        """Forecast annualised realised volatility to expiry without elapsed-session variance."""
        dates = pd.bdate_range(frame.Date.iloc[-1] + pd.Timedelta(days=1), pd.Timestamp(expiry))
        if not len(dates) or len(dates) > 245:
            raise MarketDataError('Unsupported forecast tenor.')
        key = (symbol, frame.Date.iloc[-1].isoformat(), len(frame), method, len(dates))
        if key not in self.paths:
            if method == 'GARCH':
                _, _, path, _ = fit_garch(frame, (len(dates),), min_observations=200)
                variances = (path['Daily conditional vol %'].to_numpy() / 100.0) ** 2 / 245.0
            else:
                returns = np.log(frame.Close / frame.Close.shift()).dropna()
                variances = np.full(len(dates), returns.tail(30).var())
            self.paths[key] = variances

        weights = np.array([0.0 if day.date() < now.date() else 1.0 for day in dates])
        for index, day in enumerate(dates):
            if day.date() == now.date():
                opening = datetime.combine(now.date(), time(9, 15), tzinfo=IST)
                closing = datetime.combine(now.date(), time(15, 40), tzinfo=IST)
                weights[index] = np.clip((closing - now).total_seconds() / (closing - opening).total_seconds(), 0.0, 1.0)
        years = (datetime.combine(pd.Timestamp(expiry).date(), time(15, 40), tzinfo=IST) - now).total_seconds() / (365.0 * 86400.0)
        if years <= 0:
            raise MarketDataError('Expiry must be after the valuation timestamp.')
        return float(np.sqrt(np.sum(self.paths[key] * weights) / years) * 100.0)


def atm_values(
    chain: pd.DataFrame,
    strikes: list[float] | None,
    spot: float,
    forward: float | None = None,
    years: float | None = None,
    rate: float = 0.066,
    use_black76: bool = True,
) -> tuple[float, float, float]:
    """Return (call_iv_pct, put_iv_pct, atm_iv_pct) at ATM strike, unifying Call/Put IV under Black-76 when LTPs are available."""
    if chain is None or chain.empty or spot is None or not np.isfinite(float(spot)) or float(spot) <= 0:
        return (np.nan, np.nan, np.nan)
    df = chain.copy()
    strike_col = 'strike' if 'strike' in df.columns else ('Strike' if 'Strike' in df.columns else 'strike_price')
    type_col = 'option_type' if 'option_type' in df.columns else ('Type' if 'Type' in df.columns else 'Kind')
    df[strike_col] = pd.to_numeric(df[strike_col], errors='coerce')
    df['iv'] = pd.to_numeric(df['iv'], errors='coerce')
    valid = df[np.isfinite(df[strike_col]) & df[type_col].isin(['CE', 'PE'])].copy()
    if valid.empty:
        return (np.nan, np.nan, np.nan)

    pairs = valid.groupby(strike_col)[type_col].agg(set)
    paired = [float(k) for k, kinds in pairs.items() if kinds == {'CE', 'PE'}]
    listed = sorted(float(x) for x in strikes) if strikes is not None and len(strikes) else sorted(valid[strike_col].unique())
    candidates = paired if use_black76 and paired else listed
    if not candidates:
        return (np.nan, np.nan, np.nan)

    ref_px = float(forward) if (forward is not None and np.isfinite(float(forward)) and float(forward) > 0) else float(spot)
    atm_k = min(candidates, key=lambda k: (abs(k - ref_px), k))
    ce_sub = valid[(valid[strike_col] == atm_k) & (valid[type_col] == 'CE')]
    pe_sub = valid[(valid[strike_col] == atm_k) & (valid[type_col] == 'PE')]
    if not use_black76:
        # chain_from_response stores broker percentages as fractions, including IV above 150%.
        call = float(ce_sub['iv'].iloc[0]) * 100 if len(ce_sub) == 1 else np.nan
        put = float(pe_sub['iv'].iloc[0]) * 100 if len(pe_sub) == 1 else np.nan
        call = call if np.isfinite(call) and call > 0 else np.nan
        put = put if np.isfinite(put) and put > 0 else np.nan
        return call, put, (call + put) / 2
    if ce_sub.empty or pe_sub.empty:
        return (np.nan, np.nan, np.nan)

    raw_c_iv = float(ce_sub['iv'].iloc[0])
    raw_p_iv = float(pe_sub['iv'].iloc[0])
    c_iv_pct = raw_c_iv * 100.0 if (np.isfinite(raw_c_iv) and 0 < raw_c_iv <= 1.5) else raw_c_iv
    p_iv_pct = raw_p_iv * 100.0 if (np.isfinite(raw_p_iv) and 0 < raw_p_iv <= 1.5) else raw_p_iv

    if use_black76 and ('ltp' in valid.columns or 'Mid' in valid.columns):
        px_col = 'ltp' if 'ltp' in valid.columns else 'Mid'
        c_px = float(pd.to_numeric(ce_sub[px_col], errors='coerce').iloc[0])
        p_px = float(pd.to_numeric(pe_sub[px_col], errors='coerce').iloc[0])
        t_years = float(years) if (years is not None and np.isfinite(float(years)) and float(years) > 0) else (8.0 / 365.0)
        if np.isfinite(c_px) and c_px > 0 and np.isfinite(p_px) and p_px > 0:
            fwd = float(forward) if (forward is not None and np.isfinite(float(forward)) and float(forward) > 0) else parity_implied_forward(atm_k, c_px, p_px, t_years, rate)
            b76_c = solve_black76_iv(c_px, fwd, atm_k, t_years, rate, 'CE')
            b76_p = solve_black76_iv(p_px, fwd, atm_k, t_years, rate, 'PE')
            if np.isfinite(b76_c) and np.isfinite(b76_p) and b76_c > 0 and b76_p > 0:
                c_iv_pct = float(b76_c * 100.0)
                p_iv_pct = float(b76_p * 100.0)

    if not (np.isfinite(c_iv_pct) and np.isfinite(p_iv_pct) and c_iv_pct > 0 and p_iv_pct > 0):
        return (np.nan, np.nan, np.nan)
    atm_iv_pct = 0.5 * (c_iv_pct + p_iv_pct)
    return (float(c_iv_pct), float(p_iv_pct), float(atm_iv_pct))


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
    try:
        stamp = pd.Timestamp(as_of)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(IST)
        quote_day = stamp.tz_localize(None).normalize()
        is_closed = (stamp.hour * 60 + stamp.minute) >= (15 * 60 + 30)
    except Exception:
        quote_day = hv_df['Date'].max()
        is_closed = True

    eligible = hv_df[hv_df['Date'] <= (quote_day if is_closed else (quote_day - pd.Timedelta(days=1)))].dropna(subset=['Historical vol 30D'])
    if eligible.empty:
        eligible = hv_df.dropna(subset=['Historical vol 30D'])
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
