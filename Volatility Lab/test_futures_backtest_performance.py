"""Futures speedups preserve archive conflicts and complete simulation outputs."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
from unittest.mock import patch
import gzip,hashlib,json,math,os
import pandas as pd
import futures_backtest as engine


class FuturesLoadCacheTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.location=patch.object(engine,'ROOT',self.root);self.location.start()
        engine.clear_load_cache()

    def tearDown(self):
        self.location.stop();engine.clear_load_cache();self.temp.cleanup()

    def write(self,name,rows,symbol='NIFTY',interval='day',provider='Upstox'):
        path=self.root/(name+'.json.gz')
        body={'provider':provider,'path':'/historical/'+interval+'/2024-10-31/2024-10-01',
              'meta':{'symbol':symbol,'contract':{'instrument_type':'FUT','expiry':'2024-11-28'}},
              'data':{'candles':rows}}
        with gzip.open(path,'wt',encoding='utf-8') as handle:json.dump(body,handle)
        return path

    def row(self,day,value=100):return [day+'T00:00:00+05:30',value,value+1,value-1,value,.0,0]

    def test_parameter_and_symbol_changes_reuse_daily_files_not_minute_payloads(self):
        self.write('nifty',[self.row('2024-10-01'),self.row('2024-10-02')])
        self.write('bank',[self.row('2024-10-01',200)],symbol='BANKNIFTY')
        self.write('minute',[self.row('2024-10-01',1000)],interval='1minute')
        with patch.object(engine.gzip,'open',wraps=gzip.open) as opened:
            first=engine.load('NIFTY','2024-10-01','2024-10-31')
            second=engine.load('NIFTY','2024-10-02','2024-10-31')
            bank=engine.load('BANKNIFTY','2024-10-01','2024-10-31')
            self.assertEqual(opened.call_count,3)
        self.assertEqual(len(first[0]),2);self.assertEqual(len(second[0]),1)
        self.assertEqual(next(iter(bank[0].values()))[0],200.)
        cached=engine._FILE_CACHE[str(self.root/'minute.json.gz')]
        self.assertIsNone(cached[1]);self.assertEqual(cached[2],0)

    def test_new_corrected_removed_files_and_caller_mutation_are_visible(self):
        path=self.write('a',[self.row('2024-10-01')])
        first,_=engine.load('NIFTY','2024-10-01','2024-10-31')
        first.clear()
        self.assertEqual(len(engine.load('NIFTY','2024-10-01','2024-10-31')[0]),1)
        old=path.stat().st_mtime_ns
        self.write('a',[self.row('2024-10-01',103)])
        os.utime(path,ns=(old+1_000_000,old+1_000_000))
        revised,_=engine.load('NIFTY','2024-10-01','2024-10-31')
        self.assertEqual(next(iter(revised.values()))[0],103.)
        added=self.write('b',[self.row('2024-10-02')])
        self.assertEqual(len(engine.load('NIFTY','2024-10-01','2024-10-31')[0]),2)
        added.unlink()
        self.assertEqual(len(engine.load('NIFTY','2024-10-01','2024-10-31')[0]),1)

    def test_conflicts_remain_excluded_after_cache_refresh(self):
        self.write('a',[self.row('2024-10-01')])
        path=self.write('b',[self.row('2024-10-01',101)])
        self.assertEqual(engine.load('NIFTY','2024-10-01','2024-10-31'),({},1))
        old=path.stat().st_mtime_ns
        self.write('b',[self.row('2024-10-01')]);os.utime(path,ns=(old+1_000_000,old+1_000_000))
        result,count=engine.load('NIFTY','2024-10-01','2024-10-31')
        self.assertEqual((len(result),count),(1,0))

    def test_invalid_values_outside_requested_dates_are_not_evaluated(self):
        self.write('a',[self.row('2024-10-01'),['2024-11-01T00:00:00+05:30','bad',1,1,1,1]])
        bars,count=engine.load('NIFTY','2024-10-01','2024-10-31')
        self.assertEqual((len(bars),count),(1,0))
        with self.assertRaises(ValueError):engine.load('NIFTY','2024-11-01','2024-11-01')

    def test_cache_has_row_and_file_bounds(self):
        for i in range(6):self.write(str(i),[self.row('2024-10-01'),self.row('2024-10-02')])
        with patch.object(engine,'_MAX_CACHE_ROWS',5),patch.object(engine,'_MAX_CACHE_FILES',2):
            engine.load('NIFTY','2024-10-01','2024-10-31')
            self.assertLessEqual(engine._FILE_CACHE_ROWS,5)
            self.assertLessEqual(len(engine._FILE_CACHE),2)


class FuturesSimulationParityTests(TestCase):
    def test_year_with_rolls_gaps_pending_exits_and_both_directions_matches_original(self):
        bars={}
        for i,stamp in enumerate(pd.bdate_range('2025-01-01','2025-12-31')):
            day=stamp.strftime('%Y-%m-%d')
            expiries=[(stamp.to_period('M')+offset).end_time.strftime('%Y-%m-%d') for offset in range(4)]
            for j,expiry in enumerate(expiries):
                price=10000+10*math.sin(i/8)+j*(35+12*math.sin(i/5))
                if i%29==0 and j==1:continue
                volume=0 if i%31==0 and j==0 else 100
                bars[day,expiry]=(price,price+2,price-2,price+math.sin(i/3),volume)
        # Captured from the pre-optimization engine; includes every trade charge,
        # skipped session and unfinished-position field, not merely aggregate P&L.
        golden={-1:'31c4a5009f2971c44ab1af2fadbd4c0f916229c5d19c1daeaae4f6fa0f4c5b4a',
                1:'a7b4f844e7c2525cffc9ae84698069c2d69a4ff88c1d77349acf676ecf9be2c3'}
        for side,expected in golden.items():
            trades,skips,position=engine.simulate(bars,pair=(0,2),entry=6.,target=6.66,
                quantity=75,near_side=side,slip_bps=1,max_days=12)
            raw=json.dumps([trades.to_dict('records'),skips.to_dict('records'),position],sort_keys=True,default=str).encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),expected)


if __name__=='__main__':main()
