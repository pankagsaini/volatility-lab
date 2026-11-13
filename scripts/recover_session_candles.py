"""Recover broker-reported minute candles for instruments in a private session archive."""
import argparse
import concurrent.futures
import gzip
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

IST = timezone(timedelta(hours=5, minutes=30))
SOURCE = "https://api.upstox.com/v3/historical-candle"
LOCK = threading.Lock()
REQUEST_TIMES = deque()
COOLDOWN = 0.0


def throttle():
    while True:
        with LOCK:
            now = time.monotonic()
            while REQUEST_TIMES and now - REQUEST_TIMES[0] >= 1801:
                REQUEST_TIMES.popleft()
            wait = COOLDOWN - now
            for count, duration in ((15, 1.05), (430, 61), (1880, 1801)):
                if len(REQUEST_TIMES) >= count:
                    wait = max(wait, REQUEST_TIMES[-count] + duration - now)
            if wait <= 0:
                REQUEST_TIMES.append(now)
                return
        time.sleep(min(wait, 5))


def discover(files):
    keys = {}
    # Start with the newest chain's ATM and indexes, then include all seen intraday strikes.
    for file in files:
        with gzip.open(file, "rt", encoding="utf-8") as inp:
            data = json.load(inp)
        for rec in data["records"]:
            if rec["label"] == "quotes":
                for q in rec["data"].values():
                    key = q.get("instrument_token")
                    if key:
                        keys[key] = {"symbol": q.get("symbol"), "type": "quote"}
            elif rec["label"].startswith(("NIFTY|", "BANKNIFTY|")):
                for row in sorted(rec["data"], key=lambda r: abs(r["strike_price"] - r["underlying_spot_price"])):
                    for leg in ("call_options", "put_options"):
                        key = (row.get(leg) or {}).get("instrument_key")
                        if key:
                            keys[key] = {"chain": rec["label"], "strike": row["strike_price"], "side": leg.split("_")[0]}
    return keys


def fetch(key, day):
    global COOLDOWN
    encoded = quote(key, safe="")
    current = datetime.now(IST).date().isoformat() == day
    url = f"{SOURCE}/intraday/{encoded}/minutes/1" if current else f"{SOURCE}/{encoded}/minutes/1/{day}/{day}"
    for attempt in range(4):
        throttle()
        try:
            req = Request(url, headers={"Accept": "application/json", "User-Agent": "VolatilityLab/1.0"})
            with urlopen(req, timeout=35) as response:
                payload = json.load(response)
            rows = payload["data"]["candles"]
            if payload.get("status") != "success" or not isinstance(rows, list):
                raise ValueError("Invalid broker response")
            if any(len(r) != 7 or datetime.fromisoformat(r[0]).astimezone(IST).date().isoformat() != day for r in rows):
                raise ValueError("Unexpected candle date or fields")
            return sorted(rows, key=lambda r: r[0]), None
        except HTTPError as exc:
            error = f"HTTP {exc.code}"
            if exc.code == 429:
                with LOCK:
                    COOLDOWN = max(COOLDOWN, time.monotonic() + max(65, int(exc.headers.get("Retry-After") or 65)))
            elif exc.code < 500 and exc.code not in (408, 429):
                break
        except (OSError, KeyError, ValueError) as exc:
            error = type(exc).__name__
        time.sleep(2 ** attempt)
    return [], error


def save(path, body):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as raw:
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        raw.write(gzip.compress(payload, compresslevel=5, mtime=0) if path.suffix == ".gz" else payload)
    os.replace(tmp, path)


def run(root, day):
    files = sorted(root.glob(f"{day}_snapshot_*_complete.json.gz"), reverse=True)
    if not files:
        raise ValueError("No source session snapshots")
    out = root / "historical_candles" / day
    out.mkdir(parents=True, exist_ok=True)
    instruments = discover(files[:1])
    # Scan remaining captures while the first batch is downloading.
    with concurrent.futures.ThreadPoolExecutor(1) as scanner, concurrent.futures.ThreadPoolExecutor(12) as workers:
        scan = scanner.submit(discover, files[1:])
        completed = {}
        for part in sorted(out.glob("candles_*.json.gz")):
            with gzip.open(part, "rt", encoding="utf-8") as inp:
                completed.update({row["key"]: len(row["candles"]) for row in json.load(inp)["instruments"]})
        part_number = len(list(out.glob("candles_*.json.gz")))
        errors = {}
        fields = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
        def process(keys):
            nonlocal part_number
            batch = []
            def flush():
                nonlocal part_number
                if not batch:
                    return
                part_number += 1
                save(out / f"candles_{part_number:04d}.json.gz", {"provider": "Upstox", "day": day, "interval": "1 minute", "fields": fields, "instruments": batch})
                completed.update({row["key"]: len(row["candles"]) for row in batch})
                print(f"{len(completed)} instruments archived, {sum(completed.values())} bars, {len(errors)} errors", flush=True)
                batch.clear()
            todo = {workers.submit(fetch, key, day): key for key in keys if key not in completed and key not in errors}
            for future in concurrent.futures.as_completed(todo):
                key = todo[future]
                bars, error = future.result()
                if error:
                    errors[key] = error
                else:
                    batch.append({"key": key, **instruments[key], "candles": bars})
                if len(batch) >= 80:
                    flush()
            flush()
        process(list(instruments))
        instruments.update(scan.result())
        process(list(instruments))
    save(out / "manifest.json", {"source": SOURCE, "day": day, "interval": "1 minute", "fields": fields,
         "snapshot_files_scanned": len(files), "instruments_requested": len(instruments), "instruments_returned": len(completed),
         "empty_instruments": sorted(k for k,n in completed.items() if n == 0), "errors": errors,
         "total_bars": sum(completed.values()), "parts": part_number})
    print("Manifest saved", len(instruments), len(completed), sum(completed.values()), errors and list(errors.items())[:3], flush=True)
    return bool(errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("day", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date().isoformat())
    args = parser.parse_args()
    raise SystemExit(run(args.archive, args.day))
