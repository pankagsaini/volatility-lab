"""Regressions for automatic listings, completed HV, term forecasts and calendars."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,main
from unittest.mock import Mock,patch
import json
import numpy as np
import pandas as pd

from market_data import IST,MarketDataError
from upstox_adapter import UpstoxAdapter,catalogue_due
from expiry_forecast import term_structure_forecast
from expiry_data import listed_expiries
from refresh_policy import refresh_due
import corporate_event_calendar as events


def candle(day,close=102):
    return [f'{day}T00:00:00+05:30',100,105,98,close,0,0]


class DailyRefreshTests(TestCase):
    def adapter(self):
        client=object.__new__(UpstoxAdapter)
        client._bytoken={1:'NSE_INDEX|Nifty 50'}
        client.daily=Mock(return_value={'candles':[candle('2026-10-23')]})
        client.intraday_daily=Mock(return_value={'candles':[candle('2026-10-26')]})
        return client

    def test_today_enters_only_after_close_buffer(self):
        for minute,last,count in [(29,'2026-10-23',0),(30,'2026-10-26',1)]:
            with self.subTest(minute=minute):
                client=self.adapter()
                frame=client.daily_history(1,datetime(2026,10,26,16,minute,tzinfo=IST),'2026-10-22')
                self.assertEqual(str(frame.Date.iloc[-1].date()),last)
                self.assertEqual(client.intraday_daily.call_count,count)

    def test_published_historical_close_wins_and_avoids_extra_call(self):
        client=self.adapter()
        client.daily.return_value={'candles':[candle('2026-10-26',103),candle('2026-10-23')]}
        frame=client.daily_history(1,datetime(2026,10,26,17,tzinfo=IST),'2026-10-22')
        self.assertEqual(frame.Close.iloc[-1],103)
        client.intraday_daily.assert_not_called()

    def test_wrong_day_or_unavailable_current_bar_keeps_history(self):
        for payload in ({'candles':[candle('2026-10-23')]},{'candles':[candle('2026-10-27')]},{'candles':[]}):
            client=self.adapter();client.intraday_daily.return_value=payload
            frame=client.daily_history(1,datetime(2026,10,26,17,tzinfo=IST),'2026-10-22')
            self.assertEqual(len(frame),1)
            self.assertEqual(str(frame.Date.iloc[-1].date()),'2026-10-23')
        client=self.adapter();client.intraday_daily.side_effect=MarketDataError('Unavailable')
        self.assertEqual(len(client.daily_history(1,datetime(2026,10,26,17,tzinfo=IST),'2026-10-22')),1)

    def test_invalid_ohlc_is_not_admitted(self):
        client=self.adapter();client.intraday_daily.return_value={'candles':[candle('2026-10-26',200)]}
        frame=client.daily_history(1,datetime(2026,10,26,17,tzinfo=IST),'2026-10-22')
        self.assertEqual(str(frame.Date.iloc[-1].date()),'2026-10-23')

    def test_close_poll_retries_in_five_minutes(self):
        self.assertTrue(refresh_due(datetime(2026,10,26,16,30,tzinfo=IST),{},300))
        self.assertFalse(refresh_due(datetime(2026,10,26,16,30,tzinfo=IST),{},299))
        self.assertFalse(refresh_due(datetime(2026,10,25,16,30,tzinfo=IST),{},300))


class ListingTests(TestCase):
    def test_same_day_catalogue_expires_hourly_and_migrates_legacy_cache(self):
        now=datetime(2026,10,26,12,tzinfo=IST)
        data={'date':'2026-10-26','fetched_at':(now-timedelta(minutes=59)).isoformat()}
        self.assertFalse(catalogue_due(data,now))
        self.assertTrue(catalogue_due(data,now+timedelta(minutes=1)))
        self.assertTrue(catalogue_due({'date':'2026-10-26'},now))

    def test_newly_listed_weekly_is_selected_without_inventing_other_dates(self):
        rows=[]
        for symbol in ('NIFTY','BANKNIFTY'):
            for day in ('2026-10-27','2026-11-23','2026-12-29'):
                for kind in ('FUT','CE','PE'):rows.append((symbol,date.fromisoformat(day),kind))
            for day in ('2026-12-01','2026-12-08'):
                rows.append((symbol,date.fromisoformat(day),'CE'))
        master=pd.DataFrame(rows,columns=['name','expiry','instrument_type'])
        now=datetime(2026,10,26,17,tzinfo=IST)
        nifty=listed_expiries(master,'NIFTY',now)
        self.assertIn({'date':'2026-12-08','kind':'Weekly'},nifty)
        self.assertNotIn('2026-12-15',[r['date'] for r in nifty])
        self.assertEqual(len(listed_expiries(master,'BANKNIFTY',now)),3)


class ForecastTests(TestCase):
    def frame(self):
        days=pd.bdate_range('2025-09-01','2026-10-26')
        values=100*np.exp(np.cumsum(np.random.default_rng(7).normal(0,.01,len(days))))
        return pd.DataFrame({'Date':days,'Open':values,'High':values*1.01,'Low':values*.99,'Close':values})

    def fake_forecast(self,frame,expiry,anchor):
        return {'Training through':str(frame.Date.iloc[-1].date()),'As of':anchor.isoformat(),
                'GARCH expiry RV':20.,'EWMA expiry RV':19.,'GARCH status':'Ready'}

    def test_intraday_uses_previous_close_and_disables_gap_without_lookahead(self):
        with patch('expiry_forecast.expiry_forecast',side_effect=self.fake_forecast) as engine:
            result=term_structure_forecast(self.frame(),'2026-11-03','2026-10-26T12:00:00+05:30')
        self.assertFalse(result['Comparison ready'])
        self.assertEqual(result['Training through'],'2026-10-23')
        self.assertEqual(engine.call_args.args[2],datetime(2026,10,23,15,40,tzinfo=IST))

    def test_completed_close_allows_comparison(self):
        with patch('expiry_forecast.expiry_forecast',side_effect=self.fake_forecast):
            result=term_structure_forecast(self.frame(),'2026-11-03','2026-10-26T15:40:00+05:30')
        self.assertTrue(result['Comparison ready'])
        self.assertEqual(result['Training through'],'2026-10-26')

    def test_missing_several_closes_and_expiry_day_stay_withheld(self):
        frame=self.frame();frame=frame[frame.Date<pd.Timestamp('2026-10-23')]
        with self.assertRaisesRegex(ValueError,'missing'):
            term_structure_forecast(frame,'2026-11-03','2026-10-26T12:00:00+05:30')
        with self.assertRaisesRegex(ValueError,'expiry-day'):
            term_structure_forecast(self.frame(),'2026-10-26','2026-10-26T12:00:00+05:30')


class CalendarTests(TestCase):
    def row(self,name,category='Company',symbol='BANKNIFTY',purpose='Financial Results'):
        return {'Date':'2026-11-02','Event':name,'Category':category,'Instrument':symbol,
                'Symbol':'TESTBANK','Company':'Test Bank','Purpose':purpose,'Source':'official'}

    def test_bank_results_filter_excludes_routine_notices_and_other_indices(self):
        rows=[self.row('Results'),self.row('Dividend',purpose='Dividend'),
              self.row('AGM',purpose='Annual General Meeting'),self.row('Results',symbol='NIFTY')]
        filtered=events._bank_results(rows)
        self.assertEqual(len(filtered),1)
        self.assertEqual(filtered[0]['Category'],'Bank results')

    def test_nifty_never_requests_company_calendar(self):
        with TemporaryDirectory() as folder,patch.object(events,'_legacy_nse_events') as corporate,\
                patch.object(events,'_cached_source',return_value=([], 'cached',None)):
            rows,_=events.automatic_events(Path(folder),'NIFTY',date(2026,10,26),date(2026,12,29))
        corporate.assert_not_called();self.assertEqual(rows,[])

    def test_only_curated_macro_categories_are_kept(self):
        rows=[self.row('US CPI release','Global inflation'),self.row('India CPI release','India macro'),
              self.row('Local election','Election'),self.row('Company results')]
        self.assertEqual([r['Event'] for r in events._index_macro_events(rows)],['US CPI release','India CPI release'])

    def test_bls_parser_uses_release_cell_and_handles_abbreviations(self):
        payload=b'<table><tr><th>Reference month</th><th>Release Date</th><th>Time</th></tr><tr><td>October 2026</td><td>Nov. 10, 2026</td><td>08:30 AM</td></tr><tr><td>November 2026</td><td>Dec. 10, 2026</td><td>08:30 AM</td></tr></table>'
        with patch.object(events,'_request',return_value=payload):rows=events._bls_events()
        self.assertEqual([r['Date'] for r in rows],['2026-11-10','2026-12-10'])

    def test_failed_calendar_has_retry_cooldown_and_preserves_dates(self):
        now=datetime(2026,10,26,tzinfo=timezone.utc)
        cache={'sources':{'rbi':{'updated_at':(now-timedelta(days=1)).isoformat(),'events':[self.row('RBI','India policy')]}}}
        fetch=Mock(side_effect=TimeoutError())
        first=events._cached_source(cache,'rbi',fetch,now)
        second=events._cached_source(cache,'rbi',fetch,now+timedelta(minutes=10))
        self.assertEqual(first[0],second[0]);self.assertEqual(fetch.call_count,1)
        self.assertIsNotNone(second[2])


if __name__=='__main__':main()
