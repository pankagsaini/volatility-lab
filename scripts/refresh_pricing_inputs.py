"""Fetch official model inputs for the website; never fabricate fallback inputs.

Default: write pricing-inputs.json at the repository root and under docs/.
--output: write only to a deployment artifact directory or another explicit path.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'volatility_lab'))
from pricing_sources import fetch_official_inputs
from collector_support import atomic_json


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--update-python-cache', action='store_true')
    args = parser.parse_args()
    inputs = fetch_official_inputs()
    for path in ([args.output] if args.output else [ROOT / 'pricing-inputs.json', ROOT / 'docs/pricing-inputs.json']):
        atomic_json(path, inputs)
    if args.update_python_cache:
        atomic_json(ROOT / 'volatility_lab/data/risk_free_rate.json', inputs['rate'])
        atomic_json(ROOT / 'volatility_lab/data/dividend_yields.json', inputs['dividends'])
    print(json.dumps({'rate_yield_percent':inputs['rate']['yield_percent'],
        'continuous_rate_percent':100*inputs['rate']['model_rate'],
        'dividends':{k:{'yield_percent':v['yield_percent'],'as_of':v['as_of']} for k,v in inputs['dividends'].items()}}))
