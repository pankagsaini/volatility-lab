import json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from archive_coverage import inventory,session_report,daily_sessions
from strategy_backtest import simulate,template

class CoverageTests(unittest.TestCase):
 def test_queue_inventory_distinguishes_empty_failed_and_missing_files(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with closing(sqlite3.connect(root/'queue.sqlite')) as c,c:
    c.execute('CREATE TABLE jobs(kind,meta,state,rows,file)')
    m=json.dumps({'symbol':'NIFTY','key':'CE','start':'2024-10-01','end':'2024-10-03','contract':{'expiry':'2024-10-10','instrument_type':'CE'}})
    c.executemany('INSERT INTO jobs VALUES(?,?,?,?,?)',[
      ('discovery',m,'pending',None,None),('minute',m,'done',0,'empty.gz'),
      ('minute',m,'done',5,'absent.json.gz'),('minute',m,'failed',None,None),
      ('discovery',m,'superseded',None,None)])
   # A finished file's presence is tested, not its market content.
   (root/'empty.json.gz').write_bytes(b'x')
   with closing(sqlite3.connect(root/'queue.sqlite')) as c,c:c.execute("UPDATE jobs SET file='empty.json.gz' WHERE file='empty.gz'")
   r=inventory(root);s=r['summary'][0]
   self.assertEqual(s['Discovery pending'],1);self.assertEqual(s['Empty minute responses'],1)
   self.assertEqual(s['Missing raw files'],1);self.assertEqual(s['Failed requests'],1)
   self.assertEqual(s['Stored minute rows'],5)

 def test_session_evidence_does_not_invent_holidays(self):
  r=session_report({'2024-10-01 09:19':[1]},'2024-10-01','2024-10-03',['2024-10-01','2024-10-03'])
  self.assertEqual(r['confirmed_sessions_missing_minutes'],1)
  self.assertEqual(r['unverified_dates'],1)
  self.assertIn('Unverified',r['sessions'][1]['Status'])

 def test_ordinary_weekends_are_not_counted_as_unverified_weekdays(self):
  r=session_report({'2024-10-05 10:00':[1]},'2024-10-04','2024-10-07')
  self.assertEqual(r['unverified_dates'],2)
  self.assertEqual(r['sessions'][1]['Status'],'Minute observations present')
  self.assertIn('Weekend',r['sessions'][2]['Status'])

 def test_daily_reference_and_missing_session_reaches_engine(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'daily.sqlite'
   with closing(sqlite3.connect(path)) as c,c:
    c.execute('CREATE TABLE candles(instrument,interval,stamp,open,high,low,close)')
    c.execute("INSERT INTO candles VALUES('spot','day','2024-10-01T00:00:00+05:30',100,101,99,100)")
   days=daily_sessions('spot','2024-10-01','2024-10-02',path)
   s=dict(start='2024-10-01',end='2024-10-02',entry='09:20',exit='15:15',min_dte=1,max_dte=35,quantity=1,slippage_bps=0,stop=0,target=0,legs=template('Short straddle'),weekdays=[0,1,2,3,4],confirmed_sessions=days)
   trades,skips=simulate([],{},'spot',s)
   self.assertTrue(trades.empty);self.assertEqual(len(skips),1)
   self.assertEqual(skips.iloc[0]['Date'],'2024-10-01')
   self.assertIn('spot',skips.iloc[0]['Reason'])

if __name__=='__main__':unittest.main()
