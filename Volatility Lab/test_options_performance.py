"""Cache isolation, point-in-time invariance, and nonblocking calendar tests."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase,main
from unittest.mock import Mock,patch
import json
import time
import pandas as pd
from pandas.testing import assert_frame_equal

import monitor_ui as ui
from options_context_cache import CalendarCache,saved_events


class ForecastCacheTests(TestCase):
    def setUp(self):ui._cached_term_forecast.clear()

    def forecast(self,frame,expiry,stamp):
        return {'Quote as of':stamp,'Training through':str(frame.Date.iloc[-1]),'value':20.}

    def test_quote_changes_reuse_daily_model_but_keep_exact_quote_time(self):
        raw=b'Date,Open,High,Low,Close\n2026-10-23,100,102,99,101\n'
        with patch.object(ui,'term_structure_forecast',side_effect=self.forecast) as engine:
            first=ui.term_forecast(raw,'2026-11-03','2026-10-26T10:15:00+05:30','test')
            second=ui.term_forecast(raw,'2026-11-03','2026-10-26T14:30:00+05:30','test')
        self.assertEqual(engine.call_count,1)
        self.assertEqual(first['Quote as of'],'2026-10-26T10:15:00+05:30')
        self.assertEqual(second['Quote as of'],'2026-10-26T14:30:00+05:30')

    def test_close_new_day_prices_expiry_or_engine_change_invalidates(self):
        raw=b'Date,Open,High,Low,Close\n2026-10-23,100,102,99,101\n'
        with patch.object(ui,'term_structure_forecast',side_effect=self.forecast) as engine:
            ui.term_forecast(raw,'2026-11-03','2026-10-26T15:39:59+05:30','test')
            ui.term_forecast(raw,'2026-11-03','2026-10-26T15:40:00+05:30','test')
            ui.term_forecast(raw,'2026-11-03','2026-10-27T10:00:00+05:30','test')
            ui.term_forecast(raw.replace(b',101',b',102'),'2026-11-03','2026-10-27T10:00:00+05:30','test')
            ui.term_forecast(raw,'2026-11-09','2026-10-27T10:00:00+05:30','test')
            ui.term_forecast(raw,'2026-11-09','2026-10-27T10:00:00+05:30','new-version')
        self.assertEqual(engine.call_count,6)

    def test_naive_timestamps_rejected(self):
        with self.assertRaisesRegex(ValueError,'timezone'):
            ui.term_forecast(b'','2026-11-03','2026-10-26T10:00:00','test')

    def test_cached_value_is_not_shared_with_callers(self):
        raw=b'Date,Open,High,Low,Close\n2026-10-23,100,102,99,101\n'
        with patch.object(ui,'term_structure_forecast',side_effect=self.forecast):
            first=ui.term_forecast(raw,'2026-11-03','2026-10-26T10:15:00+05:30','test')
            first['value']=99
            second=ui.term_forecast(raw,'2026-11-03','2026-10-26T10:16:00+05:30','test')
        self.assertEqual(second['value'],20.)


class ChartPricesTests(TestCase):
    def test_warm_history_reuses_one_hv_calculation_and_invalidates_on_revision(self):
        ui._cached_chart_prices.clear()
        frame=pd.DataFrame({'Date':pd.bdate_range('2026-08-01',periods=50),
                            'Open':range(100,150),'High':range(102,152),
                            'Low':range(99,149),'Close':range(101,151)})
        from market_table import historical_vol
        with patch.object(ui,'_cached_full_price_history',return_value=frame),\
                patch.object(ui,'historical_vol',wraps=historical_vol) as calculate:
            first=ui._cached_chart_prices('NIFTY',('2026-10-26',('original',)))
            second=ui._cached_chart_prices('NIFTY',('2026-10-26',('original',)))
            self.assertEqual(calculate.call_count,1)
            ui._cached_chart_prices('NIFTY',('2026-10-26',('updated',)))
            self.assertEqual(calculate.call_count,2)
        assert_frame_equal(first[1],historical_vol(frame))
        assert_frame_equal(first[1],second[1])


class CalendarCacheTests(TestCase):
    def row(self):
        return {'Date':'2026-11-02','Event':'RBI MPC decision','Category':'India policy','Source':'official'}

    def test_pending_fetch_returns_saved_data_without_waiting_or_duplicate_jobs(self):
        entered=Event();release=Event()
        def fetch(*args):
            entered.set();release.wait(2)
            return [self.row()],'Official calendars refreshed'
        service=CalendarCache(fetcher=Mock(side_effect=fetch))
        try:
            with TemporaryDirectory() as d,patch('options_context_cache.saved_events',return_value=[self.row()]):
                start=time.perf_counter()
                rows,status=service.get(d,'NIFTY',date(2026,10,26),date(2026,12,29))
                self.assertLess(time.perf_counter()-start,.3)
                self.assertEqual(rows,[self.row()]);self.assertIn('background',status)
                self.assertTrue(entered.wait(1))
                service.get(d,'NIFTY',date(2026,10,26),date(2026,12,29))
                self.assertEqual(service._fetch.call_count,1)
                release.set()
                future=next(iter(service._entries.values()))['future'];future.result(timeout=1)
                rows,status=service.get(d,'NIFTY',date(2026,10,26),date(2026,12,29))
                self.assertEqual(status,'Official calendars refreshed')
        finally:release.set();service.close()

    def test_failure_keeps_saved_dates_and_retries_after_cooldown(self):
        clock=[0.]
        fetch=Mock(side_effect=RuntimeError('private error should not appear'))
        service=CalendarCache(fetcher=fetch,clock=lambda:clock[0])
        try:
            with TemporaryDirectory() as d,patch('options_context_cache.saved_events',return_value=[self.row()]):
                args=(d,'NIFTY',date(2026,10,26),date(2026,12,29))
                service.get(*args)
                future=next(iter(service._entries.values()))['future']
                with self.assertRaises(RuntimeError):future.result(timeout=1)
                rows,status=service.get(*args)
                self.assertEqual(rows,[self.row()]);self.assertNotIn('private',status)
                self.assertEqual(fetch.call_count,1)
                clock[0]=301;service.get(*args)
                self.assertIsNotNone(next(iter(service._entries.values()))['future'])
        finally:service.close()

    def test_saved_dates_use_existing_filters_and_date_bounds(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            root.joinpath('macro_event_calendar_cache.json').write_text(json.dumps({'sources':{'rbi':{'events':[self.row()]},'fed':{'events':[]}}}))
            self.assertEqual(len(saved_events(root,'NIFTY',date(2026,10,26),date(2026,12,29))),1)
            self.assertEqual(saved_events(root,'NIFTY',date(2026,11,3),date(2026,12,29)),[])


if __name__=='__main__':main()
