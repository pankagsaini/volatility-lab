"""Resumable read-only audit of completed minute archives; never edits broker data."""
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone,timedelta
from collections import defaultdict,Counter
import argparse,csv,gzip,hashlib,json,math,sqlite3,time
from concurrent.futures import ProcessPoolExecutor
from archive_coverage import ROOT,readonly
from collector_support import atomic_json

OUT=ROOT.parent/'audit'/'candles'
IST=timezone(timedelta(hours=5,minutes=30))
VERSION=2

def validate_contract(root,jobs):
    """Compare all returned timestamps for one actual instrument, across files."""
    counts=Counter();seen={};regular=defaultdict(set);examples=[];first=None;last=None
    symbol=jobs[0]['meta']['symbol'];key=jobs[0]['meta']['key']
    def issue(reason,file,stamp=None):
        counts[reason]+=1
        if len(examples)<8:examples.append({'issue':reason,'file':file,'timestamp':str(stamp)[:40] if stamp is not None else None})
    for job in jobs:
        file=job['file'];m=job['meta'];counts['files']+=1
        try:
            with gzip.open(root/file,'rt',encoding='utf-8') as f:obj=json.load(f)
            rows=obj['data']['candles']
            if not isinstance(rows,list):raise ValueError()
            if obj.get('meta',{}).get('key')!=key:issue('metadata_mismatch',file);continue
        except (OSError,ValueError,KeyError,TypeError,EOFError):issue('unreadable_files',file);continue
        if len(rows)!=job['rows']:issue('row_count_mismatch',file)
        if not rows:counts['empty_files']+=1
        counts['raw_rows']+=len(rows)
        if not rows:continue
        import numpy as np
        import pandas as pd
        shaped=[r for r in rows if isinstance(r,list) and len(r)>=6 and isinstance(r[0],str)]
        counts['invalid_timestamp']+=len(rows)-len(shaped)
        if not shaped:continue
        texts=[r[0] for r in shaped]
        dates=pd.to_datetime(texts,format='ISO8601',utc=True,errors='coerce').as_unit('ns')
        ns=dates.asi8
        aware=np.array([t.endswith('Z') or '+' in t[10:] or '-' in t[10:] for t in texts])
        time_ok=aware & ~dates.isna() & (ns % 60_000_000_000 == 0)
        counts['invalid_timestamp']+=int((~time_ok).sum())
        local_minutes=ns//60_000_000_000+330
        day_numbers=local_minutes//1440
        # Convert each unique date once; daily grouping avoids per-candle datetime formatting.
        day_names={int(d):str(np.datetime64(int(d),'D')) for d in np.unique(day_numbers[time_ok])}
        for d,name in day_names.items():
            n=int((time_ok & (day_numbers==d)).sum())
            if not m['start']<=name<=m['end']:counts['outside_request']+=n
            if m.get('contract',{}).get('expiry','9999')<name:counts['after_expiry']+=n
        if time_ok.any():
            lo=int(local_minutes[time_ok].min());hi=int(local_minutes[time_ok].max())
            def label(v):return str(np.datetime64(v,'m')).replace('T',' ')
            lower,upper=label(lo),label(hi)
            first=lower if first is None else min(first,lower);last=upper if last is None else max(last,upper)
        try:
            vals=np.asarray([r[1:6]+[r[6] if len(r)>6 and r[6] is not None else np.nan] for r in shaped],dtype=float)
        except (ValueError,TypeError,OverflowError):
            vals=np.full((len(shaped),6),np.nan)
            for i,r in enumerate(shaped):
                try:vals[i]=[float(x) for x in r[1:6]]+[float(r[6]) if len(r)>6 and r[6] is not None else np.nan]
                except (ValueError,TypeError,OverflowError):pass
        o,h,l,c,v,oi=vals.T
        optional_oi=np.array([len(r)<=6 or r[6] is None for r in shaped])
        valid=np.isfinite(vals[:,:5]).all(axis=1) & (vals[:,:4]>0).all(axis=1) & (h>=np.maximum.reduce([o,l,c])) & (l<=np.minimum.reduce([o,h,c])) & (v>=0) & (optional_oi | (np.isfinite(oi)&(oi>=0)))
        counts['invalid_values']+=int((time_ok & ~valid).sum())
        good=time_ok & valid
        counts['zero_volume_rows']+=int((good & (v==0)).sum())
        ids=local_minutes[good].tolist()
        value_rows=vals[good].tolist()
        for value in value_rows:
            if math.isnan(value[5]):value[5]=None
        # Most files have non-overlapping timestamps. Dictionary construction is linear in C;
        # only repeated timestamps need individual comparison.
        incoming=dict(zip(ids,value_rows))
        if len(incoming)!=len(ids):
            incoming={}
            for stamp,value in zip(ids,value_rows):
                if stamp in incoming:
                    if incoming[stamp]==value:counts['identical_duplicate_rows']+=1
                    else:issue('conflicting_rows',file,str(np.datetime64(stamp,'m')))
                else:incoming[stamp]=value
        for stamp in seen.keys() & incoming.keys():
            if seen[stamp]==incoming[stamp]:counts['identical_duplicate_rows']+=1
            else:issue('conflicting_rows',file,str(np.datetime64(stamp,'m')))
        seen.update({k:v for k,v in incoming.items() if k not in seen})
        minute=local_minutes%1440
        regular_mask=good & (minute>=555) & (minute<930)
        counts['outside_regular_session_rows']+=int((good & ~regular_mask).sum())
        for d in np.unique(day_numbers[regular_mask]):
            regular[day_names[int(d)]].update(minute[regular_mask & (day_numbers==d)].tolist())
    # Potential gaps only: listing times, special sessions and no-trade bars are unverified.
    short=sum(len(minutes)<375 for minutes in regular.values())
    absent=sum(375-len(minutes) for minutes in regular.values())
    return {'symbol':symbol,'instrument_key':key,'contract':jobs[0]['meta'].get('contract',{}).get('trading_symbol',key),
            'first_observed':first,'last_observed':last,**dict(counts),'unique_valid_timestamps':len(seen),
            'observed_regular_days':len(regular),'days_below_375_observations':short,'unobserved_regular_slots':absent,
            'zero_volume_interpretation':'Index volume is normally unavailable/zero' if key.startswith('NSE_INDEX|') else 'Zero volume is retained; not an executable-fill guarantee',
            'examples':examples}

