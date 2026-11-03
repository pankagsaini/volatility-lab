"""Regression checks for retaining honest timestamps and broker delta conventions."""
import unittest
import math
import pandas as pd
from expiry_data import preserve_failed_rows
from option_math import delta_iv
from quote_status import snapshot_status

class AccuracyGuards(unittest.TestCase):
    def test_failed_refresh_keeps_entire_old_row_only_for_same_config(self):
        old={'Instrument':'NIFTY','Expiry':'2026-10-19','As of':'2026-10-16T15:40:00+05:30','IV':11.,'Price':23000,'Source':'Upstox supplied IV'}
        failed={**old,'As of':None,'IV':None,'Price':24000,'Source':'Unavailable'}
        previous={'provider':'Upstox','config':'a','rows':[old]}
        latest={**previous,'rows':[failed]}
        retained=preserve_failed_rows(latest,previous)['rows'][0]
        self.assertEqual(retained['Price'],23000)
        self.assertEqual(retained['As of'],old['As of'])
        self.assertEqual(preserve_failed_rows(latest,{**previous,'config':'b'}),latest)
        self.assertEqual(preserve_failed_rows(latest,{**previous,'provider':'Other provider'}),latest)
        good={**latest,'rows':[{**old,'IV':12.}]}
        self.assertEqual(preserve_failed_rows(good,previous),good)

    def test_broker_snapshot_is_not_labelled_live(self):
        self.assertIn('Earlier session',snapshot_status('2026-10-16T15:40:00+05:30','Upstox supplied IV',True,now='2026-10-17T10:00:00+05:30'))
        self.assertEqual(snapshot_status('2026-10-18T15:40:00+05:30','Upstox supplied IV',True,now='2026-10-17T10:00:00+05:30'),'Invalid future timestamp')

    def test_delta_signs_and_interpolation(self):
        frame=pd.DataFrame({'option_type':['CE','CE','CE'],'iv':[.1,.2,5.],'delta':[.2,.3,-.25]})
        self.assertAlmostEqual(delta_iv(frame,'CE',.25),.15)
        self.assertTrue(math.isnan(delta_iv(frame.iloc[2:],'CE',.25)))

if __name__=='__main__':unittest.main()
