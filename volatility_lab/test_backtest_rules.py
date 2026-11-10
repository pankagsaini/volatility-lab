import unittest
import pandas as pd
from broker_charges import rates,charge
from futures_backtest import simulate
from backtest_signals import decide
class TradingRulesTests(unittest.TestCase):
 def test_stt_effective_dates_and_sides(self):
  self.assertEqual(rates('2026-03-31','Futures')['stt'],.0002)
  self.assertEqual(rates('2026-04-01','Futures')['stt'],.0005)
  self.assertEqual(rates('2024-09-30','Options')['stt'],.000625)
  self.assertEqual(rates('2024-10-01','Options')['stt'],.001)
  self.assertEqual(charge('2026-04-01','Options',100,75,'buy')['STT'],0)
  self.assertEqual(charge('2026-04-01','Options',100,75,'sell')['STT'],11.25)
 def test_small_futures_brokerage_and_gst(self):
  c=charge('2026-04-01','Futures',100,10,'buy')
  self.assertAlmostEqual(c['Brokerage'],.5)
  self.assertAlmostEqual(c['GST'],.18*(c['Brokerage']+c['Exchange/IPFT']))
 def test_upstox_plan_brokerage_and_history_boundary(self):
  self.assertEqual(rates('2026-10-24','Options',30)['brokerage'],30)
  self.assertEqual(charge('2026-10-24','Options',100,75,'buy',30)['Brokerage'],30)
  with self.assertRaises(ValueError):rates('2024-03-31','Options')
 def test_signal_cannot_use_future_or_stale_record(self):
  rows=[('2026-10-27',pd.Timestamp('2026-10-21 09:21',tz='Asia/Kolkata'),{'IV-RV Spread':5})]
  self.assertFalse(decide(rows,'2026-10-27','2026-10-21','09:20',[('IV-RV Spread',1,10)])[0])
  rows[0]=('2026-10-27',pd.Timestamp('2026-10-21 09:19',tz='Asia/Kolkata'),{'IV-RV Spread':5})
  self.assertTrue(decide(rows,'2026-10-27','2026-10-21','09:20',[('IV-RV Spread',1,10)])[0])
 def test_futures_next_open_and_direction(self):
  bars={}
  for day,near,far in [('2025-01-06',100,100.45),('2025-01-07',100,100.60),('2025-01-08',100,100.80)]:
   for exp,px in [('2025-02-27',near),('2025-03-27',far)]:bars[day,exp]=(px,px,px,px,100)
  t,s,p=simulate(bars,pair=(0,1),quantity=10,slip_bps=0,near_side=-1)
  self.assertEqual(len(t),1);self.assertEqual(t.iloc[0]['Entry'],'2025-01-07');self.assertEqual(t.iloc[0]['Exit'],'2025-01-08')
  self.assertAlmostEqual(t.iloc[0]['Gross'],2.)
  reverse,_,_=simulate(bars,pair=(0,1),quantity=10,slip_bps=0,near_side=1)
  self.assertAlmostEqual(reverse.iloc[0]['Gross'],-2.)
  self.assertAlmostEqual(t.iloc[0]['Net'],t.iloc[0]['Gross']-t.iloc[0]['Charges'])
 def test_missing_leg_never_filled_from_next_expiry(self):
  bars={('2025-01-06','2025-02-27'):(100,100,100,100,10),('2025-01-06','2025-03-27'):(100.45,100.45,100.45,100.45,10),('2025-01-07','2025-02-27'):(100,100,100,100,10),('2025-01-07','2025-04-24'):(101,101,101,101,10)}
  t,s,p=simulate(bars,pair=(0,1));self.assertTrue(t.empty);self.assertIsNone(p);self.assertFalse(s.empty)
 def test_open_position_has_separate_latest_exact_contract_mark(self):
  bars={}
  for day,near,far in [('2025-01-06',100,100.45),('2025-01-07',100,100.60),('2025-01-08',100,100.80)]:
   for exp,px in [('2025-02-27',near),('2025-03-27',far)]:bars[day,exp]=(px,px,px,px,100)
  t,s,p=simulate(bars,pair=(0,1),target=50,quantity=10,slip_bps=0)
  self.assertTrue(t.empty);self.assertEqual(p['Status'],'End of selected period')
  self.assertEqual(p['Last mark date'],'2025-01-08');self.assertAlmostEqual(p['Unrealised gross'],2.)
  self.assertEqual(p['Mark status'],'Latest exact-contract close')
 def test_expiry_boundary_is_recorded_without_forced_close(self):
  bars={}
  for day in ('2025-01-01','2025-01-02','2025-01-10'):
   for exp,px in [('2025-01-10',100),('2025-02-10',100.45)]:bars[day,exp]=(px,px,px,px,100)
  t,s,p=simulate(bars,pair=(0,1),target=50,slip_bps=0)
  self.assertTrue(t.empty);self.assertEqual(p['Status'],'Expiry boundary reached before an executable exit')
  self.assertIn('Expiry boundary',s.iloc[-1].Reason)
 def test_pending_exit_records_missing_exact_open(self):
  bars={}
  for day,near,far,volume in [('2025-01-06',100,100.45,100),('2025-01-07',100,100.60,100),('2025-01-08',100,101.50,100),('2025-01-09',100,101.50,0)]:
   for exp,px in [('2025-02-27',near),('2025-03-27',far)]:bars[day,exp]=(px,px,px,px,volume)
  t,s,p=simulate(bars,pair=(0,1),target=12.0,slip_bps=0)
  self.assertTrue(t.empty);self.assertIn('Pending exit: Carry target',p['Status']);self.assertIn('exact exit open unavailable',p['Status'])
  self.assertEqual(p['Last mark date'],'2025-01-08')
if __name__=='__main__':unittest.main()
