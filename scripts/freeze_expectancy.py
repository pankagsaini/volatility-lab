"""Audit archived S1–S6 endpoint prices and slippage, without rewriting a ledger.

Run python scripts/freeze_expectancy.py --archive PATH --output DIRECTORY
on the published snapshot cohort. Output contains aggregates and file hashes only.
"""
import argparse
import csv
from collections import defaultdict
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from volatility_lab.backtest.broker_charges import charge  # noqa: E402
from scripts.rebuild_backtest_archive import estimate_margin  # noqa: E402

TICKS = (0, 1, 2, 5)
SPLIT = "2026-11-06"  # final third of the 18 published days; no parameter fitting.
LEG = re.compile(r"([BS])(\d*) (\d+)(CE|PE) \(")
COHORT_SHA = "13ff5a86f84d8579b9a7283eff46e5b105e17184"


def timestamp(v):
    if v is None:
        return None
    if str(v).isdigit() and len(str(v)) in (10, 13):
        return float(v) / (1000 if len(str(v)) == 13 else 1)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return t.timestamp() if t.tzinfo else None
    except ValueError:
        return None


def sides(doc, row, legs, entry):
    record = next(r for r in doc["records"] if r["label"] == row["sym"]+"|"+row["exp"])
    chain = {n["strike_price"]: n for n in record["data"]}
    arrivals = [timestamp(doc["retrieved_at"]), timestamp(record["retrieved_at"])]
    # The decision can only be made after the selected response has arrived.
    decision = max(arrivals) if all(arrivals) else None
    ltps, books = [], []
    for side, qty, strike, kind in legs:
        md = chain[strike]["call_options" if kind == "CE" else "put_options"]["market_data"]
        ltps.append(md["ltp"])
        buy = (side == 1) == entry
        bid, ask = md.get("bid_price") or 0, md.get("ask_price") or 0
        size = md.get("ask_qty" if buy else "bid_qty") or 0
        q = timestamp(md.get("quote_timestamp"))
        ok = (q and decision and 0 <= decision-q <= 120 and size >= qty and
              (md.get("bid_qty") or 0)>0 and (md.get("ask_qty") or 0)>0 and
              0 < bid <= ask and md.get("quote_source") == "Upstox /v2/market-quote/quotes")
        books.append((ask if buy else bid) if ok else None)
    return ltps, books


def outcome(row, legs, opens, closes, ticks):
    gross = charges = 0.0
    for (side, qty, _, _), opening, closing in zip(legs, opens, closes):
        entry = max(0.05, opening + side*ticks*.05)
        exit_ = max(0.05, closing - side*ticks*.05)
        gross += side*qty*(exit_-entry)
        for price, action in [(entry, side), (exit_, -side)]:
            charges += sum(charge(row["date"], "options", price, qty, "buy" if action == 1 else "sell").values())
    return round(gross, 2), round(charges, 2)


