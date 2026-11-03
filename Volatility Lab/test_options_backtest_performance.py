"""Indexes preserve contract selection, signal freshness and raw conflict handling."""
from contextlib import closing
from datetime import date
import gzip,json,sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
from unittest.mock import patch
import pandas as pd
from backtest_index import ContractIndex,SignalIndex
from backtest_signals import decide
import backtest_engine
import strategy_backtest
import test_strategy_backtest


class IndexedBacktestTests(TestCase):
 def test_tolerance_and_duplicate_definitions_match_full_scan(self):
  contracts=[dict(instrument_key=str(i),instrument_type=kind,strike_price=strike,expiry=expiry)
             for i,(kind,strike,expiry) in enumerate([
              ('CE',100,'2024-10-10'),('CE',100,'2024-10-10'),('CE',100.0009,'2024-10-10'),
              ('CE',100.0011,'2024-10-10'),('PE',100,'2024-10-10'),('CE',100,'2024-11-10')])]
  index=ContractIndex(contracts)
  self.assertEqual(index.eligible(date(2024,10,1),1,35),['2024-10-10'])
  for target in (99.999,100,100.001,100.002,200):
   for kind in ('CE','PE'):
    expected=[r for r in contracts if r['expiry']=='2024-10-10' and r['instrument_type']==kind and abs(float(r['strike_price'])-target)<.001]
    self.assertCountEqual(index.exact('2024-10-10',kind,target),expected)

 def test_signal_index_keeps_input_order_freshness_and_expiry_boundaries(self):
  expiry='2024-10-10'
  rows=[(expiry,pd.Timestamp('2024-10-01 '+t,tz='Asia/Kolkata'),{'IV':iv})
        for t,iv in [('09:19',21),('09:06',19),('09:21',99),('08:59',1)]]
  rows+=[('2024-11-10',pd.Timestamp('2024-10-01 09:20',tz='Asia/Kolkata'),{'IV':99}),
         (expiry,pd.Timestamp('2024-09-30 09:19',tz='Asia/Kolkata'),{'IV':99})]
  index=SignalIndex(rows)
  for day in ('2024-09-30','2024-10-01','2024-10-02'):
   for clock in ('09:00','09:05','09:20','09:21','09:22','09:37'):
    self.assertEqual(decide(index,expiry,day,clock,[('IV',10,25)]),decide(rows,expiry,day,clock,[('IV',10,25)]))

 def test_entry_fee_is_evaluated_once_and_audit_details_stay_final_only(self):
  contracts,series,settings=test_strategy_backtest.StrategyTests().base()
  settings.update(stop=100000,target=100000)
  with patch.object(strategy_backtest,'charge',return_value={'cost':1.}) as charge:
   trades,skips=strategy_backtest.simulate(contracts,series,'spot',settings)
  self.assertEqual(len(trades),1);self.assertTrue(skips.empty)
  # Two entry legs, two monitored closes, then one final exit for both legs.
  self.assertEqual(charge.call_count,2+2*2+2)
  self.assertEqual(trades.iloc[0].Charges,4.)
  self.assertEqual(len(json.loads(trades.iloc[0].Legs)),2)
  self.assertEqual(trades.iloc[0]['Exit time'],'09:22')

 def test_missing_path_still_skips_before_requesting_unsupported_dated_costs(self):
  contracts,series,settings=test_strategy_backtest.StrategyTests().base()
  settings['stop']=1
  del series['PE']['2024-10-01 09:20']
  with patch.object(strategy_backtest,'charge',side_effect=AssertionError('Must not price a skipped trade')):
   trades,skips=strategy_backtest.simulate(contracts,series,'spot',settings)
  self.assertTrue(trades.empty);self.assertIn('entry candle',skips.iloc[0].Reason)


class ArchiveLoaderTests(TestCase):
 def test_date_timezone_conflicts_and_new_jobs_are_preserved(self):
  with TemporaryDirectory() as folder:
   root=Path(folder)
   with closing(sqlite3.connect(root/'queue.sqlite')) as con:
    con.execute('CREATE TABLE jobs(kind TEXT,meta TEXT,file TEXT,state TEXT)')
    def add(kind,meta,filename,data):
     with gzip.open(root/filename,'wt',encoding='utf-8') as f:json.dump({'data':data},f)
     con.execute('INSERT INTO jobs VALUES(?,?,?,?)',(kind,json.dumps(meta),filename,'done'));con.commit()
    add('contracts',{'symbol':'NIFTY'},'contracts.gz',[{'instrument_type':'CE','instrument_key':'call','expiry':'2024-10-10'}])
    meta={'symbol':'NIFTY','key':'call','start':'2024-09-30','end':'2024-10-02'}
    add('minute',meta,'one.gz',{'candles':[
     ['2024-09-30T23:00:00+00:00',1,2,1,2,10],
     ['2024-10-01T09:20:00+05:30',2,3,1,2,10],
     ['2024-10-02T09:20:00+05:30',9,9,9,9,1],
     ['2024-10-01T09:21:00',8,8,8,8,1]]})
    add('minute',meta,'two.gz',{'candles':[
     ['2024-10-01T04:30:00+05:30',1,2,1,2,10],
     ['2024-10-01T09:20:00+05:30',4,4,4,4,10]]})
    con.execute('INSERT INTO jobs VALUES(?,?,?,?)',('minute',json.dumps({**meta,'symbol':'BANKNIFTY'}),'not-read.gz','done'))
    con.execute('INSERT INTO jobs VALUES(?,?,?,?)',('minute',json.dumps({**meta,'start':'2024-09-01','end':'2024-09-02'}),'not-read.gz','done'));con.commit()
    with patch.object(backtest_engine,'ROOT',root):
     contracts,series,conflicts=backtest_engine.load('NIFTY','2024-10-01','2024-10-01')
     self.assertEqual(conflicts,1)
     self.assertEqual(series,{'call':{'2024-10-01 04:30':[1,2,1,2,10]}})
     self.assertEqual(len(contracts),1)
     add('minute',meta,'three.gz',{'candles':[['2024-10-01T09:22:00+05:30',5,5,5,5,1]]})
     _,updated,_=backtest_engine.load('NIFTY','2024-10-01','2024-10-01')
     self.assertIn('2024-10-01 09:22',updated['call'])


if __name__=='__main__':main()
