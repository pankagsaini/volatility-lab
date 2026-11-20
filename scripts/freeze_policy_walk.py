"""Walk a pinned, previously seen capture cohort through the production Python state machine, in memory."""
import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"volatility_lab")]
from volatility_lab.backtest import forward_test as engine  # noqa: E402
from scripts.freeze_expectancy import summarize  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    replay = ROOT/"docs/replay"
    manifest = json.loads((replay/"manifest.json").read_bytes())
    states = {}
    for session in manifest["sessions"]:
        if not engine.MIN_FORWARD_DATE <= session["date"] <= "2026-11-19":
            continue
        for item in json.loads((replay/session["file"]).read_bytes())["points"]:
            states[item["source"]] = item["at"]
    ledgers = {i:engine.sanitize_forward_ledger({}) for i in (0,1,2,5)}
    original_quote = engine._executable_quote
    counters = Counter()
    entries = set()
    hashes = {}
    decisions = []
    for source,at in sorted(states.items(), key=lambda kv: (kv[1],kv[0])):
        counters["available captures"] += 1
        clock = at[11:16]
        if clock not in {"09:20","09:25"} and not any(l["open_positions"] for l in ledgers.values()):
            continue
        if clock in {"09:20","09:25"}:
            counters["decision captures"] += 1
        content = (args.archive/source).read_bytes()
        hashes[source] = hashlib.sha256(content).hexdigest()
        doc = json.loads(gzip.decompress(content))
        prior_ids = entries.copy()
        for ticks,ledger in ledgers.items():
            if not ledger["open_positions"] and clock not in {"09:20","09:25"}:
                continue
            def adverse_quote(node,kind,side,quantity,is_exit,**kwargs):
                price = original_quote(node,kind,side,quantity,is_exit,**kwargs)
                if not price:
                    return price
                direction = -side if is_exit else side
                return max(.05,round(price+ticks*.05*direction,2))
            with patch.object(engine,"_executable_quote",adverse_quote):
                ledgers[ticks] = engine.process_live_upstox_snapshot(doc,ledger,sl_rule="SL_50_MAXLOSS",tp_rule="TP_50_30")
        ledger = ledgers[0]
        counters["processed captures"] += 1
        if ledger.get("gate_blocked"):
            counters["session gate blocked"] += 1
        entries.update(r["id"] for r in ledger["open_positions"])
        if clock in {"09:20","09:25"}:
            decisions.append([source,at,hashes[source],bool(ledger.get("gate_blocked")),ledger.get("gate_message"),
                              ",".join(sorted(entries-prior_ids))])
    fields = ["date","strategy","symbol","entry_time","exit_at","exit_reason","capital","gross","charges","net"]
    with (args.output/"freeze-policy-closed.csv").open("w",encoding="utf8",newline="") as f:
        writer = csv.writer(f,lineterminator="\n")
        writer.writerow(["ticks",*fields])
        for ticks,ledger in ledgers.items():
            for trade in ledger["closed_trades"]:
                writer.writerow([ticks]+[trade.get(k) for k in ("date","strat","sym","triggerTime","exit_at","exitReason","capital","gross","charges","net")])
    with (args.output/"freeze-policy-stress.csv").open("w",encoding="utf8",newline="") as f:
        writer=None
        for ticks,ledger in ledgers.items():
            for code in ["S1","S2","S3","S4","S5","S6"]:
                closed=[r for r in ledger["closed_trades"] if r["strat"]==code]
                row=dict(ticks=ticks,strategy=code,open_count=sum(r["strat"]==code for r in ledger["open_positions"]),
                         **summarize(closed))
                if writer is None:
                    writer=csv.DictWriter(f,fieldnames=row.keys(),lineterminator="\n")
                    writer.writeheader()
                writer.writerow(row)
    ledger=ledgers[0]
    with (args.output/"freeze-policy-decisions.csv").open("w",encoding="utf8",newline="") as f:
        writer=csv.writer(f,lineterminator="\n")
        writer.writerow(["source","available_at","sha256","gate_blocked","gate_status","new_open_ids"])
        writer.writerows(decisions)
    for day in sorted({k[:10] for k in hashes}):
        sequence="".join(f"{k} {v}\n" for k,v in sorted(hashes.items()) if k[:10]==day)
        print(day,len(sequence.splitlines()),hashlib.sha256(sequence.encode()).hexdigest())
    print(dict(counters),len(entries),"opened",len(ledger["closed_trades"]),"closed",len(ledger["open_positions"]),"still open")
    for pos in ledger["open_positions"]:
        print(pos["date"],pos["sym"],pos["strat"],pos.get("freshness"),pos.get("exit_pending"))


if __name__ == "__main__":
    main()
