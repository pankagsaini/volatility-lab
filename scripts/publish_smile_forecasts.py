"""Publish annualized variance paths from completed Upstox index candles for the public Smile."""

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from arch.utility.exceptions import PerformanceWarning

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'volatility_lab'))
from forecast import fit_garch
from vol_lib import rv_snapshot, validate_ohlc

INDICES = {'NIFTY': 'NSE_INDEX|Nifty 50', 'BANKNIFTY': 'NSE_INDEX|Nifty Bank'}
IST = ZoneInfo('Asia/Kolkata')


def broker_history(instrument, end):
    key = quote(instrument, safe='')
    start = end - timedelta(days=380)

    def fetch(first):
        url = f'https://api.upstox.com/v3/historical-candle/{key}/days/1/{end}/{first}'
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if payload.get('status') != 'success':
            raise ValueError('Upstox did not return daily candles')
        return validate_ohlc(pd.DataFrame(
            [(candle[0][:10], *candle[1:5]) for candle in payload['data']['candles']
             if first.isoformat() <= candle[0][:10] <= end.isoformat()],
            columns=['Date', 'Open', 'High', 'Low', 'Close'],
        ))

    history = fetch(start)
    # The broker's long-range response can lag the shorter range after the close.
    if history.index[-1].date() < end:
        try:
            recent = fetch(end - timedelta(days=95))
        except (OSError, ValueError) as error:
            print(f'Recent-close retry unavailable for {instrument} ({error})', file=sys.stderr)
        else:
            history = pd.concat([history, recent.loc[recent.index > history.index[-1]]])
    return history


def forecast_paths(history):
    # Keep recent origins so a dated quote can use its own prior close after a fresh fit is published.
    result = {}
    for close in history.index[-5:]:
        frame = history.loc[(history.index <= close) & (history.index > close - pd.DateOffset(years=1))]
        if len(frame) < 201 or (close - frame.index[0]).days < 345:
            continue
        ewma = rv_snapshot(frame)['ewma_next_session_vol'] * 100
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', PerformanceWarning)
                _, _, path, _ = fit_garch(frame, (245,), min_observations=200, stationary_retry=True)
        except (ValueError, RuntimeError, FloatingPointError) as error:
            print(f'GARCH withheld for {close.date()} ({error})', file=sys.stderr)
            garch = None
        else:
            garch = [round(float(vol) ** 2, 6) for vol in path['Daily conditional vol %']]
        result[str(close.date())] = {'ewma': round(float(ewma), 6), 'garch_variances': garch}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/data/smile-forecasts.json')
    parser.add_argument('--through', type=date.fromisoformat, default=datetime.now(IST).date() - timedelta(days=1))
    args = parser.parse_args()
    if args.through >= datetime.now(IST).date():
        parser.error('--through must be before the current IST date')

    artifact = {'schema': 1, 'source': 'Upstox daily index candles', 'models': {}}
    for symbol, instrument in INDICES.items():
        try:
            paths = forecast_paths(broker_history(instrument, args.through))
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            print(f'{symbol} forecasts unavailable ({error})', file=sys.stderr)
            continue
        if paths:
            artifact['models'][symbol] = paths
            print(f'{symbol} fitted through {max(paths)}')

    if not artifact['models']:
        raise SystemExit('No valid forecast origins. Retaining the previous artifact.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(artifact, allow_nan=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(args.output)


if __name__ == '__main__':
    main()
