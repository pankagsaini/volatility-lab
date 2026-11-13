"""Verify embedded IV against private Upstox archives without modifying either.

Usage: python scripts/verify_dashboard_iv.py PATH_TO_ARCHIVE_DIRECTORY
Only public market observations and archive basenames are embedded in the site.
"""
import argparse
import gzip
import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import re


def verify(archive_dir):
    html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
    history = json.loads(re.search(r"var REAL_HISTORICAL_1Y = (.*);", html)[1])
    catalog = json.loads(re.search(r"var marketCatalog = (.*);", html)[1])
    paths = {p.name: p for p in archive_dir.rglob("*_complete.json.gz")}

    @lru_cache(maxsize=2)
    def records(filename):
        raw = json.loads(gzip.decompress(paths[filename].read_bytes()))
        assert raw["provider"] == "Upstox" and raw["status"] == "complete"
        assert not raw.get("errors") and not raw.get("pending")
        return {r["label"]: r for r in raw["records"]}, raw["retrieved_at"]

    count = 0
    def check(symbol, point, timestamp_key="snapshotId"):
        nonlocal count
        assert point["ivSource"] == "Upstox option_greeks.iv"
        recs, retrieved = records(point["archiveFile"])
        rec = recs[symbol + "|" + point["expiry"]]
        assert point[timestamp_key] == rec.get("chain_retrieved_at", rec["retrieved_at"])
        atm = next(n for n in rec["data"] if n["strike_price"] == point["atm"])
        call = atm["call_options"]["option_greeks"]["iv"]
        put = atm["put_options"]["option_greeks"]["iv"]
        if call > 0 and put > 0:
            assert math.isclose(point["iv"], (call + put) / 2, abs_tol=1e-10)
        else:
            assert point["iv"] is None
        if "markAt" in point:
            assert point["markAt"] == retrieved
            books = [atm[s]["market_data"] for s in ("call_options","put_options")]
            times = [b.get("quote_timestamp") for b in books]
            assert times == [point["cQuoteAt"],point["pQuoteAt"]]
            valid = all(t and -30 <= (datetime.fromisoformat(retrieved) - datetime.fromisoformat(t)).total_seconds() <= 120
                        and b["ask_price"] >= b["bid_price"] > 0 and min(b["bid_qty"], b["ask_qty"]) > 0
                        for t,b in zip(times,books))
            if valid and abs((datetime.fromisoformat(times[0])-datetime.fromisoformat(times[1])).total_seconds()) <= 30:
                assert point["markSource"] == "paired_book_mid"
                assert math.isclose(point["strad"], sum(b["bid_price"]+b["ask_price"] for b in books)/2)
            else:
                assert point["markSource"] is None and point["strad"] is None
        elif "strad" in point:
            prices = [atm[side]["market_data"]["ltp"] for side in ("call_options", "put_options")]
            if all(p > 0 for p in prices):
                assert math.isclose(point["strad"], sum(prices), abs_tol=.011)
            else:
                assert point["strad"] is None
        count += 1
        return rec

    for symbol, points in history.items():
        assert [p["d"] for p in points] == sorted(set(p["d"] for p in points))
        for point in points:
            if point["iv"] is not None:
                check(symbol, point)
                assert point["d"] < point["expiry"], "Daily IV must roll on expiry day"
    intraday = []
    for symbol, items in catalog.items():
        for item in items:
            rec = check(symbol, item, "ivCapturedAt")
            raw_chain = {n["strike_price"]: n for n in rec["data"]}
            for node in item["chain"]:
                raw = raw_chain[node["k"]]
                for field, side in (("uCallIv", "call_options"), ("uPutIv", "put_options")):
                    assert node[field] == raw[side]["option_greeks"]["iv"]
            for point in item["intraday"]:
                assert point["atm"] == item.get("straddleStrike", item["atm"]) and point["expiry"] == item["expiry"]
                assert "vwap" not in point
                intraday.append((symbol,point))
    for symbol, point in sorted(intraday,key=lambda entry:entry[1]["archiveFile"]):
        check(symbol,point)
    print(f"Verified {count} IV observations and every embedded option-chain IV against Upstox archives.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_dir", type=Path)
    verify(parser.parse_args().archive_dir)
