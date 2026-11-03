"""IV history accepts broker values captured through Google Cloud only."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np

import expiry_data
import snapshot_history
import smile


class IVTrustPolicyTests(unittest.TestCase):
    def test_daily_iv_history_rejects_non_cloud_sources(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(expiry_data, 'DATA', Path(tmp)):
            expiry_data.save_observation('NIFTY', '2026-10-27', 'cfg', '2026-10-16', 14.2, np.nan,
                                         'Upstox supplied IV')
            self.assertTrue(expiry_data.history('NIFTY', '2026-10-27', 'cfg').empty)

            expiry_data.save_observation('NIFTY', '2026-10-27', 'cfg', '2026-10-19', 14.4, np.nan,
                                         'Upstox supplied IV (Google Cloud)')
            saved = expiry_data.history('NIFTY', '2026-10-27', 'cfg')
            self.assertEqual(saved.Source.tolist(), ['Upstox supplied IV (Google Cloud)'])

    def test_intraday_save_replaces_local_row_only_with_cloud_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'monitor.sqlite'
            row = {'Instrument': 'NIFTY', 'Expiry': '2026-10-27',
                   'As of': '2026-10-19T10:00:00+05:30', 'Source': 'Upstox supplied IV',
                   'IV': 14.5}
            snapshot_history.save(path, 'cfg', row)
            self.assertFalse(path.exists())

            with closing(sqlite3.connect(path)) as con:
                con.execute('CREATE TABLE intraday_observations (symbol TEXT, expiry TEXT, config TEXT, as_of TEXT, payload TEXT, PRIMARY KEY(symbol,expiry,config,as_of))')
                con.execute('INSERT INTO intraday_observations VALUES (?,?,?,?,?)',
                            ('NIFTY', '2026-10-27', 'cfg', row['As of'], json.dumps(row)))
                con.commit()

            cloud_row = {**row, 'Capture source': 'Google Drive'}
            snapshot_history.save(path, 'cfg', cloud_row)
            with closing(sqlite3.connect(path)) as con:
                saved = json.loads(con.execute('SELECT payload FROM intraday_observations').fetchone()[0])
            self.assertEqual(saved['Capture source'], 'Google Drive')

    def test_smile_storage_ignores_non_cloud_calculations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'monitor.sqlite'
            smile.save_smile(path, 'NIFTY', '2026-10-27', 'cfg', '2026-10-19T10:00:00+05:30', {})
            self.assertFalse(path.exists())
            smile.save_smile(path, 'NIFTY', '2026-10-27', 'cfg', '2026-10-19T10:00:00+05:30',
                             {'points': []}, capture_source='Google Drive')
            self.assertIsNotNone(smile.load_smile(path, 'NIFTY', '2026-10-27', 'cfg',
                                                  '2026-10-19T10:00:00+05:30'))

    def test_latest_smile_returns_its_cloud_capture_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'monitor.sqlite'
            older = {'points': [{'Point': 'ATM', 'IV': 15.0}]}
            newer = {'points': [{'Point': 'ATM', 'IV': 16.0}]}
            smile.save_smile(path, 'NIFTY', '2026-10-27', 'old_cfg',
                             '2026-10-19T10:00:00+05:30', older, capture_source='Google Drive')
            smile.save_smile(path, 'NIFTY', '2026-10-27', 'old_cfg',
                             '2026-10-20T10:00:00+05:30', newer, capture_source='Google Drive')
            smile.save_smile(path, 'NIFTY', '2026-10-27', 'cfg',
                             '2026-10-21T10:00:00+05:30',
                             {'points': [{'Point': 'ATM', 'IV': None}]}, capture_source='Google Drive')

            saved = smile.load_latest_smile(path, 'NIFTY', '2026-10-27', 'current_cfg')

            self.assertEqual(saved['points'][0]['IV'], 16.0)
            self.assertEqual(saved['as_of'], '2026-10-20T10:00:00+05:30')

    def test_smile_atm_uses_nearest_strike_with_both_broker_ivs(self):
        import pandas as pd

        chain = pd.DataFrame([
            {'strike': 99.0, 'option_type': 'CE', 'iv': .18, 'delta': .4,
             'source': 'Upstox supplied IV'},
            {'strike': 101.0, 'option_type': 'CE', 'iv': .20, 'delta': .4,
             'source': 'Upstox supplied IV'},
            {'strike': 101.0, 'option_type': 'PE', 'iv': .30, 'delta': -.4,
             'source': 'Upstox supplied IV'},
        ])

        result = smile.smile_points(chain, [99.0, 101.0], 100.0)

        atm = next(point for point in result['points'] if point['Point'] == 'ATM')
        self.assertEqual(atm['IV'], 25.0)
        self.assertEqual(atm['Strikes'], '101.0')
        self.assertEqual(atm['Source'], 'Upstox supplied IV')


if __name__ == '__main__':
    unittest.main()
