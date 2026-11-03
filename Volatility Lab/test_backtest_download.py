import tempfile,unittest,json
from pathlib import Path
from datetime import date
import backtest_download as d
from unittest.mock import patch,MagicMock

class QueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.old=d.ROOT;d.ROOT=Path(self.temp.name);self.c=d.db()
 def tearDown(self):
  self.c.close();d.ROOT=self.old;self.temp.cleanup()
 def test_consolidation_preserves_scope_and_finished_rows(self):
  for expired,key in [(True,'expired'),(False,'active')]:
   d._enqueue_candles(self.c,key,date(2021,1,1),date(2025,12,1),expired,'day',{'symbol':'NIFTY'},299)
  self.c.commit();d.optimize_queue(self.c)
  pending=self.c.execute("select path,meta from jobs where state='pending'").fetchall()
  self.assertGreater(len(pending),2)
  active=[json.loads(text) for path,text in pending if '/days/1/' in path]
  expired=[json.loads(text) for path,text in pending if '/day/' in path]
  self.assertEqual([(m['start'],m['end']) for m in active],[('2021-01-01','2025-12-01')])
  self.assertEqual(min(m['start'] for m in expired),'2021-01-01')
  self.assertEqual(max(m['end'] for m in expired),'2025-12-01')
  self.c.execute("update jobs set state='done' where id=(select id from jobs where state='pending' limit 1)")
  d.optimize_queue(self.c)
  self.assertEqual(self.c.execute("select count(*) from jobs where state='done'").fetchone()[0],1)
 def test_minute_overlap_keeps_only_uncovered_ranges(self):
  d.candle_jobs(self.c,'k',date(2025,1,10),date(2025,1,20),True,'1minute',{'symbol':'NIFTY'})
  self.c.execute("update jobs set state='done'")
  d.candle_jobs(self.c,'k',date(2025,1,1),date(2025,1,31),True,'1minute',{'symbol':'NIFTY'})
  spans=sorted((json.loads(r[0])['start'],json.loads(r[0])['end']) for r in self.c.execute("select meta from jobs where state='pending'"))
  self.assertEqual(spans,[('2025-01-01','2025-01-09'),('2025-01-21','2025-01-31')])
 def test_single_window_is_not_lost(self):
  d.candle_jobs(self.c,'k',date(2025,1,1),date(2025,2,1),True,'day',{'symbol':'NIFTY'})
  self.c.commit();d.optimize_queue(self.c)
  self.assertEqual(self.c.execute("select count(*) from jobs where state='pending'").fetchone()[0],1)
 def test_next_batch_balances_indices_within_priority(self):
  for symbol in ('NIFTY','BANKNIFTY'):
   for n in range(3):d.enqueue(self.c,f'/discovery/{symbol}/{n}',{},'discovery',{'symbol':symbol})
  self.c.commit();jobs=d.next_jobs(self.c,2)
  self.assertEqual({json.loads(row[4])['symbol'] for row in jobs},{'NIFTY','BANKNIFTY'})
 def test_next_batch_keeps_kind_priority_and_fills_single_symbol_work(self):
  d.enqueue(self.c,'/discovery/BANKNIFTY/1',{},'discovery',{'symbol':'BANKNIFTY'})
  d.enqueue(self.c,'/minute/NIFTY/1',{},'minute',{'symbol':'NIFTY'})
  d.enqueue(self.c,'/minute/NIFTY/2',{},'minute',{'symbol':'NIFTY'})
  self.c.commit();jobs=d.next_jobs(self.c,2)
  self.assertEqual(len(jobs),2)
  self.assertEqual({row[3] for row in jobs},{'minute'})
  self.assertEqual({json.loads(row[4])['symbol'] for row in jobs},{'NIFTY'})
 def test_banknifty_weekly_history_is_skipped(self):
  meta={'symbol':'BANKNIFTY','key':'weekly','expired':True,'start':'2024-01-01','end':'2024-12-31','contract':{'weekly':True}}
  # Simulate a legacy row from before the central exclusion guard.
  self.c.execute("insert into jobs(id,path,params,kind,meta) values('weekly','/weekly','{}','discovery',?)",(json.dumps(meta),));self.c.commit()
  self.assertEqual(d.skip_banknifty_weekly_history(self.c),1)
  self.assertEqual(self.c.execute("select state from jobs").fetchone()[0],'skipped')
 def test_new_weekly_banknifty_jobs_are_not_enqueued(self):
  meta={'symbol':'BANKNIFTY','contract':{'weekly':True}}
  d.candle_jobs(self.c,'weekly',date(2024,1,1),date(2024,2,1),True,'1minute',meta)
  d.enqueue(self.c,'/weekly',{},'discovery',meta)
  self.assertEqual(self.c.execute('select count(*) from jobs').fetchone()[0],0)
 def test_retry_minute_ranges_are_already_covered(self):
  d.candle_jobs(self.c,'k',date(2025,1,10),date(2025,1,20),True,'1minute',{'symbol':'NIFTY'})
  self.c.execute("update jobs set state='retry'")
  d.candle_jobs(self.c,'k',date(2025,1,1),date(2025,1,31),True,'1minute',{'symbol':'NIFTY'})
  spans=sorted((json.loads(r[0])['start'],json.loads(r[0])['end']) for r in self.c.execute("select meta from jobs where state='pending'"))
  self.assertEqual(spans,[('2025-01-01','2025-01-09'),('2025-01-21','2025-01-31')])
 def test_ordinary_enqueue_does_not_revive_superseded_work(self):
  d.enqueue(self.c,'/old',{},'discovery',{'symbol':'NIFTY'})
  self.c.execute("update jobs set state='superseded'")
  d.enqueue(self.c,'/old',{},'discovery',{'symbol':'NIFTY'})
  self.assertEqual(self.c.execute('select state from jobs').fetchone()[0],'superseded')
 def test_repair_is_selective_preserves_done_and_runs_once(self):
  cases=[('active','skipped','ActiveContractHistoryNotRequested','NIFTY',False),('timeout','failed','RequestTimeoutError','BANKNIFTY',False),('retry','retry','RequestTimeoutError','NIFTY',False),('done','done','RequestTimeoutError','NIFTY',False),('weekly','skipped','ActiveContractHistoryNotRequested','BANKNIFTY',True),('provider','failed','RequestWorkerError','NIFTY',False)]
  for ident,state,error,symbol,weekly in cases:
   self.c.execute('insert into jobs(id,meta,state,error,attempts,rows,file) values(?,?,?,?,3,99,?)',(ident,json.dumps({'symbol':symbol,'contract':{'weekly':weekly}}),state,error,ident+'.json.gz'))
  self.c.commit()
  original_done=self.c.execute("select * from jobs where id='done'").fetchone()
  self.assertEqual(d.repair_pacing_queue(self.c),3)
  states=dict(self.c.execute('select id,state from jobs'))
  self.assertEqual(states,{'active':'pending','timeout':'pending','retry':'pending','done':'done','weekly':'skipped','provider':'failed'})
  self.assertEqual(self.c.execute("select * from jobs where id='done'").fetchone(),original_done)
  self.assertEqual(self.c.execute('select count(*) from queue_repair_jobs').fetchone()[0],3)
  self.c.execute("update jobs set state='failed',attempts=3,error='RequestTimeoutError' where id='timeout'");self.c.commit()
  self.assertEqual(d.repair_pacing_queue(self.c),0)
  self.assertEqual(self.c.execute("select state,attempts from jobs where id='timeout'").fetchone(),('failed',3))
 def test_request_deadline_covers_complete_child_response(self):
  process=MagicMock();process.is_alive.side_effect=[True,True,False]
  context=MagicMock();context.Process.return_value=process
  with patch.object(d.mp,'get_context',return_value=context):
   with self.assertRaises(d.RequestTimeoutError):d.fetch_job(('id','/path','{}'))
  process.join.assert_any_call(d.REQUEST_TIMEOUT_SECONDS)
  process.terminate.assert_called_once()
  process.close.assert_called_once()
  self.assertEqual(list(d.ROOT.glob('request-*')),[])
 def test_complete_child_payload_is_returned_without_rewriting(self):
  expected={'candles':[['2025-01-01T09:15:00+05:30',1,2,1,2,0,0]]}
  process=MagicMock();process.is_alive.return_value=False;process.exitcode=0
  def make_process(**kwargs):
   process.start.side_effect=lambda:Path(kwargs['args'][1]).write_text(json.dumps(['ok',expected]),encoding='utf-8')
   return process
  context=MagicMock();context.Process.side_effect=make_process
  with patch.object(d.mp,'get_context',return_value=context):self.assertEqual(d.fetch_job(('id','/path','{}')),expected)
  self.assertEqual(list(d.ROOT.glob('request-*')),[])
 def test_long_expired_discovery_is_rechunked(self):
  meta={'symbol':'NIFTY','key':'expired','expired':True,'start':'2024-01-01','end':'2025-01-01','contract':{'weekly':False}}
  d.enqueue(self.c,'/long',{},'discovery',meta);self.c.commit()
  self.assertEqual(d.rechunk_expired_discovery(self.c),1)
  states=[row[0] for row in self.c.execute("select state from jobs")]
  self.assertIn('superseded',states);self.assertGreater(states.count('pending'),1)
if __name__=='__main__':unittest.main()
