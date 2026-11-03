"""Performance changes must still expose corrected prices and new captures."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
from unittest.mock import patch
import gzip
import json
import sqlite3
import numpy as np
import pandas as pd
import expiry_data
from market_table import aligned_spread
from history_policy import register,key
from dashboard_cache import latest_cloud_capture,_latest_capture,_capture_summary


class SnapshotCacheTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.location=patch.object(expiry_data,'DATA',self.root);self.location.start()
        expiry_data._aligned_snapshot.cache_clear()
        days=pd.bdate_range('2026-07-01','2026-10-26')
        prices=100*np.exp(np.cumsum(np.random.default_rng(4).normal(0,.01,len(days))))
        self.prices=pd.DataFrame({'Date':days,'Open':prices,'High':prices*1.01,'Low':prices*.99,'Close':prices})
        self.prices.to_csv(self.root/'nifty_one_year.csv',index=False)
        self.rows=[{'Instrument':'NIFTY','Expiry':e,'As of':'2026-10-26T15:40:00+05:30','IV':iv,'Source':'Upstox supplied IV'}
                   for e,iv in [('2026-10-27',20.),('2026-11-03',21.),('2026-11-09',None)]]
        self.snapshot={'schema':expiry_data.SCHEMA,'config':'current','rows':self.rows,'details':deepcopy(self.rows)}
        self.save()

    def tearDown(self):
        self.location.stop();self.temp.cleanup();expiry_data._aligned_snapshot.cache_clear()

    def save(self):
        (self.root/'expiry_latest.json').write_text(json.dumps(self.snapshot),encoding='utf-8')

    def test_same_session_reuses_hv_and_retains_all_original_results(self):
        rows=deepcopy(self.rows)
        rows.append({**rows[0],'Expiry':'2026-11-23','Source':'daily-close'})
        saved=pd.read_csv(self.root/'nifty_one_year.csv')
        expected=[aligned_spread(r['IV'],r['As of'],r['Source'],saved) for r in rows]
        with patch.object(expiry_data.base,'aligned_spread',wraps=aligned_spread) as calculate:
            result=expiry_data.align_snapshot({'rows':rows,'details':[]})
        self.assertEqual(calculate.call_count,2)
        for row,wanted in zip(result['rows'],expected):
            for field,value in wanted.items():
                if isinstance(value,float) and np.isnan(value):self.assertTrue(np.isnan(row[field]))
                else:self.assertEqual(row[field],value)

    def test_quote_and_candle_changes_invalidate_and_callers_cannot_mutate_cache(self):
        with patch.object(expiry_data,'align_snapshot',wraps=expiry_data.align_snapshot) as align:
            first=expiry_data.load_snapshot('current')
            first['rows'][0]['IV']=999
            again=expiry_data.load_snapshot('current')
            self.assertEqual(again['rows'][0]['IV'],20.)
            self.assertEqual(align.call_count,1)
            self.snapshot['rows'][0]['IV']=22.;self.save()
            changed=expiry_data.load_snapshot('current')
            self.assertAlmostEqual(changed['rows'][0]['IV-RV Spread']-again['rows'][0]['IV-RV Spread'],2.)
            self.assertEqual(align.call_count,2)
            self.prices.loc[self.prices.index[-2],['Open','High','Low','Close']]=[120,121,119,120]
            self.prices.to_csv(self.root/'nifty_one_year.csv',index=False)
            corrected=expiry_data.load_snapshot('current')
            self.assertEqual(align.call_count,3)
            self.assertNotEqual(corrected['rows'][0]['HV30'],changed['rows'][0]['HV30'])
            self.assertIsNone(expiry_data.load_snapshot('other'))

    def test_missing_history_then_new_file_is_not_stuck_unavailable(self):
        target=self.root/'nifty_one_year.csv';target.unlink()
        missing=expiry_data.load_snapshot('current')
        self.assertTrue(np.isnan(missing['rows'][0]['HV30']))
        self.prices.to_csv(target,index=False)
        self.assertTrue(np.isfinite(expiry_data.load_snapshot('current')['rows'][0]['HV30']))


class SharedDashboardTests(TestCase):
    def test_existing_settings_can_be_read_while_importer_holds_write_lock(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'monitor.sqlite'
            register(path,.06,{'NIFTY':.01},3.)
            writer=sqlite3.connect(path)
            try:
                writer.execute('BEGIN IMMEDIATE')
                register(path,.06,{'NIFTY':.01},3.)
            finally:writer.rollback();writer.close()
            register(path,.07,{'NIFTY':.01},3.)
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM calculation_assumptions').fetchone()[0],2)

    def test_cloud_summary_updates_on_new_capture_or_replaced_content(self):
        _capture_summary.clear()
        with TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'data'/'google_archive';archive.mkdir(parents=True)
            self.assertIsNone(latest_cloud_capture(root))
            def save(name,status,day):
                with gzip.open(archive/name,'wt',encoding='utf-8') as handle:
                    json.dump({'day':day,'mode':'snapshot','status':status,'retrieved_at':day+'T10:00:00+00:00'},handle)
            save('2026-10-23_a.json.gz','partial','2026-10-23')
            self.assertIn('partial for 23 Oct',latest_cloud_capture(root))
            save('2026-10-26_a.json.gz','complete','2026-10-26')
            self.assertIn('complete for 26 Oct',latest_cloud_capture(root))
            save('2026-10-26_a.json.gz','corrected','2026-10-26')
            self.assertIn('corrected for 26 Oct',latest_cloud_capture(root))


if __name__=='__main__':main()
