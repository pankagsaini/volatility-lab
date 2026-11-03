"""As-known-at-entry signals. Never carry a later-retrieved IV backwards."""
import sqlite3,json
from contextlib import closing
from pathlib import Path
import pandas as pd
PATH=Path(__file__).resolve().parent/'data/monitor.sqlite'
def load_signals(symbol):
 if not PATH.exists():return []
 with closing(sqlite3.connect(PATH.resolve().as_uri()+'?mode=ro',uri=True)) as c:rows=c.execute('SELECT expiry,as_of,payload FROM intraday_observations WHERE symbol=? ORDER BY as_of',(symbol,)).fetchall()
 out=[]
 for exp,stamp,payload in rows:
  r=json.loads(payload)
  try:
   asof=pd.Timestamp(stamp);received=pd.Timestamp(r['Retrieved at'])
   if asof.tzinfo is None or received.tzinfo is None:continue
   available=max(asof,received).tz_convert('Asia/Kolkata')
  except (KeyError,ValueError,TypeError):continue
  out.append((exp,available,r))
 return sorted(out,key=lambda x:x[1])
def decide(rows,expiry,day,entry,filters):
 at=pd.Timestamp(day+' '+entry,tz='Asia/Kolkata')
 from backtest_index import SignalIndex
 if isinstance(rows,SignalIndex):rows=rows.for_entry(expiry,day)
 candidates=[r for exp,t,r in rows if exp==expiry and t<=at and t.date()==at.date() and (at-t).total_seconds()<=900]
 if not candidates:return False,'Missing signal known before entry'
 r=candidates[-1]
 for metric,low,high in filters:
  v=r.get(metric)
  try:v=float(v)
  except (TypeError,ValueError):return False,'Missing '+metric
  if not pd.notna(v):return False,'Missing '+metric
  if not low<=v<=high:return False,metric+' outside entry range'
 return True,'Signal conditions met'
