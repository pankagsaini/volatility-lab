"""ATM synthetic future is transparent arithmetic over raw broker LTPs."""
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pandas as pd
from synthetic_future import calculate
import expiry_data
import market_table
from market_data import IST


def option(strike,call,put):
    return {'strike_price':strike,
            'call_options':{'market_data':{'ltp':call}},
            'put_options':{'market_data':{'ltp':put}}}


class SyntheticFutureTests(unittest.TestCase):
    def test_compares_spot_nearest_pair_to_same_expiry_future(self):
        result=calculate([option(24500,120,80),option(24450,145,58)],24484,
                         {'last_price':24535,'timestamp':'2026-11-02T11:00:00+05:30'},
                         {'tradingsymbol':'NIFTY26NOVFUT'})
        self.assertEqual(result['ATM strike'],24500)
        self.assertEqual(result['Synthetic future'],24540)
        self.assertEqual(result['Listed future'],24535)
        self.assertEqual(result['Synthetic − future'],5)
        self.assertEqual(result['Synthetic status'],'Ready')

    def test_equal_distance_uses_lower_listed_strike_deterministically(self):
        result=calculate([option(100,10,8),option(200,24,15)],150,
                         {'last_price':102},{'tradingsymbol':'TEST'})
        self.assertEqual(result['ATM strike'],100)
        self.assertEqual(result['Synthetic future'],102)

    def test_weekly_expiry_without_future_is_explicit_and_never_fabricated(self):
        result=calculate([option(100,10,8)],101,None,None)
        self.assertEqual(result['Synthetic future'],102)
        self.assertIsNone(result['Listed future'])
        self.assertEqual(result['Synthetic status'],'No listed futures contract for this expiry')

    def test_weekly_uses_its_calendar_month_future_and_saved_rows_are_backfilled(self):
        futures=pd.DataFrame([
            {'expiry':pd.Timestamp('2026-11-23').date(),'tradingsymbol':'NIFTY26NOVFUT'},
            {'expiry':pd.Timestamp('2026-12-29').date(),'tradingsymbol':'NIFTY26DECFUT'},
        ])
        contract,basis=expiry_data.month_future_contract(futures,pd.Timestamp('2026-11-09').date())
        self.assertEqual(contract['tradingsymbol'],'NIFTY26NOVFUT')
        self.assertEqual(basis,'Monthly future for weekly expiry month')
        snapshot={'rows':[
            {'Instrument':'NIFTY','Expiry':'2026-11-09','Type':'Weekly','Synthetic future':23000},
            {'Instrument':'NIFTY','Expiry':'2026-11-23','Type':'Monthly','Listed future':23020,
             'Future contract':'NIFTY26NOVFUT','Future quote time':'2026-11-02T15:30:00+05:30'},
        ],'details':[]}
        aligned=expiry_data.align_snapshot(snapshot)
        weekly=aligned['rows'][0]
        self.assertEqual(weekly['Month future LTP'],23020)
        self.assertEqual(weekly['Future contract'],'NIFTY26NOVFUT')
        self.assertEqual(weekly['Future reference'],'Monthly future for weekly expiry month')
        self.assertEqual(weekly['Synthetic status'],'Ready · monthly future reference')

    def test_missing_or_nonpositive_option_ltp_is_withheld(self):
        result=calculate([option(100,0,8)],101,{'last_price':102},{'tradingsymbol':'TEST'})
        self.assertIsNone(result['Synthetic future'])
        self.assertEqual(result['Synthetic status'],'ATM call or put LTP unavailable')

    def test_option_refresh_requests_and_saves_matching_futures_contract(self):
        """The comparison cannot accidentally use a different futures month."""
        with TemporaryDirectory() as folder:
            root=Path(folder)
            now=datetime.now(IST)
            expiry=(now+timedelta(days=14)).date()
            future='NIFTY_TEST_FUT'
            master=pd.DataFrame([
                ('NIFTY',expiry,'CE','NIFTY_TEST_CE',24500),
                ('NIFTY',expiry,'PE','NIFTY_TEST_PE',24500),
                ('NIFTY',expiry,'FUT',future,None),
            ],columns=['name','expiry','instrument_type','tradingsymbol','strike'])
            raw=[{'expiry':str(expiry),'strike_price':24500,
                  'call_options':{'option_greeks':{'iv':12,'delta':.5},'market_data':{'ltp':120}},
                  'put_options':{'option_greeks':{'iv':13,'delta':-.5},'market_data':{'ltp':80}}}]
            class Client:
                provider='Upstox';quote_batch_size=250
                def chain(self,*_):return raw
            client=Client();monitor=expiry_data.ExpiryMonitor();monitor.nfo=master
            monitor.masters=lambda *_:None
            monitor.history=lambda *_:None
            calls=[]
            def request(_client,path,params=None,**_):
                calls.append((path,params))
                if path=='/quote' and any(key=='i' and value=='NSE:NIFTY 50' for key,value in params):
                    return {'NSE:NIFTY 50':{'last_price':24490,'ohlc':{'close':24400},'timestamp':now.isoformat()}}
                return {'NFO:'+future:{'last_price':24535,'timestamp':now.isoformat()}}
            monitor.request=request
            alignment={'IV-RV Spread':1.,'HV30':11.,'RV through':str(now.date()),'RV status':'Same-session close'}
            with patch.object(expiry_data,'DATA',root),\
                 patch.dict(market_table.INDICES,{'NIFTY':'NIFTY 50'},clear=True),\
                 patch.object(expiry_data.base,'valuation_time',return_value=(now,False)),\
                 patch.object(expiry_data.base,'fresh',return_value=True),\
                 patch.object(expiry_data.base,'aligned_spread',return_value=alignment):
                result=monitor.refresh_all(client,.06,{'NIFTY':.01},3.)
            row=result['rows'][0]
            self.assertEqual(row['Synthetic future'],24540)
            self.assertEqual(row['Listed future'],24535)
            self.assertEqual(row['Synthetic − future'],5)
            self.assertEqual(row['Future contract'],future)
            futures_requests=[params for path,params in calls if path=='/quote' and params and any(k=='i' and v=='NFO:'+future for k,v in params)]
            self.assertEqual(len(futures_requests),1)
            saved=__import__('json').loads((root/('nifty_option_quotes_'+str(expiry)+'.json')).read_text())
            self.assertIn('NFO:'+future,saved['quotes'])
            self.assertEqual(saved['contracts'][-1]['tradingsymbol'],future)


if __name__=='__main__':unittest.main()
