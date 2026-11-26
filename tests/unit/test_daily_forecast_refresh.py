"""Daily publication must advance past old IV snapshots without using partial candles."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import refresh_forecasts as refresh, publish_live_state as publisher


def test_completed_session_respects_close_buffer_weekends_and_special_days():
    assert refresh.completed_session(datetime.fromisoformat('2026-11-13T16:29:00+05:30')) == date(2026, 11, 12)
    assert refresh.completed_session(datetime.fromisoformat('2026-11-13T16:30:00+05:30')) == date(2026, 11, 13)
    assert refresh.completed_session(datetime.fromisoformat('2026-11-15T18:00:00+05:30')) == date(2026, 11, 13)
    assert refresh.completed_session(datetime.fromisoformat('2026-11-08T17:00:00+05:30')) == date(2026, 11, 8)
    with pytest.raises(ValueError, match='timezone'):
        refresh.completed_session(datetime(2026, 11, 13))


def test_daily_merge_requires_all_completed_sessions_and_ignores_future():
    frame = pd.DataFrame([['2026-11-11', 100, 102, 99, 101]], columns=['Date','Open','High','Low','Close'])
    candles = [[f'2026-11-{day}T00:00:00+05:30', 100, 102, 99, 101, 0] for day in [12,13,16]]
    result = refresh.merge_candles(frame, candles, date(2026, 11, 13))
    assert list(result.Date.dt.day) == [11,12,13]
    with pytest.raises(ValueError, match='missing a completed session'):
        refresh.merge_candles(frame, candles[1:], date(2026, 11, 13))


def test_publisher_uses_new_close_even_when_iv_snapshot_is_older(monkeypatch):
    data = Path(__file__).parents[2] / 'volatility_lab/data'
    catalog = {'NIFTY':[{'expiry':'2026-11-17','ivCapturedAt':'2026-11-13T13:14:00+05:30'}]}
    received = []

    def model(frame, expiry, anchor):
        received.append(frame.Date.max())
        return {'Forecast as of': anchor.isoformat(), 'EGARCH status':'Ready', 'Model':'EGARCH',
                'Trading sessions':2, 'Training returns':750, 'Calendar status':'Sourced',
                'Simulations':20000, 'Simulation seed':1, 'EGARCH expiry RV':14.0}

    monkeypatch.setattr(publisher, 'term_structure_forecast', model)
    state = publisher.build('var marketCatalog = ' + json.dumps(catalog) + ';', data,
                            as_of=datetime.fromisoformat('2026-11-15T18:00:00+05:30'))
    assert received == ['2026-11-13']
    series = state['forecasts']['NIFTY']
    assert series['training_through'] == '2026-11-13'
    assert series['expiries'][0]['egarch'] == 14
    assert 'ewma' not in series['expiries'][0]


def test_snapshot_risk_uses_prior_dated_closes_only():
    from volatility_lab.ingestion.snapshot_risk import prior_close_metrics
    import math
    from statistics import stdev

    dates = pd.date_range('2026-10-01', periods=40)
    frame = pd.DataFrame({'Date': dates.strftime('%Y-%m-%d'), 'Close': [100 + i for i in range(40)]})
    session = dates[-1].date()
    metrics = prior_close_metrics(frame, session)
    returns = [math.log((100 + i) / (99 + i)) for i in range(9, 39)]
    assert metrics['as_of'] == str(dates[-2].date())
    assert metrics['hv30'] == pytest.approx(stdev(returns) * 100 * math.sqrt(245))
    frame.loc[39, 'Close'] = 9999
    assert prior_close_metrics(frame, session) == metrics
    assert prior_close_metrics(frame.iloc[:10], session) == {}


@pytest.mark.parametrize('now', ['2026-11-25T17:30:00+05:30', '2026-11-26T01:15:00+05:30'])
def test_daily_refresh_uses_only_the_completed_broker_candle(tmp_path, monkeypatch, now):
    data_dir = Path(__file__).parents[2] / 'volatility_lab/data'
    latest = pd.read_csv(data_dir / 'nifty_one_year.csv').Date.max()
    expected = date(2026, 11, 25)
    prior_sessions = refresh.sessions(pd.Timestamp(latest).date() + timedelta(days=1), expected)
    assert prior_sessions[-1] == expected

    as_of = datetime.fromisoformat(now)
    close_date = '2026-11-25'
    calls = []

    def fake_fetch(url, params):
        calls.append(url)
        if '/intraday/' in url:
            return {'status': 'success', 'data': {'candles': [[f'{close_date}T00:00:00+05:30', 100, 102, 99, 101, 0]]}}
        candles = [[f'{day.isoformat()}T00:00:00+05:30', 100, 102, 99, 101, 0] for day in prior_sessions[:-1]]
        return {'status': 'success', 'data': {'candles': candles}}

    monkeypatch.setattr(refresh, 'fetch', fake_fetch)
    refresh.refresh_daily_candles(tmp_path, expected, as_of)
    assert sum('/intraday/' in url for url in calls) == 2
    assert pd.read_csv(tmp_path / 'nifty_one_year.csv').Date.iloc[-1] == '2026-11-25'

    close_date = '2026-11-26'
    with pytest.raises(ValueError, match='missing a completed session'):
        refresh.refresh_daily_candles(tmp_path, expected, as_of)

    with pytest.raises(ValueError, match='unfinished'):
        refresh.refresh_daily_candles(tmp_path, expected, datetime.fromisoformat('2026-11-25T11:00:00+05:30'))

    monkeypatch.setattr(refresh, 'fetch', lambda url, params: {'status': 'success', 'data': {'candles': []}} if '/intraday/' in url else fake_fetch(url, params))
    with pytest.raises(ValueError, match='missing a completed session'):
        refresh.refresh_daily_candles(tmp_path, expected, as_of)