def worker(args):
    return validate_contract(*args)

def run(root=ROOT,out=OUT):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with closing(readonly(root/'queue.sqlite')) as c:
        rows=c.execute("SELECT meta,file,rows FROM jobs WHERE state='done' AND kind='minute'").fetchall()
    groups=defaultdict(list)
    for text,file,n in rows:
        m=json.loads(text);groups[m['key']].append({'meta':m,'file':file,'rows':n})
    sizes={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in root.glob('*.json.gz')}
    started=datetime.now(timezone.utc).isoformat();last_update=0
    with closing(sqlite3.connect(out/'validation.sqlite')) as db,ProcessPoolExecutor(max_workers=3) as pool:
        db.execute('CREATE TABLE IF NOT EXISTS results(instrument TEXT PRIMARY KEY,signature TEXT,result TEXT)');db.commit()
        total=Counter();findings=[];processed=0;cached=0;prepared=[]
        for key,jobs in groups.items():
            signature=hashlib.sha256(json.dumps([VERSION,sorted((j['file'],j['rows'],sizes.get(j['file'])) for j in jobs)]).encode()).hexdigest()
            old=db.execute('SELECT result FROM results WHERE instrument=? AND signature=?',(key,signature)).fetchone()
            prepared.append((key,jobs,signature,old))
        results=pool.map(worker,((root,jobs) for key,jobs,signature,old in prepared if not old),chunksize=4)
        for key,jobs,signature,old in prepared:
            if old:r=json.loads(old[0]);cached+=1
            else:
                r=next(results)
                with db:db.execute('INSERT OR REPLACE INTO results VALUES(?,?,?)',(key,signature,json.dumps(r)))
            findings.append(r);processed+=1
            for k,v in r.items():
                if isinstance(v,int):total[k]+=v
            if time.monotonic()-last_update>5:
                atomic_json(out/'status.json',{'status':'running','snapshot_at':started,'updated':datetime.now(timezone.utc).isoformat(),'contracts_checked':processed,'contracts_in_snapshot':len(groups),'files_in_snapshot':len(rows),'cached_contracts':cached,'counts':dict(total)})
                last_update=time.monotonic()
        with closing(readonly(root/'queue.sqlite')) as c:
            current=c.execute("SELECT COUNT(*) FROM jobs WHERE state='done' AND kind='minute'").fetchone()[0]
        report={'status':'snapshot_validated','snapshot_at':started,'finished':datetime.now(timezone.utc).isoformat(),'contracts_checked':processed,'files_in_snapshot':len(rows),'new_completed_files_since_snapshot':max(0,current-len(rows)),'cached_contracts':cached,'counts':dict(total),
                'limitations':['Only completed minute jobs in the start-of-run snapshot were checked; download continues.','Conflicting rows are comparison occurrences, not unique conflicts; unique_valid_timestamps includes timestamps later found conflicting.','Unobserved regular slots are candidates, not proven missing broker data: special sessions, listing intervals and no-trade minutes are not independently verified.','No data was removed or repaired. Wide spreads are not a rejection criterion.','Failed/pending jobs and completely absent contract days are covered by the separate request/session reports.','Existing validation reuse assumes archived files with unchanged size/mtime are unchanged.']}
        atomic_json(out/'report.json',report);atomic_json(out/'status.json',report)
        columns=sorted(set().union(*(set(r)-{'examples'} for r in findings))) if findings else ['symbol']
        with (out/'contracts.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows({k:v for k,v in r.items() if k!='examples'} for r in findings)
        atomic_json(out/'issues.json',[r for r in findings if r.get('examples') or any(r.get(k,0) for k in ('invalid_values','invalid_timestamp','outside_request','after_expiry','unreadable_files','row_count_mismatch','metadata_mismatch'))])
        print(json.dumps(report,indent=2))

if __name__=='__main__':
    import msvcrt
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'validator.lock').open('a+b') as handle:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise SystemExit('Another validation is already running.')
        try:run()
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
