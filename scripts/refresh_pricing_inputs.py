"""Fetch official model inputs for the website; never fabricate fallback inputs.

Default: write pricing-inputs.json at the repository root and under docs/.
--output: write only to a deployment artifact directory or another explicit path.
--retain --output FILE: recover a verified dated publication after an official-source outage.
"""
import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from volatility_lab.pricing.pricing_policy import number, recorded_inputs, timestamp
from volatility_lab.pricing.pricing_sources import fetch_official_inputs
from volatility_lab.storage.collector_support import atomic_json
from scripts.public_artifact import fetch_json


def source_dates(bundle, now):
    """Verify the original checks, even when the browser's allowed age has elapsed."""
    symbols = ('NIFTY', 'BANKNIFTY')
    if (not isinstance(bundle, dict) or type(bundle.get('schema')) is not int or bundle['schema'] != 1
            or not isinstance(bundle.get('dividends'), dict) or set(bundle['dividends']) != set(symbols)):
        raise ValueError('Unknown pricing inputs publication')
    checked = timestamp(bundle.get('checked_at'))
    if checked is None or checked > now:
        raise ValueError('Invalid publication check time')
    rate = bundle.get('rate')
    for row in (rate, *bundle['dividends'].values()):
        if (not isinstance(row, dict) or not isinstance(row.get('checked_at'), str)
                or timestamp(row['checked_at']) != checked or not isinstance(row.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Incomplete original source evidence for pricing inputs')
    y = number(rate.get('yield_percent'))
    if y is None or not 0 < y < 30:
        raise ValueError('Invalid original T-bill auction yield')
    expected_rate = math.log1p(y / 100 * 91 / 365) * 365 / 91
    dates = []
    for symbol in symbols:
        div = bundle['dividends'][symbol]
        r, q = recorded_inputs({'rate_source': rate, 'dividend_source': div}, symbol, checked)
        percent = number(div.get('yield_percent'))
        if (r is None or not math.isclose(r, expected_rate) or q is None
                or percent is None or not math.isclose(q, percent / 100)):
            raise ValueError('Published model inputs disagree with the dated RBI or NSE source')
        dates.append(date.fromisoformat(div['as_of']))
    return checked, dates


def retain(output):
    now = datetime.now(timezone.utc)
    published = fetch_json('pricing-inputs.json', 64 * 1024)
    checked, dates = source_dates(published, now)
    selected = published
    if output.exists():
        try:
            checkout = json.loads(output.read_bytes())
            checkout_checked, checkout_dates = source_dates(checkout, now)
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if checkout_checked > checked and all(a >= b for a, b in zip(checkout_dates, dates)):
                selected = checkout
    atomic_json(output, selected)
    print(f"Retained verified RBI and NSE inputs originally checked at {selected['checked_at']}.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--retain', action='store_true')
    parser.add_argument('--update-python-cache', action='store_true')
    args = parser.parse_args()
    if args.retain:
        if not args.output or args.update_python_cache:
            parser.error('--retain requires --output and applies only to the website artifact')
        retain(args.output)
        sys.exit(0)
    inputs = fetch_official_inputs()
    for path in ([args.output] if args.output else [ROOT / 'pricing-inputs.json', ROOT / 'docs/pricing-inputs.json']):
        atomic_json(path, inputs)
    if args.update_python_cache:
        from volatility_lab.runtime.settings import data_dir
        atomic_json(data_dir() / 'risk_free_rate.json', inputs['rate'])
        atomic_json(data_dir() / 'dividend_yields.json', inputs['dividends'])
    print(json.dumps({'rate_yield_percent':inputs['rate']['yield_percent'],
        'continuous_rate_percent':100*inputs['rate']['model_rate'],
        'dividends':{k:{'yield_percent':v['yield_percent'],'as_of':v['as_of']} for k,v in inputs['dividends'].items()}}))
