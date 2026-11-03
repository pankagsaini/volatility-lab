import unittest
from unittest.mock import patch
import strategy_backtest as engine

class StrategyTests(unittest.TestCase):
 def base(self):
  contracts=[dict(instrument_key=k,instrument_type=k,strike_price=100,expiry='2024-10-10',trading_symbol=k) for k in ('CE','PE')]
  bars={'spot':{'2024-10-01 09:19':[100,100,100,100,1]}}
  for key in ('CE','PE'):
   bars[key]={f'2024-10-01 {t}':[o,max(o,c),min(o,c),c,1] for t,o,c in [('09:20',10,12),('09:21',15,15),('09:22',8,8)]}
  s=dict(start='2024-10-01',end='2024-10-01',entry='09:20',exit='09:22',min_dte=1,max_dte=35,quantity=10,slippage_bps=0,stop=0,target=0,legs=engine.template('Short straddle'),weekdays=[1],filters=[])
  return contracts,bars,s
 @patch('strategy_backtest.charge',return_value={})
 def test_stop_is_next_open_not_threshold(self,_):
  c,b,s=self.base();s['stop']=30
  t,k=engine.simulate(c,b,'spot',s)
  self.assertEqual(t.iloc[0]['Exit time'],'09:21');self.assertEqual(t.iloc[0].Net,-100)
  self.assertEqual(t.iloc[0]['Exit reason'],'Portfolio stop')
 @patch('strategy_backtest.charge',return_value={})
 def test_long_legs_target_and_slippage(self,_):
  c,b,s=self.base();s.update(legs=engine.template('Long straddle'),target=30,slippage_bps=10)
  t,k=engine.simulate(c,b,'spot',s)
  self.assertEqual(t.iloc[0]['Exit reason'],'Portfolio target');self.assertAlmostEqual(t.iloc[0].Net,99.5)
 def test_missing_risk_path_withheld(self):
  c,b,s=self.base();s['stop']=10000;del b['PE']['2024-10-01 09:21']
  t,k=engine.simulate(c,b,'spot',s);self.assertTrue(t.empty);self.assertIn('monitoring',k.iloc[0].Reason)
 def test_missing_exact_strike_not_substituted(self):
  c,b,s=self.base();s['legs'][0]['Offset']=50
  t,k=engine.simulate(c,b,'spot',s);self.assertTrue(t.empty);self.assertIn('strike',k.iloc[0].Reason)
 def test_spot_no_lookahead(self):
  c,b,s=self.base();b['spot']={'2024-10-01 09:20':[100,100,100,100,1]}
  t,k=engine.simulate(c,b,'spot',s);self.assertTrue(t.empty)
 def test_missing_signal_not_invented(self):
  c,b,s=self.base();s['filters']=[('IV-RV Spread',0,10)]
  t,k=engine.simulate(c,b,'spot',s);self.assertTrue(t.empty);self.assertIn('signal',k.iloc[0].Reason)
 @patch('strategy_backtest.charge',return_value={})
 def test_broker_lot_sizing_uses_each_dated_contract_lot(self,_):
  c,b,s=self.base();c[0]['lot_size']=25;c[1]['lot_size']=65;s.pop('quantity');s['lots']=2
  t,_=engine.simulate(c,b,'spot',s);details=__import__('json').loads(t.iloc[0].Legs)
  self.assertEqual(t.iloc[0]['Sizing'],'broker_lot_size')
  self.assertEqual([x['Quantity'] for x in details],[50,130])
  self.assertEqual([x['Lot size'] for x in details],[25,65])
 @patch('strategy_backtest.charge',return_value={})
 def test_long_debit_trade_is_rejected_above_capital(self,_):
  c,b,s=self.base()
  for r in c:r['lot_size']=25
  s.pop('quantity');s.update(lots=1,legs=engine.template('Long straddle'),capital_allocated=400)
  t,k=engine.simulate(c,b,'spot',s)
  self.assertTrue(t.empty);self.assertIn('exceeds allocation',k.iloc[0].Reason)
 @patch('strategy_backtest.charge',return_value={})
 def test_short_strategy_never_invents_margin(self,_):
  c,b,s=self.base()
  for r in c:r['lot_size']=25
  s.pop('quantity');s['lots']=1
  t,_=engine.simulate(c,b,'spot',s)
  self.assertTrue(__import__('pandas').isna(t.iloc[0]['Capital required']))
  self.assertEqual(t.iloc[0]['Capital source'],'Broker margin not supplied')
 @patch('strategy_backtest.charge',return_value={})
 def test_manual_margin_scales_by_strategy_lots_and_enforces_cap(self,_):
  c,b,s=self.base()
  for r in c:r['lot_size']=25
  s.pop('quantity');s.update(lots=2,margin_per_lot=1000,capital_allocated=1500)
  t,k=engine.simulate(c,b,'spot',s)
  self.assertTrue(t.empty);self.assertIn('₹2,000 exceeds allocation ₹1,500',k.iloc[0].Reason)
if __name__=='__main__':unittest.main()