def summarize(rows):
    daily, cap = defaultdict(float), defaultdict(float)
    nets = []
    for r in rows:
        net = r["gross"] - r["charges"]
        nets.append(net)
        daily[r["date"]] += net
        cap[r["date"]] += r["capital"]
    win, loss = [n for n in nets if n > 0], [n for n in nets if n < 0]
    peak = equity = drawdown = 0.0
    for day in sorted(daily):
        equity += daily[day]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    values = list(daily.values())
    sd = statistics.stdev(values) if len(values) > 1 else 0
    downside = statistics.mean(min(0, n)**2 for n in values)**.5 if values else 0
    def avg(data):
        return statistics.mean(data) if data else None
    measures = dict(n=len(rows), days=len(values), gross=sum(r["gross"] for r in rows) if rows else None,
                    charges=sum(r["charges"] for r in rows) if rows else None, net=sum(nets) if nets else None,
                    expectancy=avg(nets), win_pct=100*len(win)/len(nets) if nets else None,
                    avg_win=avg(win), avg_loss=avg(loss), profit_factor=sum(win)/-sum(loss) if loss else None,
                    drawdown=drawdown if rows else None, roc_pct=100*sum(nets)/max(cap.values()) if cap else None,
                    sharpe_day=avg(values)/sd if sd else None, sortino_day=avg(values)/downside if downside else None)
    return {k: round(v, 4) if isinstance(v,float) else v for k,v in measures.items()}


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restate-html", action="store_true",
                        help="Restate only legacy archive quantities after raw price reconciliation.")
    args = parser.parse_args()
    html = (ROOT/"docs/index.html").read_text(encoding="utf-8")
    archive = json.loads(re.search(r"var tradeArchive = (.*?);\n", html).group(1))
    complete = []
    inputs = {}
    for (symbol, day) in sorted({(r["sym"], r["date"]) for r in archive}):
        session = json.loads((ROOT/f"docs/replay/{symbol}-{day}.json").read_bytes())
        points = {p["at"][11:19]: p for p in session["points"]}
        raw = {}
        for r in (r for r in archive if r["sym"] == symbol and r["date"] == day):
            legs = [(1 if side == "B" else -1, int(ratio or 1)*session["lot"], int(k), kind)
                    for side, ratio, k, kind in LEG.findall(r["legs"])]
            assert legs
            endpoints = []
            for flag,key in [(True,"entryAt"), (False,"exitAt")]:
                point = points[r[key]]
                name = point["source"]
                if name not in raw:
                    content = (args.archive/name).read_bytes()
                    raw[name] = json.loads(gzip.decompress(content))
                    assert raw[name]["status"] == "complete" and raw[name]["provider"] == "Upstox"
                    inputs[name] = sha256(content).hexdigest()
                endpoints.append(sides(raw[name], r, legs, flag))
            for mode, index in (("LTP",0), ("qualified_L1",1)):
                opens, closes = [end[index] for end in endpoints]
                if any(x is None for x in opens+closes):
                    continue
                for ticks in TICKS:
                    gross, charges = outcome(r,legs,opens,closes,ticks)
                    if mode == "LTP" and ticks == 0:
                        ratio = r["unitLegs"]/sum(l[1] for l in legs)
                        assert abs(gross*ratio-r["gross"]) < .011, (r["date"],symbol,r["strat"],gross,r["gross"])
                        old_legs = [(side, int(qty*ratio), k, kind) for side, qty, k, kind in legs]
                        old_gross,old_charges = outcome(r,old_legs,opens,closes,0)
                        assert abs(old_gross-r["gross"]) < .011 and abs(old_charges-r["charges"]) < .011
                        sizes = [dict(side=side, quantity=qty, strike=strike, kind=kind, entry=px)
                                 for (side,qty,strike,kind),px in zip(legs,opens)]
                        capital = estimate_margin(sizes,r["entrySpot"],symbol)
                        if args.restate_html and ratio != 1:
                            r.update(unitLegs=sum(l[1] for l in legs),capital=capital,gross=gross,charges=charges)
                    complete.append(dict(date=day,symbol=symbol,code=r["strat"],rule=r["rulePass"],
                                         mode=mode,ticks=ticks,gross=gross,charges=charges,capital=capital))
    report = []
    for mode in ("LTP","qualified_L1"):
        for population in ("baseline","archived_filter"):
            for code in [f"S{i}" for i in range(1,7)]:
                for ticks in TICKS:
                    pool = [r for r in complete if r["code"] == code and r["mode"] == mode and r["ticks"] == ticks
                            and (population == "baseline" or r["rule"])]
                    for symbol in ("BOTH","NIFTY","BANKNIFTY"):
                        for period in ("full","early","later"):
                            rows = [r for r in pool if (symbol == "BOTH" or r["symbol"] == symbol)
                                    and (period == "full" or ((r["date"] >= SPLIT) == (period == "later")))]
                            report.append(dict(prices=mode,population=population,code=code,ticks=ticks,
                                               symbol=symbol,period=period,**summarize(rows)))
    args.output.mkdir(exist_ok=True,parents=True)
    write_csv(args.output/"freeze-stress.csv",report)
    write_csv(args.output/"freeze-source-hashes.csv",[dict(filename=k,sha256=v) for k,v in sorted(inputs.items())])
    if args.restate_html:
        for path in [ROOT/"index.html", ROOT/"docs/index.html"]:
            data = path.read_bytes().decode()
            meta_pattern = r"var backtestMeta = (.*?);\r?\n"
            meta=json.loads(re.search(meta_pattern,data).group(1))
            meta.update(schema=2,lotRestatement={"sourceCommit":COHORT_SHA,"units":{"NIFTY":65,"BANKNIFTY":30},
                        "source":"NSE FO contract masters 2026-10-21 and 2026-11-18; raw Upstox endpoints"})
            for name,obj in [("tradeArchive",archive),("backtestMeta",meta)]:
                data,count = re.subn("var "+name+r" = [^\n]*?;(?=\r?\n)",
                    lambda _: "var "+name+" = "+json.dumps(obj,ensure_ascii=False,separators=(",",":"))+";",data)
                assert count == 1
            path.write_bytes(data.encode())
    print(f"Reconciled {len(archive)} round trips to {len(inputs)} compressed source captures.")


if __name__ == "__main__":
    main()
