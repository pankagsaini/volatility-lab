import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from catalogue_fetch import refresh
from market_data import MarketDataError

class CatalogueTests(unittest.TestCase):
    def test_transient_failure_recovers_and_records_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'master.json'
            body=gzip.compress(json.dumps([{'instrument_key':'index'}]).encode())
            opener=Mock()
            opener.open.side_effect=[TimeoutError('SECRET'),io.BytesIO(body)]
            with patch('catalogue_fetch.time.sleep'):
                result=refresh(opener,p,'2026-10-23',{'NIFTY':'index'})
            self.assertEqual(result['date'],'2026-10-23')
            log=(p.parent/'catalogue_refresh_events.jsonl').read_text()
            self.assertIn('TimeoutError',log)
            self.assertNotIn('SECRET',log)
            self.assertEqual(opener.open.call_count,2)

    def test_failure_retains_cache_and_reports_http_status(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'master.json';p.write_text('old cache')
            opener=Mock()
            opener.open.side_effect=HTTPError('secret-url',503,'secret-message',{},None)
            with patch('catalogue_fetch.time.sleep'),self.assertRaisesRegex(MarketDataError,'HTTP 503'):
                refresh(opener,p,'2026-10-23',{'NIFTY':'index'})
            self.assertEqual(p.read_text(),'old cache')
            self.assertEqual(opener.open.call_count,3)
            self.assertNotIn('secret',(p.parent/'catalogue_refresh_events.jsonl').read_text())

    def test_invalid_catalogue_never_replaces_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'master.json';p.write_text('old cache')
            opener=Mock();opener.open.side_effect=lambda *a,**kw:io.BytesIO(gzip.compress(b'[]'))
            with patch('catalogue_fetch.time.sleep'),self.assertRaisesRegex(MarketDataError,'validate: ValueError'):
                refresh(opener,p,'2026-10-23',{'NIFTY':'index'})
            self.assertEqual(p.read_text(),'old cache')

    def test_bad_compression_and_json_are_decode_failures(self):
        for body in (b'not-gzip',gzip.compress(b'not-json')):
            with self.subTest(body=body),tempfile.TemporaryDirectory() as d:
                p=Path(d)/'master.json';p.write_text('old cache')
                opener=Mock();opener.open.side_effect=lambda *a,**kw:io.BytesIO(body)
                with patch('catalogue_fetch.time.sleep'),self.assertRaisesRegex(MarketDataError,'decode:'):
                    refresh(opener,p,'2026-10-23',{'NIFTY':'index'})
                self.assertEqual(p.read_text(),'old cache')

    def test_write_contention_recovers_with_save_diagnostic(self):
        from collector_support import atomic_json
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'master.json';p.write_text('old cache')
            body=gzip.compress(json.dumps([{'instrument_key':'index'}]).encode())
            opener=Mock();opener.open.side_effect=lambda *a,**kw:io.BytesIO(body)
            writes=[0]
            def write(path,data):
                writes[0]+=1
                if writes[0]==1:raise PermissionError('private path')
                return atomic_json(path,data)
            with patch('catalogue_fetch.time.sleep'),patch('catalogue_fetch.atomic_json',side_effect=write):
                refresh(opener,p,'2026-10-23',{'NIFTY':'index'})
            logs=[json.loads(s) for s in (p.parent/'catalogue_refresh_events.jsonl').read_text().splitlines()]
            self.assertEqual(logs[0]['attempts'][0]['phase'],'save')
            self.assertEqual(logs[-1]['status'],'complete')
            self.assertEqual(json.loads(p.read_text())['date'],'2026-10-23')

if __name__=='__main__':unittest.main()
