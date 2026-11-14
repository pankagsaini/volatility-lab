"""Build the dated options desk fallback from original, complete Upstox chain captures."""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
import re
import sys

IST = timezone(timedelta(hours=5, minutes=30))
SOURCE = "Upstox option_greeks.iv"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from volatility_lab.core.market_calendar import expiry_close
from volatility_lab.ingestion.google_archive_sync import validate_report


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def snapshot(path):
    with gzip.open(path, "rt", encoding="utf-8") as reader:
        payload = json.load(reader)
    if validate_report(payload, historical=True)["mode"] != "snapshot":
        raise ValueError(f"Invalid archive {path.name}")
    return payload


def pair(row):
    return [((row.get(side) or {}).get("option_greeks") or {}).get("iv")
            for side in ("call_options", "put_options")]


def average(values):
    return (sum(values) / len(values) if values and
            all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in values) else None)


def anchor_point(row, rec, raw, path):
    books = [(row.get(side) or {}).get("market_data") or {} for side in ("call_options", "put_options")]
    mark_at = timestamp(raw["retrieved_at"])
    def valid(book):
        bid, ask = book.get("bid_price"), book.get("ask_price")
        return (book.get("quote_timestamp") and bid and ask and ask >= bid > 0
                and (book.get("bid_qty") or 0) > 0 and (book.get("ask_qty") or 0) > 0
                and -30 <= (mark_at-timestamp(book["quote_timestamp"])).total_seconds() <= 120)
    valid_pair = all(valid(b) for b in books) and abs((timestamp(books[0]["quote_timestamp"])
                    - timestamp(books[1]["quote_timestamp"])).total_seconds()) <= 30
    return {"t": mark_at.astimezone(IST).strftime("%H:%M"), "atm": row["strike_price"], "expiry": row["expiry"],
            "snapshotId": rec.get("chain_retrieved_at", rec["retrieved_at"]), "markAt": raw["retrieved_at"],
            "iv": average(pair(row)), "ivSource": SOURCE, "archiveFile": path.name,
            "markSource": "paired_book_mid" if valid_pair else None, "cQuoteAt": books[0].get("quote_timestamp"),
            "pQuoteAt": books[1].get("quote_timestamp"),
            "strad": sum(b["bid_price"] + b["ask_price"] for b in books)/2 if valid_pair else None}


