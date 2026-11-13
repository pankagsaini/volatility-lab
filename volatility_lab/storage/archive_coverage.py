"""Read-only request inventory and evidence-based backtest session coverage.

Queue row totals are not unique candles; unobserved dates are not inferred holidays.
"""
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
from contextlib import closing
from datetime import date,datetime,timedelta,timezone
import json,sqlite3
from collections import defaultdict,Counter

ROOT=PACKAGE_ROOT/'data'/'backtest_1minute'

def readonly(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)

def inventory(root=ROOT):
    root=Path(root)
    with closing(readonly(root/'queue.sqlite')) as c:
        jobs=c.execute("SELECT kind,meta,state,rows,file FROM jobs WHERE state!='superseded'").fetchall()
    counts=Counter();contracts={};rows_saved=Counter();empty=Counter();missing=Counter()
    files={p.name for p in root.glob('*.json.gz')}
    for kind,text,state,rows,file in jobs:
        m=json.loads(text);symbol=m.get('symbol','Unknown');counts[symbol,kind,state]+=1
        if state=='done' and (not file or file not in files):missing[symbol]+=1
        if state=='done' and rows==0:empty[symbol,kind]+=1
        if kind=='minute' and state=='done':rows_saved[symbol]+=rows or 0
        if kind not in ('minute','discovery'):continue
        key=m.get('key');contract=m.get('contract',{});ident=(symbol,key)
        r=contracts.setdefault(ident,{'Index':symbol,'Contract':contract.get('trading_symbol',key),'Instrument key':key,'Expiry':contract.get('expiry'),'Type':contract.get('instrument_type','Underlying'),'Discovery pending':0,'Discovery done':0,'Minute pending':0,'Minute done':0,'Failed requests':0,'Empty minute responses':0,'Stored minute rows':0,'Missing raw files':0,'Requested from':m.get('start'),'Requested to':m.get('end')})
        r['Requested from']=min(r['Requested from'] or '9999',m.get('start','9999'))
        r['Requested to']=max(r['Requested to'] or '',m.get('end',''))
        if state=='failed':r['Failed requests']+=1
        if state in ('pending','done'):r[('Minute' if kind=='minute' else 'Discovery')+' '+state]+=1
        if state=='done' and (not file or file not in files):r['Missing raw files']+=1
        if kind=='minute' and state=='done':
            r['Stored minute rows']+=rows or 0
            if rows==0:r['Empty minute responses']+=1
    summary=[]
    for symbol in sorted({k[0] for k in counts}):
        availability=root/(symbol+'_availability.json')
        a=json.loads(availability.read_text()) if availability.exists() else {}
        expiries=sorted(a.get('returned_expiries',[]))
        summary.append({'Index':symbol,'First returned expiry':expiries[0] if expiries else None,'Last returned expiry':expiries[-1] if expiries else None,'Returned expiries':len(expiries),'Discovery pending':counts[symbol,'discovery','pending'],'Discovery done':counts[symbol,'discovery','done'],'Minute pending':counts[symbol,'minute','pending'],'Minute done':counts[symbol,'minute','done'],'Failed requests':sum(n for (s,k,state),n in counts.items() if s==symbol and state=='failed'),'Empty discovery responses':empty[symbol,'discovery'],'Empty minute responses':empty[symbol,'minute'],'Stored minute rows':rows_saved[symbol],'Missing raw files':missing[symbol]})
    scope=json.loads((root/'scope.json').read_text()) if (root/'scope.json').exists() else {}
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'scope':scope,'summary':summary,'contracts':list(contracts.values()),'limitations':['Request inventory, not a candle-level completeness certificate.','Stored row counts may include overlaps. Empty response does not prove a non-trading day.','First returned expiry is not the first available candle date.','Listing-date/session calendar and tick-level execution coverage are not verified.']}

def daily_sessions(key,start,end,path=None):
    path=Path(path) if path else ROOT.parent/'upstox'/'research.sqlite'
    if not path.exists():return []
    with closing(readonly(path)) as c:
        rows=c.execute("SELECT DISTINCT substr(stamp,1,10) FROM candles WHERE instrument=? AND interval='day' AND substr(stamp,1,10) BETWEEN ? AND ? AND open>0 AND close>0 AND high>=open AND high>=close AND low<=open AND low<=close",(key,str(start),str(end))).fetchall()
    return sorted(r[0] for r in rows)

def session_report(spot,start,end,confirmed=()):
    observed=defaultdict(set)
    for stamp in spot:
        if str(start)<=stamp[:10]<=str(end):observed[stamp[:10]].add(stamp[11:16])
    confirmed=set(confirmed);details=[];day=date.fromisoformat(str(start));last=date.fromisoformat(str(end))
    while day<=last:
        d=str(day);minutes=observed.get(d,set())
        status=('Minute observations present' if minutes else
                'Missing minutes; daily candle confirms session' if d in confirmed else
                'Weekend; special-session status unverified' if day.weekday()>=5 else
                'Unverified weekday; no daily or minute evidence')
        details.append({'Date':d,'Status':status,'Observed spot minutes':len(minutes),'Daily reference':d in confirmed})
        day+=timedelta(days=1)
    return {'daily_reference_sessions':len(confirmed),'observed_minute_sessions':len(observed),'confirmed_sessions_missing_minutes':len(confirmed-set(observed)),'unverified_dates':sum(r['Status'].startswith('Unverified weekday') for r in details),'sessions':details,'coverage_basis':'Saved daily index candles and loaded minute observations; not a complete exchange calendar. Weekend special sessions are not inferred. Presence does not prove all session minutes are present.'}

if __name__=='__main__':
    from collector_support import atomic_json
    import csv
    report=inventory();out=ROOT.parent/'audit';out.mkdir(exist_ok=True)
    atomic_json(out/'historical_coverage.json',report)
    with (out/'historical_contract_coverage.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(report['contracts'][0]) if report['contracts'] else ['Index']);writer.writeheader();writer.writerows(report['contracts'])
    print(json.dumps(report['summary'],indent=2))
