import unittest
from datetime import date
from backtest_engine import simulate
class Tests(unittest.TestCase):
 def base(self):
  cs=[dict(instrument_key=k,instrument_type=k,strike_price=100,expiry='2024-10-10',trading_symbol=k) for k in ['CE','PE']]
  s={'spot':{'2024-10-01 09:19':[100,100,100,100,1]},'CE':{'2024-10-01 09:20':[10,10,10,10,1],'2024-10-01 15:15':[8,8,8,8,1]},'PE':{'2024-10-01 09:20':[10,10,10,10,1],'2024-10-01 15:15':[8,8,8,8,1]}}
  return cs,s
 def test_costs(self):
  cs,s=self.base();t,k=simulate(cs,s,'spot',date(2024,10,1),date(2024,10,1),quantity=10,slip_bps=100,order_fee=1)
  self.assertAlmostEqual(t.iloc[0].Gross,40);self.assertAlmostEqual(t.iloc[0].Slippage,3.6);self.assertAlmostEqual(t.iloc[0].Net,32.4)
 def test_missing(self):
  cs,s=self.base();del s['PE']['2024-10-01 15:15'];t,k=simulate(cs,s,'spot',date(2024,10,1),date(2024,10,1));self.assertTrue(t.empty);self.assertEqual(len(k),1)
 def test_no_future_spot(self):
  cs,s=self.base();s['spot']={'2024-10-01 09:20':[100,100,100,100,1]};t,k=simulate(cs,s,'spot',date(2024,10,1),date(2024,10,1));self.assertTrue(t.empty)
if __name__=='__main__':unittest.main()
