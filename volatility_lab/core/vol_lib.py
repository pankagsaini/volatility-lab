"""Step 1: daily OHLC validation and historical/conditional volatility.

All results are decimal annualized volatilities unless named variance.
Windows and annualization use trading sessions, not calendar days.
"""
import numpy as np
import pandas as pd

YANG_ZHANG_VERSION = 'yang-zhang-2000-open-close-v3'


def validate_ohlc(frame):
    original = frame
    if isinstance(frame, pd.DataFrame) and frame.index.name == 'Date' and 'Date' not in frame.columns:
        frame = frame.reset_index()
    required = ['Date', 'Open', 'High', 'Low', 'Close']
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f'Missing columns: {sorted(missing)}')
    df = frame[required].copy()
    df['Date'] = pd.to_datetime(df['Date'], errors='raise')
    if df['Date'].isna().any() or df['Date'].dt.normalize().duplicated().any():
        raise ValueError('Dates must be present and unique daily sessions')
    prices = df[required[1:]].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError('OHLC prices must be finite and strictly positive')
    if ((prices.High < prices[['Open', 'Close', 'Low']].max(axis=1)) |
        (prices.Low > prices[['Open', 'Close', 'High']].min(axis=1))).any():
        raise ValueError('Each High/Low must contain the Open and Close')
    df[required[1:]] = prices
    out = df.sort_values('Date').set_index('Date')
    if getattr(original, '_ohlc_validated', False) and original.equals(out):
        return original
    out._ohlc_validated = True
    return out


def rolling_rv_suite(frame, window=20, lam=0.94, ann_factor=245):
    """Vectorized rolling volatility estimators; EWMA uses zero-mean squared log returns."""
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError('window must be an integer of at least 2 sessions')
    if not 0 < lam < 1 or not np.isfinite(ann_factor) or ann_factor <= 0:
        raise ValueError('Require 0 < lambda < 1 and positive annualization')
    df = validate_ohlc(frame)
    if len(df) < window + 1:
        raise ValueError(f'Need at least {window + 1} daily OHLC rows')
    o, h, l, c = (df[col] for col in ['Open', 'High', 'Low', 'Close'])
    returns = np.log(c / c.shift(1))
    overnight = np.log(o / c.shift(1))
    intraday = np.log(c / o)
    hl = np.log(h / l)
    rs = np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    overnight_var = overnight.rolling(window).var(ddof=1)
    rs_var = rs.rolling(window).mean().clip(lower=0.0)
    cc_var = returns.rolling(window).var(ddof=1)
    oc_var = intraday.rolling(window).var(ddof=1)
    intraday_var = k * oc_var + (1.0 - k) * rs_var
    yz_var = (overnight_var + intraday_var).clip(lower=0.0)
    parkinson_var = (hl ** 2).rolling(window).mean() / (4.0 * np.log(2.0))
    gk_var = (0.5 * (hl ** 2) - (2.0 * np.log(2.0) - 1.0) * (intraday ** 2)).rolling(window).mean().clip(lower=0.0)
    ewma_var = returns.dropna().pow(2).ewm(alpha=1.0 - lam, adjust=False).mean().reindex(df.index)
    return pd.DataFrame({
        'close_to_close_rv': np.sqrt(cc_var * ann_factor),
        'parkinson_rv': np.sqrt(parkinson_var * ann_factor),
        'garman_klass_rv': np.sqrt(gk_var * ann_factor),
        'rogers_satchell_rv': np.sqrt(rs_var * ann_factor),
        'yang_zhang_rv': np.sqrt(yz_var * ann_factor),
        'overnight_annualized_variance': overnight_var * ann_factor,
        'intraday_annualized_variance': intraday_var * ann_factor,
        'ewma_next_session_vol': np.sqrt(ewma_var * ann_factor),
    }, index=df.index)


def rv_snapshot(frame, window=20, lam=0.94, ann_factor=245):
    suite = rolling_rv_suite(frame, window=window, lam=lam, ann_factor=ann_factor)
    last = suite.iloc[-1]
    return {
        'as_of': suite.index[-1].isoformat(),
        'yang_zhang_version': YANG_ZHANG_VERSION,
        'window_trading_sessions': window,
        'annualization_sessions': ann_factor,
        'close_to_close_rv': float(last['close_to_close_rv']),
        'parkinson_rv': float(last['parkinson_rv']),
        'garman_klass_rv': float(last['garman_klass_rv']),
        'rogers_satchell_rv': float(last['rogers_satchell_rv']),
        'yang_zhang_rv': float(last['yang_zhang_rv']),
        'overnight_annualized_variance': float(last['overnight_annualized_variance']),
        'intraday_annualized_variance': float(last['intraday_annualized_variance']),
        'ewma_next_session_vol': float(last['ewma_next_session_vol']),
    }
