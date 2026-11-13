"""Reproduce the November closing-HV update and the two explicitly approved IV estimates."""
import argparse
import gzip
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.upstox.com/v3/historical-candle"
SESSIONS = {
    "2026-11-08": ("2026-11-09_snapshot_152742_complete.json.gz", 5.8970, 63917),
    "2026-11-13": ("2026-11-13_snapshot_131442_complete.json.gz", 5.8681, 63956),
}
INDICES = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}


def read_archive(path):
    with gzip.open(path, "rt", encoding="utf-8") as data:
        return json.load(data)


def broker_candles(key, unit, start, end):
    encoded = urllib.parse.quote(key, safe="")
    url = f"{BASE}/{encoded}/{unit}/{end}/{start}"
    req = urllib.request.Request(url, headers={"Accept":"application/json", "User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as data:
        body = json.load(data)
    if body["status"] != "success":
        raise ValueError("Broker candle request failed")
    return body["data"]["candles"]


def invert(price, spot, strike, years, rate, dividend, forward, call):
    # Use the shipped website's model and bounds, without loading dashboard dependencies.
    js = '''
const fs=require('node:fs'), vm=require('node:vm');
const html=fs.readFileSync('index.html','utf8');
const c=vm.createContext({B76_INV_SQRT_2PI:1/Math.sqrt(2*Math.PI)});
for (const name of ['normPdf','normCdf','black76Greeks','black76Price','black76IvDec','bsmGreeks','bsmIvDec']) {
  const start=html.indexOf('      function '+name+'(');
  vm.runInContext(html.slice(start,html.indexOf('\\n      }',start)+8),c);
}
const [price,spot,k,t,r,q,f,call]=JSON.parse(process.argv[1]);
console.log(f===null ? c.bsmIvDec(price,spot,k,t,r,q,call) : c.black76IvDec(price,f,k,t,r,call));
'''
    args = json.dumps([price,spot,strike,years,rate,dividend,forward,call])
    result = float(subprocess.check_output(['node','-e',js,args],cwd=ROOT))
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Candle price is outside valid IV inversion bounds")
    return result


def estimate(archive, symbol, day, spot, minutes, pricing):
    snapshot, bill, release = SESSIONS[day]
    report = read_archive(archive / snapshot)
    catalogue = report["catalogue"]["indices"][symbol]
    cutoff = f"{day}T15:29:00+05:30"
    end = datetime.fromisoformat(f"{day}T15:30:00+05:30")
    def bar(key):
        rows = minutes.get(key, []) if day == "2026-11-13" else broker_candles(key,"minutes/1",day,day)
        result = next((c for c in rows if c[0] == cutoff and c[4] > 0 and c[5] > 0),None)
        if result is None:
            raise ValueError(f"{symbol} {day} {key} has no traded 15:29 candle")
        return result
    expiries = [x for x in catalogue["option_expiries"] if x > day]
    # Upstox's standard candle API returned 400 for the expired 9 Nov NIFTY pair.
    if symbol == "NIFTY" and day == "2026-11-08":
        expiries.remove("2026-11-09")
    expiry = expiries[0]
    rows = next(r["data"] for r in report["records"] if r["label"] == f"{symbol}|{expiry}")
    atm = min(rows, key=lambda r: (abs(r["strike_price"]-spot),r["strike_price"]))
    strike = atm["strike_price"]
    call_key, put_key = [atm[side]["instrument_key"] for side in ("call_options","put_options")]
    future_key = next((f["instrument_key"] for f in catalogue["futures"] if f["expiry"] == expiry),None)
    call, put = bar(call_key), bar(put_key)
    future = bar(future_key) if future_key else None
    fwd = future[4] if future else None
    rate = math.log1p(bill/100*(91/365))/(91/365)
    dividend = pricing["dividends"][symbol]
    if dividend["as_of"] > day:
        raise ValueError("Dividend factsheet is newer than the estimate")
    years = (datetime.fromisoformat(expiry+"T15:40:00+05:30")-end).total_seconds()/(365*86400)
    ivs = [100 * invert(c[4],spot,strike,years,rate,dividend["model_yield"],fwd,is_call)
           for c,is_call in ((call,True),(put,False))]
    return {"iv": statistics.mean(ivs), "legIv": ivs, "expiry": expiry, "atm": strike, "asOf": end.isoformat(),
            "model": "Black-76" if future else "BSM", "source": "Upstox v3 traded one-minute candle closes",
            "spot": spot, "call": {"key":call_key,"bar":call}, "put": {"key":put_key,"bar":put},
            "future": {"key":future_key,"bar":future} if future else None, "rbiYieldPct":bill,
            "rbiSource":f"https://www.rbi.org.in/SCRIPTS/BS_PressReleaseDisplay.aspx?prid={release}",
            "dividendYield": dividend["model_yield"] if not future else None, "dividendDate":dividend["as_of"] if not future else None,
            "dividendSource":dividend["source"] if not future else None}


def build(archive):
    path = ROOT/"index.html"
    html = path.read_text("utf-8")
    pattern = r"var REAL_HISTORICAL_1Y = (.*);"
    history = json.loads(re.search(pattern,html)[1])
    pricing = json.loads((ROOT/"pricing-inputs.json").read_text("utf-8"))
    minutes = {}
    for file in (archive/"historical_candles"/"2026-11-13").glob("candles_*.json.gz"):
        minutes.update((r["key"],r["candles"]) for r in read_archive(file)["instruments"])
    for symbol,key in INDICES.items():
        days = sorted((r[0][:10], r[4]) for r in broker_candles(key,"days/1","2026-08-10","2026-11-13"))
        if not days or days[-1][0] != "2026-11-13":
            raise ValueError(f"Missing verified broker close for {symbol}")
        observed = dict(days)
        returns = [math.log(b[1]/a[1]) for a,b in zip(days,days[1:])]
        date_indices = {d:i for i,(d,_) in enumerate(days)}
        for row in history[symbol]:
            day = row["d"]
            idx = date_indices.get(day,-1)
            if idx >= 30:
                row["c"] = observed[day]
                for n in (10,20,30):
                    row[f"h{n}"] = statistics.stdev(returns[idx-n:idx])*math.sqrt(245)*100
                row["hvCloseDate"] = day
            if day in SESSIONS:
                row["closingEstimate"] = estimate(archive,symbol,day,observed[day],minutes,pricing)
                print(symbol,day,observed[day],row.get("h10"),row.get("h30"),row["closingEstimate"]["iv"],row["closingEstimate"]["expiry"])
    html = re.sub(pattern,lambda _:"var REAL_HISTORICAL_1Y = "+json.dumps(history,separators=(",",":"),allow_nan=False)+";",html,count=1)
    for file in (path, ROOT/"docs"/"index.html"):
        file.write_text(html,encoding="utf-8",newline="\r\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path)
    build(parser.parse_args().archive)

