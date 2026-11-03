"""Step 1: daily OHLC validation and historical/conditional volatility.

All results are decimal annualized volatilities unless named variance.
Windows and annualization use trading sessions, not calendar days.
"""
import numpy as np
import pandas as pd


def validate_ohlc(frame):
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
    return df.sort_values('Date').set_index('Date')


def rv_snapshot(frame, window=20, lam=0.94, ann_factor=252):
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError('window must be an integer of at least 2 sessions')
    if not 0 < lam < 1 or not np.isfinite(ann_factor) or ann_factor <= 0:
        raise ValueError('Require 0 < lambda < 1 and positive annualization')
    df = validate_ohlc(frame)
    if len(df) < window + 1:
        raise ValueError(f'Need at least {window + 1} daily OHLC rows')
    o, h, l, c = (df[col] for col in ['Open', 'High', 'Low', 'Close'])
    returns = np.log(c / c.shift(1)).dropna()
    overnight = np.log(o / c.shift(1)).tail(window)
    intraday = np.log(c / o).tail(window)
    rs = (np.log(h/c)*np.log(h/o) + np.log(l/c)*np.log(l/o)).tail(window)
    k = 0.34 / (1.34 + (window + 1)/(window - 1))
    overnight_var = float(overnight.var(ddof=1))
    intraday_var = float(k*intraday.var(ddof=1) + (1-k)*rs.mean())
    # Explicit zero-mean EWMA recursion; initialize with first squared return.
    # Every observed return updates the next-session conditional variance.
    ewma_var = float(returns.iloc[0]**2)
    for ret in returns.iloc[1:]:
        ewma_var = lam*ewma_var + (1-lam)*float(ret)**2
    return {
        'as_of': df.index[-1].isoformat(),
        'window_trading_sessions': window,
        'annualization_sessions': ann_factor,
        'close_to_close_rv': float(returns.tail(window).std(ddof=1)*np.sqrt(ann_factor)),
        'yang_zhang_rv': float(np.sqrt(max(0, overnight_var+intraday_var)*ann_factor)),
        'overnight_annualized_variance': overnight_var*ann_factor,
        'intraday_annualized_variance': intraday_var*ann_factor,
        'ewma_next_session_vol': float(np.sqrt(ewma_var*ann_factor)),
    }
