import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
import daily_capture as dc
from market_data import IST

class DailyCaptureTests(unittest.TestCase):
    def test_overlapping_run_does_not_touch_status(self):
        with tempfile.TemporaryDirectory() as d,patch.object(dc,'STATUS',Path(d)/'status.json'):
            dc.STATUS.write_text('existing worker')
            with dc.collector_lock() as acquired:
                self.assertTrue(acquired)
                with patch.object(dc,'_run') as work:
                    self.assertEqual(dc.run(),0)
                    work.assert_not_called()
            self.assertEqual(dc.STATUS.read_text(),'existing worker')
            with dc.collector_lock() as acquired:self.assertTrue(acquired)

    def test_failed_run_keeps_retry_eligible_and_records_safe_history(self):
        with tempfile.TemporaryDirectory() as d,patch.object(dc,'STATUS',Path(d)/'status.json'):
            with patch('upstox_adapter.market_client',side_effect=RuntimeError('SECRET')):
                self.assertEqual(dc.run(datetime(2026,10,23,8,30,tzinfo=IST)),1)
                self.assertEqual(dc.run(datetime(2026,10,23,8,35,tzinfo=IST)),1)
            state=json.loads(dc.STATUS.read_text())
            self.assertFalse(state['completed'])
            history=(dc.STATUS.parent/'runs.jsonl').read_text()
            self.assertEqual(len(history.splitlines()),2)
            self.assertNotIn('SECRET',history)
            self.assertNotIn('SECRET',dc.STATUS.read_text())

    def test_completed_stage_does_not_repeat_api_work(self):
        with tempfile.TemporaryDirectory() as d,patch.object(dc,'STATUS',Path(d)/'status.json'):
            dc.STATUS.write_text(json.dumps({'completed':{'2026-10-23_history_catchup':'done'}}))
            with patch('upstox_adapter.market_client') as client:
                self.assertEqual(dc.run(datetime(2026,10,23,8,30,tzinfo=IST)),0)
                client.assert_not_called()

if __name__=='__main__':unittest.main()
