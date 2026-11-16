"""Daily publication must advance past old IV snapshots without using partial candles."""
from datetime import date, datetime
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
