"""Policy tests use explicit fixture inputs, never network or production assumptions."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import unittest
from volatility_lab.pricing_policy import select_model, recorded_inputs


class TestPricingSelection(unittest.TestCase):
    def setUp(self):
        self.stamp = '2026-11-13T04:30:00Z'
        self.expiry = '2026-11-17'
        self.args = dict(spot=25000, expiry=self.expiry, as_of=self.stamp, spot_timestamp=self.stamp,
                         years=4/365, rate=.06, dividend=.0142)
        book = dict(bid_price=198, ask_price=202, bid_qty=100, ask_qty=100, timestamp=self.stamp)
        self.chain = [dict(expiry=self.expiry, strike_price=25000,
                           call_options={'market_data':deepcopy(book)}, put_options={'market_data':deepcopy(book)})]
        self.future = dict(expiry=self.expiry, bid=25019, ask=25021, timestamp=self.stamp)

    def test_exact_expiry_precedence(self):
        selection = select_model(self.chain, **self.args, future_quote=self.future)
        self.assertEqual((selection['model'],selection['source'],selection['forward']), ('black76','listed_future',25020))
        self.future['expiry'] = '2026-11-24'
        self.assertEqual(select_model(self.chain, **self.args, future_quote=self.future)['source'],'synthetic_forward')

    def test_strict_three_percent_boundary_and_one_wide_leg(self):
        for spread in [2.999,3,3.001,20]:
            self.chain[0]['put_options']['market_data'].update(bid_price=200-spread,ask_price=200+spread)
            model = select_model(self.chain, **self.args)['model']
            self.assertEqual(model,'black76' if spread < 3 else 'bsm')

    def test_stale_crossed_missing_and_desynchronized(self):
        for updates in [dict(timestamp='2026-11-13T04:27:59Z'),dict(bid_price=203),dict(bid_qty=0),
                        dict(timestamp='2026-11-13T04:29:54Z')]:
            chain=deepcopy(self.chain);chain[0]['put_options']['market_data'].update(updates)
            self.assertEqual(select_model(chain, **self.args)['model'],'bsm')
        self.args['rate']=None
        self.assertIsNone(select_model(self.chain, **self.args, future_quote=self.future)['model'])
        self.args['rate']=.06;self.args['dividend']=None
        self.assertIsNone(select_model([], **self.args)['model'])
        self.args['dividend']=.0142;self.args['spot_timestamp']='2026-11-12T04:30:00Z'
        self.assertIsNone(select_model(self.chain, **self.args)['model'])

    def test_archive_sources_cannot_look_ahead(self):
        source=dict(rate_source=dict(source='https://www.rbi.org.in/',sha256='fixture',checked_at=self.stamp,model_rate=.06),
                    dividend_source=dict(source='https://www.niftyindices.com/Factsheet/ind_nifty50.pdf',sha256='fixture',
                                         checked_at=self.stamp,as_of='2026-10-30',model_yield=.0142))
        self.assertEqual(recorded_inputs(source,'NIFTY',self.stamp),(.06,.0142))
        self.assertEqual(recorded_inputs(source,'NIFTY','2026-11-12T04:30:00Z'),(None,None))
        self.assertEqual(recorded_inputs({},'NIFTY',self.stamp),(None,None))

    def test_subday_and_expired(self):
        self.args['years']=.01/365
        self.assertEqual(select_model(self.chain, **self.args)['model'],'black76')
        self.args['years']=0
        self.assertIsNone(select_model(self.chain, **self.args)['model'])


class TestPricingNumerics(unittest.TestCase):
    def test_both_models_and_immutable_observations(self):
        from volatility_lab.pricing_policy import price_observed_option
        from volatility_lab.option_math import bsm_greeks, black76_greeks, DEFAULT_PRICING_MODEL
        self.assertEqual(DEFAULT_PRICING_MODEL,'black76')
        T,r,q,vol=30/365,.058,.0142,.22
        F=25000*math.exp((r-q)*T)
        for kind in ['CE','PE']:
            price=bsm_greeks(25000,25200,T,r,vol,kind,dividend=q)['price']
            self.assertAlmostEqual(price,black76_greeks(F,25200,T,r,vol,kind)['price'],places=8)
            for model in ['black76','bsm']:
                selected=dict(model=model,forward=F,rate=r,dividend=q)
                result=price_observed_option(price,25200,T,kind,selected,spot=25000)
                self.assertAlmostEqual(result['iv'],vol,places=7)
                self.assertEqual(selected['model'],model)


if __name__ == '__main__':
    unittest.main()
