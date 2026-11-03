"""Archive safety and idempotency regressions, using temporary local files."""
import gzip,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import google_archive_sync as g

class ArchiveTests(unittest.TestCase):
    def test_holiday_feed_time_does_not_create_new_session(self):
        from datetime import datetime
        from market_data import IST
        from market_table import valuation_time
        from futures_data import quote_value
        now=datetime(2026,10,20,12,0,tzinfo=IST)
        q={'timestamp':now.isoformat(),'last_trade_time':'2026-10-19T15:29:59+05:30','last_price':100.,'depth':{'buy':[{'price':99.,'quantity':1}],'sell':[{'price':101.,'quantity':1}]}}
        stamp,closing=valuation_time(q,now)
        self.assertTrue(closing);self.assertEqual(str(stamp.date()),'2026-10-19')
        result=quote_value(q,now)
        self.assertEqual(result['Source'],'Closing LTP estimate')
        self.assertEqual(result['As of'][:10],'2026-10-19')

    def test_rejects_wrong_provider_and_naive_time(self):
        base={'schema':1,'provider':'Upstox','mode':'snapshot','retrieved_at':'2026-10-20T10:00:00Z','records':[]}
        for change in ({'provider':'Unknown'},{'retrieved_at':'2026-10-20'}):
            with self.assertRaises(ValueError):g.validated(gzip.compress(json.dumps({**base,**change}).encode()))
    def test_verified_copy_receipt_and_repeat_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'drive';source.mkdir();store=root/'local';config=root/'config.json'
            config.write_text('{"cleanup_approved":true}')
            name='2026-10-20_snapshot_120000_complete.json.gz'
            raw=gzip.compress(json.dumps({'schema':1,'provider':'Upstox','mode':'snapshot','retrieved_at':'2026-10-20T06:30:00Z','records':[]}).encode())
            (source/name).write_bytes(raw)
            with patch.object(g,'STORE',store),patch.object(g,'CONFIG',config),patch.object(g,'source_folder',return_value=source),patch.object(g,'import_report',return_value=0) as importer:
                self.assertEqual(g.sync_once(None,{},3.),1)
                self.assertEqual((store/name).read_bytes(),raw)
                self.assertEqual(g.sync_once(None,{},3.),0)
                self.assertEqual(importer.call_count,1)
                (store/name).unlink()
                self.assertEqual(g.sync_once(None,{},3.),1)
                self.assertEqual((store/name).read_bytes(),raw)
                self.assertEqual(importer.call_count,2)
                # Explicit repair must detect even same-size local corruption.
                (store/name).write_bytes(b'x'*len(raw))
                self.assertEqual(g.sync_once(None,{},3.,verify_receipt=True),1)
                self.assertEqual((store/name).read_bytes(),raw)
                renewed=json.loads((source/'local_verified_receipts.json').read_text())
                self.assertEqual(renewed['files'][name]['sha256'],g.hashlib.sha256(raw).hexdigest())
                receipt=json.loads((source/'local_verified_receipts.json').read_text())
                self.assertIn(name,receipt['files'])
                self.assertTrue((source/name).exists())
                # Corruption must not produce a successful import or refreshed receipt.
                (source/name).write_bytes(b'broken gzip')
                self.assertEqual(g.sync_once(None,{},3.),0)
                self.assertEqual(json.loads((store/'status.json').read_text())['status'],'partial')

if __name__=='__main__':unittest.main()
