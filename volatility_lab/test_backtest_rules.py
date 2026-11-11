import unittest
import pandas as pd
from broker_charges import rates,charge
from futures_backtest import simulate
from backtest_signals import decide
class TradingRulesTests(unittest.TestCase):
 def test_stt_effective_dates_and_sides(self):
  self.assertEqual(rates('2026-03-31')['fut_stt'],.0002)
  self.assertEqual(rates('2026-04-01')['fut_stt'],.0005)
  self.assertEqual(rates('2024-09-30')['opt_stt'],.000625)
  self.assertEqual(rates('2024-10-01')['opt_stt'],.001)
  self.assertEqual(charge('2026-04-01','Options',100,75,'buy')['STT'],0)
  self.assertEqual(charge('2026-04-01','Options',100,75,'sell')['STT'],11.25)
 def test_small_futures_brokerage_and_gst(self):
  c=charge('2026-04-01','Futures',100,10,'buy')
  self.assertAlmostEqual(c['Brokerage'],.5)
  self.assertAlmostEqual(c['GST'],.18*(c['Brokerage']+c['Exchange']+c['IPFT']))
 def test_upstox_plan_brokerage_and_history_boundary(self):
  self.assertEqual(rates('2026-10-24')['opt_stt'],.0015)
  self.assertEqual(charge('2026-10-24','Options',100,75,'buy',30)['Brokerage'],30)
  with self.assertRaises(ValueError):rates('not-a-date')
 def test_signal_cannot_use_future_or_stale_record(self):
  rows=[('2026-10-27',pd.Timestamp('2026-10-21 09:21',tz='Asia/Kolkata'),{'IV-RV Spread':5})]
  self.assertFalse(decide(rows,'2026-10-27','2026-10-21','09:20',[('IV-RV Spread',1,10)])[0])
  rows[0]=('2026-10-27',pd.Timestamp('2026-10-21 09:19',tz='Asia/Kolkata'),{'IV-RV Spread':5})
  self.assertTrue(decide(rows,'2026-10-27','2026-10-21','09:20',[('IV-RV Spread',1,10)])[0])
 @staticmethod
 def _bars(near_exp,far_exp,records):
  rows=[]
  for day,near_open,near_close,far_open,far_close in records:
   rows.extend([
    {'day':day,'expiry':near_exp,'open':near_open,'high':near_close,'low':near_open,'close':near_close,'volume':100,'oi':0},
    {'day':day,'expiry':far_exp,'open':far_open,'high':far_close,'low':far_open,'close':far_close,'volume':100,'oi':0},
   ])
  return pd.DataFrame(rows)
 def test_futures_next_open_and_direction(self):
  bars=self._bars('2025-02-27','2025-03-27',[
   ('2025-01-06',100,100,100.45,100.45),
   ('2025-01-07',100,100,100.60,100.60),
   ('2025-01-08',100,100,100.80,100.80),
  ])
  t,s,p=simulate(bars,pair=(0,1),entry_carry=8,target_carry=4,quantity=10,slip_bps=0,near_side=-1)
  self.assertEqual(len(t),1);self.assertEqual(t.iloc[0]['EntryDay'],'2025-01-07');self.assertEqual(t.iloc[0]['ExitDay'],'2025-01-08')
  self.assertAlmostEqual(t.iloc[0]['GrossPnL'],2.)
  self.assertAlmostEqual(t.iloc[0]['NetPnL'],t.iloc[0]['GrossPnL']-t.iloc[0]['Charges'])
 def test_missing_leg_never_filled_from_next_expiry(self):
  bars=pd.DataFrame([
   {'day':'2025-01-06','expiry':'2025-02-27','open':100,'high':100,'low':100,'close':100,'volume':10,'oi':0},
   {'day':'2025-01-06','expiry':'2025-03-27','open':100.45,'high':100.45,'low':100.45,'close':100.45,'volume':10,'oi':0},
   {'day':'2025-01-07','expiry':'2025-02-27','open':100,'high':100,'low':100,'close':100,'volume':10,'oi':0},
   {'day':'2025-01-07','expiry':'2025-04-24','open':101,'high':101,'low':101,'close':101,'volume':10,'oi':0},
  ])
  t,s,p=simulate(bars,pair=(0,1),entry_carry=8,target_carry=0,quantity=10,near_side=-1)
  self.assertTrue(t.empty);self.assertIsNone(p['unfinished']);self.assertFalse(s.empty)
 def test_open_position_has_separate_latest_exact_contract_mark(self):
  bars=self._bars('2025-02-27','2025-03-27',[
   ('2025-01-06',100,100,100.45,100.45),
   ('2025-01-07',100,100,100.60,100.60),
   ('2025-01-08',100,100,100.80,100.80),
  ])
  t,s,p=simulate(bars,pair=(0,1),entry_carry=5,target_carry=0,quantity=10,slip_bps=0)
  self.assertTrue(t.empty);self.assertEqual(p['unfinished']['status'],'open')
  self.assertEqual(p['unfinished']['mark_day'],'2025-01-08')
  self.assertAlmostEqual(p['unfinished']['unrealized_gross'],-2.)
 def test_expiry_boundary_is_recorded_without_forced_close(self):
  bars=self._bars('2025-01-10','2025-02-10',[
   ('2025-01-01',100,100,100.45,100.45),
   ('2025-01-02',100,100,100.45,100.45),
   ('2025-01-10',float('nan'),100,100.45,100.45),
  ])
  t,s,p=simulate(bars,pair=(0,1),entry_carry=5,target_carry=0,quantity=10,slip_bps=0)
  self.assertTrue(t.empty);self.assertEqual(p['unfinished']['status'],'open')
  self.assertIn('Missing executable exit quotes',s.iloc[-1]['Reason'])
 def test_pending_exit_records_missing_exact_open(self):
  bars=self._bars('2025-02-27','2025-03-27',[
   ('2025-01-06',100,100,100.45,100.45),
   ('2025-01-07',100,100,100.60,100.60),
   ('2025-01-08',100,100,100.10,100.10),
   ('2025-01-09',100,100,float('nan'),100.10),
  ])
  t,s,p=simulate(bars,pair=(0,1),entry_carry=5,target_carry=6,quantity=10,slip_bps=0)
  self.assertTrue(t.empty);self.assertEqual(p['unfinished']['status'],'open')
  self.assertIn('Missing executable exit quotes',s.iloc[-1]['Reason'])
  self.assertEqual(p['unfinished']['mark_day'],'2025-01-09')
if __name__=='__main__':unittest.main()
