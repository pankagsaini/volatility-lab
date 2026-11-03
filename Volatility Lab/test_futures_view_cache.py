"""Futures caches must admit new/corrected candles and ignore quote-only changes."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from futures_rolling import setup,VERSION
from futures_view_cache import database_revision,rolling_revision,_rolling_content
from futures_ui import saved_rolling_chart,saved_dated_chart,saved_intraday_chart,saved_expiry_pairs


class FuturesCacheTests(unittest.TestCase):
    def setUp(self):
        _rolling_content.cache_clear()
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)/'futures'
        self.root.mkdir()
        self.con=setup(self.root)
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('CREATE TABLE continuous_provenance(symbol TEXT,rank INTEGER,day TEXT,expiry TEXT,provider TEXT,PRIMARY KEY(symbol,rank,day))')
        for symbol in ('NIFTY','BANKNIFTY'):
            for day in ('2026-10-22','2026-10-23'):
                for rank,expiry in enumerate(('2026-10-27','2026-11-23','2026-12-29'),1):
                    self.con.execute('INSERT INTO continuous VALUES (?,?,?,?,?,?,?,?,?)',(symbol,rank,day,100+rank,1000,0,0,expiry,'old'))
                    self.con.execute('INSERT INTO continuous_provenance VALUES (?,?,?,?,?)',(symbol,rank,day,expiry,'Broker'))
                    self.con.execute('INSERT INTO candles VALUES (?,?,?,?,?,?)',(symbol,expiry,day,100+rank,1000,0))
        self.con.commit()
        self.front,self.back='2026-10-27','2026-11-23'

    def tearDown(self):
        _rolling_content.cache_clear()
        self.con.close();self.tmp.cleanup()

    def revision(self):return (VERSION,rolling_revision(self.root,'NIFTY'))

    def add_snapshot(self,stamp,spread=-1):
        payload={'pairs':[{'Front expiry':self.front,'Back expiry':self.back,'As of':stamp,
                           'Spread':spread,'Source':'Broker','Annualized carry %':2}]}
        self.con.execute('INSERT INTO snapshots VALUES (?,?,?)',('NIFTY',stamp,json.dumps(payload)))
        self.con.commit()

    def test_quote_refresh_and_acquisition_metadata_do_not_rebuild_rolling(self):
        before=self.revision()
        self.add_snapshot('2026-10-23T15:30:00+05:30')
        self.con.execute("UPDATE continuous SET fetched='new' WHERE symbol='NIFTY'")
        self.con.commit()
        (self.root/'NIFTY_rolling_audit.json').write_text('{"fetched":"new"}')
        self.assertEqual(before,self.revision())

    def test_interior_price_and_provenance_corrections_invalidate(self):
        before=self.revision()
        self.con.execute("UPDATE continuous SET close=100 WHERE symbol='NIFTY' AND rank=1 AND day='2026-10-22'")
        self.con.commit()
        corrected=self.revision();self.assertNotEqual(before,corrected)
        self.con.execute("UPDATE continuous_provenance SET provider='Corrected broker source' WHERE symbol='NIFTY' AND rank=1 AND day='2026-10-22'")
        self.con.commit()
        self.assertNotEqual(corrected,self.revision())

    def test_other_symbol_and_calendar_observation_times_do_not_invalidate(self):
        import time
        calendar=self.root.parent/'listed_monthly_expiries.json'
        calendar.write_text(json.dumps({'NIFTY':{'2027-01':{'expiry':'2027-01-26','observed':'old'}}}))
        before=self.revision()
        calendar.write_text(json.dumps({'NIFTY':{'2027-01':{'expiry':'2027-01-26','observed':'new'}}}))
        self.con.execute("UPDATE continuous SET close=90 WHERE symbol='BANKNIFTY'");self.con.commit()
        self.assertEqual(before,self.revision())
        time.sleep(0.02)
        calendar.write_text(json.dumps({'NIFTY':{'2027-01':{'expiry':'2027-01-25','observed':'new'}}}))
        self.assertNotEqual(before,self.revision())

    def test_rolling_cache_returns_corrected_prices_and_isolates_mutation(self):
        first=saved_rolling_chart(self.root,'NIFTY',self.revision())
        self.assertEqual(first.Spread.iloc[0],-1)
        first.loc[first.index[0],'Spread']=999
        same=saved_rolling_chart(self.root,'NIFTY',self.revision())
        self.assertEqual(same.Spread.iloc[0],-1)
        self.con.execute("UPDATE continuous SET close=99 WHERE symbol='NIFTY' AND rank=1 AND day='2026-10-22'")
        self.con.commit()
        corrected=saved_rolling_chart(self.root,'NIFTY',self.revision())
        self.assertEqual(corrected.Spread.iloc[0],-3)

    def test_committed_wal_candle_is_visible_without_audit_change(self):
        before=database_revision(self.root)
        first=saved_dated_chart(self.root,'NIFTY',self.front,self.back,before)
        self.con.execute('INSERT INTO candles VALUES (?,?,?,?,?,?)',('NIFTY',self.front,'2026-10-26',104,1000,0))
        self.con.execute('INSERT INTO candles VALUES (?,?,?,?,?,?)',('NIFTY',self.back,'2026-10-26',108,1000,0))
        self.con.commit()
        after=database_revision(self.root);self.assertNotEqual(before,after)
        second=saved_dated_chart(self.root,'NIFTY',self.front,self.back,after)
        self.assertEqual(len(second),len(first)+1)
        self.assertEqual(second.Spread.iloc[-1],-4)

    def test_new_snapshots_refresh_intraday_and_archived_pairs(self):
        empty=saved_intraday_chart(self.root,'NIFTY',self.front,self.back,database_revision(self.root))
        self.assertTrue(empty.empty)
        self.assertEqual(saved_expiry_pairs(self.root,'NIFTY',database_revision(self.root)),[])
        self.add_snapshot('2026-10-23T15:30:00+05:30',-2)
        fresh=saved_intraday_chart(self.root,'NIFTY',self.front,self.back,database_revision(self.root))
        self.assertEqual(fresh.Spread.tolist(),[-2])
        self.assertEqual(saved_expiry_pairs(self.root,'NIFTY',database_revision(self.root)),[(self.front,self.back)])


if __name__=='__main__':unittest.main()
