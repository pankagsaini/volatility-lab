"""Transfer the two validated daily candle histories from Pages to the paper runner."""

from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import sys

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from volatility_lab.core.market_calendar import IST
from volatility_lab.core.vol_lib import validate_ohlc
from volatility_lab.storage.references import REFERENCE_DIR, text_digest

URL = "https://volatilitylab.in/paper-risk-history.json"
NAMES = ("nifty_one_year.csv", "banknifty_one_year.csv")
MAX_BYTES = 4_000_000


def validate(bundle):
    if (bundle.get("schema") != 1 or bundle.get("provider") != "Upstox daily candles"
            or set(bundle["files"]) != set(NAMES)):
        raise ValueError("Unknown daily history bundle")
    for name in NAMES:
        entry = bundle["files"][name]
        csv = entry["csv"]
        if len(csv) > MAX_BYTES // 2 or text_digest(csv.encode()) != entry["sha256"]:
            raise ValueError("Daily history checksum mismatch")
        frame = pd.read_csv(StringIO(csv), dtype={"Date": str})
        if (list(frame.columns) != ["Date", "Open", "High", "Low", "Close"] or len(frame) < 31
                or not frame.Date.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all()
                or not frame.Date.is_monotonic_increasing or frame.Date.iloc[-1] != bundle["through"]
                or frame.Date.iloc[-1] > datetime.now(IST).date().isoformat()):
            raise ValueError("Daily history has invalid session dates or coverage")
        validate_ohlc(frame)
    return bundle


def write_bundle(source, output):
    files = {}
    for name in NAMES:
        csv = (source / name).read_text(encoding="utf-8")
        files[name] = {"csv": csv, "sha256": text_digest(csv.encode())}
    through = files[NAMES[0]]["csv"].splitlines()[-1].split(",")[0]
    bundle = validate({"schema": 1, "provider": "Upstox daily candles", "through": through, "files": files})
    output.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    print(f"Published verified daily risk histories through {through}.")


def fetch_bundle():
    options = {"timeout": (10, 30), "allow_redirects": False, "stream": True}
    token = os.environ.get("CI_READ_TOKEN", "").strip()
    if token:
        options["headers"] = {"X-CI-Read-Token": token}
    with requests.get(URL, **options) as response:
        if response.status_code == 404:
            raise FileNotFoundError("Public daily history bundle has not been published")
        if response.status_code != 200:
            raise RuntimeError(f"Daily history request failed (HTTP {response.status_code})")
        body = bytearray()
        for chunk in response.iter_content(65536):
            if len(body) + len(chunk) > MAX_BYTES:
                raise ValueError("Daily history bundle exceeds size limit")
            body.extend(chunk)
    return validate(json.loads(body))


def retain(output):
    try:
        previous = fetch_bundle()
    except FileNotFoundError:
        # The first deployment can serve the dated release floor during an outage.
        manifest = json.loads((REFERENCE_DIR / "reference-manifest.json").read_bytes())
        for name in NAMES:
            if text_digest((REFERENCE_DIR / name).read_bytes()) != manifest["files"][name]:
                raise ValueError("Bundled daily history checksum mismatch")
        write_bundle(REFERENCE_DIR, output)
    else:
        output.write_text(json.dumps(previous, separators=(",", ":")), encoding="utf-8")
        print(f"Retained the previously published daily risk histories through {previous['through']}.")


def install(destination):
    bundle = fetch_bundle()
    # This is the runner's isolated data directory. Validate the complete pair
    # before opening either destination or passing any capture to the worker.
    for name in NAMES:
        (destination / name).write_bytes(bundle["files"][name]["csv"].encode())
    print(f"Verified published daily risk histories through {bundle['through']}.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retain", required=True, type=Path)
    retain(parser.parse_args().retain)