def build(archive, day):
    paths = sorted(archive.glob(f"{day}_snapshot_*_complete.json.gz"))
    html = (ROOT / "index.html").read_text("utf-8")
    history = json.loads(re.search(r"var REAL_HISTORICAL_1Y = (.*);", html)[1])
    tracks = {}
    catalog = {"NIFTY": [], "BANKNIFTY": []}
    for path in paths:
        raw = snapshot(path)
        if raw.get("nfo_session_validated") is not True or raw.get("day") != day:
            continue
        for rec in raw["records"]:
            if not re.fullmatch(r"(NIFTY|BANKNIFTY)\|\d{4}-\d{2}-\d{2}", rec["label"]):
                continue
            symbol, expiry = rec["label"].split("|")
            rows = rec["data"]
            spot = rows[0]["underlying_spot_price"]
            atm = min(rows, key=lambda r: (abs(r["strike_price"]-spot), r["strike_price"]))
            strike, points = tracks.setdefault(rec["label"], (atm["strike_price"], []))
            anchor = next((r for r in rows if r["strike_price"] == strike), None)
            if anchor:
                points.append(anchor_point(anchor, rec, raw, path))
            if path != paths[-1]:
                continue
            risk = (raw.get("risk_metrics") or {}).get(symbol, {})
            quotes = next(r["data"] for r in raw["records"] if r["label"] == "quotes")
            by_key = {q["instrument_token"]: q for q in quotes.values()}
            future = next((f for f in raw["catalogue"]["indices"][symbol]["futures"] if f["expiry"] == expiry), {})
            chain = []
            for row in rows:
                node = {"k": row["strike_price"]}
                for side, prefix, name in (("call_options","c","Call"), ("put_options","p","Put")):
                    leg = row.get(side) or {}
                    greeks = leg.get("option_greeks") or {}
                    market = leg.get("market_data") or {}
                    node["u"+name+"Iv"] = node[prefix+"Iv"] = greeks.get("iv")
                    node["u"+name+"Delta"] = node[prefix+"Delta"] = greeks.get("delta")
                    node[prefix+"Ltp"] = market.get("ltp")
                    oi = market.get("oi")
                    node[prefix+"Oi"] = oi / 1000 if oi is not None else None
                node["mktIv"] = node["pIv"] if node["k"] < spot else node["cIv"]
                chain.append(node)
            calls = sum(n["cOi"] or 0 for n in chain)
            prices = [((atm.get(s) or {}).get("market_data") or {}).get("ltp") for s in ("call_options","put_options")]
            end = expiry_close(expiry)
            opt = {"expiry": expiry, "kind": "Monthly" if future else "Weekly", "sessionDate": day,
                   "dte": (end-timestamp(raw["retrieved_at"])).total_seconds()/86400, "spot": spot, "atm": atm["strike_price"],
                   "straddleStrike": strike, "intraday": points, "chain": chain, "brokerAtmIv": average(pair(atm)),
                   "iv": average(pair(atm)), "ivSource": SOURCE, "pricingModel": "Captured Upstox IV",
                   "cIv": pair(atm)[0], "pIv": pair(atm)[1], "upstoxCallIv": pair(atm)[0], "upstoxPutIv": pair(atm)[1],
                   "strad": sum(prices) if average(prices) is not None else None,
                   "fut": by_key.get(future.get("instrument_key"), {}).get("last_price"), "syn": None,
                   "vix": by_key.get("NSE_INDEX|India VIX", {}).get("last_price"), "ivPct": None,
                   "pcr": sum(n["pOi"] or 0 for n in chain)/calls if calls else None,
                   "riskAsOf": risk.get("as_of"), **{k:risk.get(k) for k in ("hv10","hv20","hv30","ewma")},
                   "ivCapturedAt": rec.get("chain_retrieved_at", rec["retrieved_at"]), "archiveFile": path.name}
            catalog[symbol].append(opt)
    if not all(catalog.values()):
        raise ValueError("Latest archive lacks validated broker chains")
    for sym, items in catalog.items():
        items.sort(key=lambda x: x["expiry"])
        front = next((i for i in items if i["expiry"] > day), None)
        # The day may have an underlying close recorded by a separate historical source.
        if front:
            old = next((r for r in history[sym] if r["d"] == day), {})
            point = {"d":day, "l":timestamp(day).strftime("%d %b"), "c":old.get("c"),
                     "iv":front["iv"], "ivSource":SOURCE, "atm":front["atm"], "expiry":front["expiry"],
                     "snapshotId":front["ivCapturedAt"], "archiveFile":front["archiveFile"],
                     **{k:front[v] for k,v in (("h10","hv10"),("h20","hv20"),("h30","hv30"),("ew","ewma"))}}
            if old.get("hvCloseDate") == day:
                point.update({key: old[key] for key in ("c", "h10", "h20", "h30", "hvCloseDate")})
            if "closingEstimate" in old:
                point["closingEstimate"] = old["closingEstimate"]
            history[sym] = sorted([r for r in history[sym] if r["d"] != day]+[point], key=lambda r:r["d"])
        # On expiry day use the nearest later listed expiry in the very same original capture.
        for point in history[sym]:
            if point.get("expiry") != point["d"] or not point.get("archiveFile"):
                continue
            source = snapshot(archive / point["archiveFile"])
            eligible = sorted((r for r in source["records"] if r["label"].startswith(sym+"|")
                               and r["label"].split("|")[1] > point["d"]), key=lambda r:r["label"])
            if not eligible:
                point["iv"] = None
                continue
            rec = eligible[0]
            atm = min(rec["data"], key=lambda n: (abs(n["strike_price"]-n["underlying_spot_price"]), n["strike_price"]))
            point.update(iv=average(pair(atm)), atm=atm["strike_price"], expiry=atm["expiry"],
                         snapshotId=rec.get("chain_retrieved_at", rec["retrieved_at"]))
    for var, value in (("REAL_HISTORICAL_1Y",history),("marketCatalog",catalog)):
        line = "var "+var+" = "+json.dumps(value, separators=(",",":"), allow_nan=False)+";"
        html = re.sub(r"var "+var+r" = .*;", lambda _: line, html, count=1)
    for var, value in (("lastSyncedRetrievedAt",raw["retrieved_at"]),("latestSnapshotDay",day),
                       ("latestAsOfLabel",timestamp(raw["retrieved_at"]).astimezone(IST).strftime("%d %b %H:%M IST"))):
        html = re.sub("var "+var+r" = '[^']*';", "var "+var+" = '"+value+"';", html, count=1)
    for path in (ROOT/"index.html",ROOT/"docs"/"index.html"):
        path.write_text(html, encoding="utf-8", newline="\r\n")
    print(day, {s:len(v) for s,v in catalog.items()}, len(paths), "captures")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("day")
    args = parser.parse_args()
    build(args.archive, args.day)
