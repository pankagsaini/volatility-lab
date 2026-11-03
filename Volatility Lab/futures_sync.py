"""Refresh both futures indices independently, reusing incremental history caches."""
from futures_rolling import fetch_rolling
from market_data import MarketDataError

SYMBOLS = ('NIFTY', 'BANKNIFTY')

def refresh_all(engine, client, root, history_only=False):
    errors = []
    for symbol in SYMBOLS:
        try:
            result = engine.refresh(client, root, symbol, history_only=history_only)
            errors.extend(f"{symbol}: {error}" for error in result.get('history_errors', []))
        except (MarketDataError, ValueError, KeyError, TypeError, OSError) as error:
            errors.append(f'{symbol}: dated history / quotes: {error}')
        try:
            fetch_rolling(engine, client, root, symbol)
        except (MarketDataError, ValueError, KeyError, TypeError, OSError) as error:
            errors.append(f'{symbol}: rolling history: {error}')
    return errors
