"""Regressions for numerical, provenance and point-in-time audit findings."""
from datetime import date
from unittest.mock import patch
import json
import math
import sqlite3
import warnings

import numpy as np
import pandas as pd
import pytest

from volatility_lab.pricing import option_math as om, smile, pricing_policy
from volatility_lab.core import market_calendar as calendar, vol_lib
from volatility_lab.forecasting import expiry_forecast as ef, forecast
from volatility_lab.research import relative_value
from volatility_lab.services import market_table
from scripts import audit_derived_prices as audit


def ohlc(scale=1.0):
    dates = pd.bdate_range(end='2026-11-13', periods=230)
    returns = np.random.default_rng(42).normal(0, .01 * scale, len(dates))
    close = 100 * np.exp(np.cumsum(returns))
    close *= 100 / close[-1]  # Two different paths with identical final close.
    return pd.DataFrame({'Date': dates, 'Open': close, 'High': close * 1.01,
                         'Low': close * .99, 'Close': close})


def chain():
    observed = '2026-11-13T10:00:00+05:30'
    rows = []
    for strike, forward in [(90, 102), (100, 100)]:
        for kind in ('CE', 'PE'):
            price = om.black76_greeks(forward, strike, .2, .06, .3, kind)['price']
            rows.append({'strike': strike, 'option_type': kind, 'expiry': '2026-12-01',
                         'bid': price * .999, 'ask': price * 1.001,
                         'timestamp': observed, 'iv': .9, 'delta': .5 if kind == 'CE' else -.5})
    return pd.DataFrame(rows), observed


def test_forecast_training_cache_cannot_alias_paths_or_hide_mutation():
    first, second = ohlc(), ohlc(2)
    a, _ = ef._prepare_one_year_slice(first, date(2026, 11, 13))
    b, _ = ef._prepare_one_year_slice(second, date(2026, 11, 13))
    assert not a.Close.equals(b.Close)
    with patch.object(ef, 'fit_garch', side_effect=ValueError('Fit withheld')):
        assert ef._get_base_expiry_models(b)[0] > 1.9 * ef._get_base_expiry_models(a)[0]
    a.loc[a.index[-1], 'Low'] = 999
    with pytest.raises(ValueError, match='High/Low'):
        vol_lib.validate_ohlc(a)


def test_garch_projects_all_265_sessions():
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        frame = ohlc()
        # Deterministic stationary GARCH sample avoids boundary estimates of IID noise.
        rng = np.random.default_rng(17)
        variance, returns = 1.0, []
        for _ in range(len(frame)):
            shock = math.sqrt(variance) * rng.normal()
            returns.append(shock / 100)
            variance = .1 + .15 * shock ** 2 + .7 * variance
        frame['Close'] = 100 * np.exp(np.cumsum(returns))
        frame['Open'] = frame.Close
        frame['High'] = frame.Close * 1.01
        frame['Low'] = frame.Close * .99
        _, diagnostics, path, _ = forecast.fit_garch(frame, (265,), min_observations=200)
    assert diagnostics['converged']
    assert len(path) == 265
    assert np.isfinite(path['Daily conditional vol %']).all()


def test_session_windows_and_expiring_contract_close():
    assert ef.sessions('2025-10-21', '2025-10-21') == [date(2025, 10, 21)]
    assert ef.sessions('2026-02-01', '2026-02-01') == [date(2026, 2, 1)]
    opened, closed = calendar.session_window('2026-11-08')
    assert opened.strftime('%H:%M') == '14:20'
    assert closed.strftime('%H:%M') == '15:40'
    assert calendar.expiry_close('2026-11-17').strftime('%H:%M') == '15:40'
    assert calendar.expiry_close('2026-12-08').strftime('%H:%M') == '15:30'
    assert calendar.session_window('2026-12-08')[1].strftime('%H:%M') == '15:45'
    assert calendar.session_window('2026-11-10') is None
    assert ef.completed_cutoff(pd.Timestamp('2026-11-08T15:35:00+05:30')) == date(2026, 11, 7)


def test_near_expiry_atm_black76_gamma_and_theta_remain_nonzero():
    years, sigma = 1e-24, .2
    actual = om.black76_greeks(100, 100, years, .06, sigma, 'CE')
    pdf = 1 / math.sqrt(2 * math.pi)
    assert actual['gamma'] == pytest.approx(pdf / (100 * sigma * math.sqrt(years)))
    assert actual['theta_daily'] == pytest.approx(-100 * pdf * sigma / (2 * math.sqrt(years) * 365))
    assert actual['price'] > 0


@pytest.mark.parametrize('rate', [float('nan'), float('inf'), -10000])
def test_invalid_numerical_lanes_do_not_warn(rate):
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        assert math.isnan(om.solve_iv(10, 100, 100, 1, rate, 'CE'))
        assert math.isnan(om.solve_black76_iv(10, 100, 100, 1, rate, 'CE'))
        result = om.bsm_greeks_vec(100, 100, 1, rate=rate, vol=.2)
        assert math.isnan(float(result['price']))
        result = om.black76_greeks(100, 100, 1, rate, 0, 'CE')
        assert math.isnan(result['price'])
    expired = om.bsm_greeks(105, 100, 0, -10000, 0, 'CE')
    assert float(expired['price']) == 5


def test_chain_uses_atm_tight_books_and_mixed_epoch_units():
    rows, observed = chain()
    rows['timestamp'] = rows['timestamp'].astype(object)
    rows.loc[2, 'timestamp'] = int(pd.Timestamp(observed).timestamp())
    rows.loc[3, 'timestamp'] = int(pd.Timestamp(observed).timestamp() * 1000)
    result = om.black76_chain_greeks(rows, spot=100, years=.2, rate=.06, as_of=observed)
    np.testing.assert_allclose(result.forward, 100)
    np.testing.assert_allclose(result.iv_b76.iloc[2:], .3, atol=1e-8)
    rows.loc[2, 'ask'] *= 2
    assert om.black76_chain_greeks(rows, spot=100, years=.2, rate=.06, as_of=observed).iv_b76.isna().all()


def test_model_smile_and_atm_never_fallback_to_broker_iv_or_stale_ltp(tmp_path):
    rows, observed = chain()
    result = smile.black76_smile_points(rows, spot=100, forward=100, years=.2, rate=.06,
                                        as_of='2026-11-13T12:00:00+05:30')
    assert all(point['IV'] is None for point in result['points'])
    smile.save_smile(tmp_path / 'smiles.sqlite', 'NIFTY', '2026-12-01', 'cfg', observed,
                     result, capture_source='Google Drive')
    assert not (tmp_path / 'smiles.sqlite').exists()
    assert all(math.isnan(v) for v in market_table.atm_values(
        rows, [100], 100, use_black76=True, forward=100, years=.2, rate=.06))


def test_relative_value_ranks_only_strictly_prior_same_index():
    history = pd.DataFrame([{'Instrument': 'NIFTY', 'As of': f'2026-11-{day:02d}T15:40:00+05:30',
                             'Expiry': f'2026-12-{day:02d}', 'DTE': 10, 'IV': float(day)}
                            for day in range(1, 14)])
    current = {'Instrument': 'NIFTY', 'As of': '2026-11-07T15:40:00+05:30', 'IV': 7, 'DTE': 10}
    history = pd.concat([history, history.assign(Instrument='BANKNIFTY', IV=1000)])
    stats = relative_value.maturity_stats(history, current, min_expiries=6)
    assert stats['count'] == 6
    assert stats['percentile'] == 100
    fallback = relative_value.maturity_stats(history, current, min_expiries=12)
    assert fallback['Percentile'] == 100


def test_hv_alignment_cannot_fall_forward_to_future_close():
    frame = ohlc()
    result = market_table.aligned_spread(20, '2025-11-13T10:00:00+05:30', 'Upstox', frame)
    assert math.isnan(result['HV30'])
    result = market_table.aligned_spread(20, '2026-11-13T15:35:00+05:30', 'Upstox', frame)
    assert result['RV date'] == '2026-11-12'
    result = market_table.aligned_spread(20, None, 'Upstox', frame)
    assert math.isnan(result['HV30'])


def test_benchmark_reuses_canonical_math_and_preserves_elapsed_expiry():
    assert audit.black76_greeks(F=100, K=100, T=.1, r=.06, sigma=.2) == om.black76_greeks(100, 100, .1, .06, .2)
    assert float(audit.bsm_greeks(105, 100, 0, .06, 0, 'CE', q=.02)['price']) == 5
    rows = [{'strike': 100, 'call_ltp': 12, 'put_ltp': 8}]
    result = audit.run_benchmark_audit(rows, 100, r=.06, q=.01, dte_days=100)
    assert result['rows'][0]['synthetic_future'] == round(100 + math.exp(.06 * 100 / 365) * 4, 2)
    elapsed = audit.run_benchmark_audit(rows, 100, r=.06, q=.01, dte_days=0)
    assert elapsed['rows'][0]['lab_black76_call_iv'] is None
    assert elapsed['rows'][0]['lab_bsm_call_iv'] is None


def test_malformed_books_and_vector_call_flags():
    for value in (123, {'depth': 'invalid'}, {'depth': {'buy': [2], 'sell': [3]}}):
        assert pricing_policy.book(value, '2026-11-13T10:00:00+05:30') is None
    actual = om.black76_greeks_vec(100, [100, 100], .2, .06, .2, is_call=[True, False])
    assert actual['delta'][0] > 0 > actual['delta'][1]


def test_quote_validation_escape_hatch_is_rejected():
    rows, observed = chain()
    with pytest.raises(ValueError, match='two-sided books'):
        om.black76_chain_greeks(rows, forward=100, years=.2, rate=.06, allow_unvalidated_quotes=True)


def test_independent_audit_uses_actual_expiry_close_and_recorded_inputs():
    from volatility_lab.storage import data_accuracy
    observed = '2026-11-13T15:40:00+05:30'
    payload = {'spot': 100, 'valuation': observed, 'broker_option_chain': [
        {'expiry': '2026-11-17', 'strike_price': 100}], 'spot_quote': {'timestamp': observed},
        'pricing_inputs': {
            'rate_source': {'model_rate': .06, 'checked_at': observed, 'sha256': 'test-rate',
                            'source': 'https://www.rbi.org.in/'},
            'dividend_source': {'model_yield': .01, 'as_of': '2026-10-30', 'checked_at': observed,
                                'sha256': 'test-dividend',
                                'source': 'https://www.niftyindices.com/Factsheet/ind_nifty50.pdf'}}}
    with patch.object(data_accuracy, 'synthetic_future', return_value={}) as calculate:
        data_accuracy.check_row({'Expiry': '2026-11-17', 'Instrument': 'NIFTY'}, payload, None)
    assert calculate.call_args.kwargs['years'] == pytest.approx(4 / 365)
    assert calculate.call_args.kwargs['rate'] == .06
    assert calculate.call_args.kwargs['dividend'] == .01


def test_archived_straddle_preserves_missing_captures_and_fixed_strike():
    from volatility_lab.research import restored_lab as restored
    source = 'Upstox supplied IV (Google Cloud)'
    rows = pd.DataFrame([
        {'Source': source, 'Price': 100, 'IV': 20, 'ATM strike': 100, 'ATM call LTP': 4, 'ATM put LTP': 3},
        {'Source': source, 'Price': None, 'IV': None, 'ATM strike': 100, 'ATM call LTP': 4, 'ATM put LTP': None},
        {'Source': source, 'Price': 110, 'IV': 21, 'ATM strike': 110, 'Straddle midpoint': 8},
        {'Source': 'Black-76 model', 'Price': 100, 'IV': 999, 'ATM strike': 100, 'Straddle midpoint': 999},
    ])
    actual = restored.captured_session(rows, strike=100)
    assert actual['Captured straddle'].iloc[0] == 7
    assert actual['Captured straddle'].iloc[1:].isna().all()
    assert math.isnan(actual.Price.iloc[1])
    assert math.isnan(actual.IV.iloc[1])
    assert math.isnan(actual.IV.iloc[3])
    assert rows.IV.iloc[3] == 999  # Read-time quarantine leaves original observations intact.


def test_straddle_requires_explicit_inputs_and_sums_both_leg_gamma():
    from volatility_lab.research import restored_lab as restored
    assert restored.compute_straddle_greeks(100, 100, 2, 20) is None
    assert restored.compute_straddle_greeks(100, 100, 2, 20, rate=.06) is None
    result = restored.compute_straddle_greeks(100, 100, .01, .5, rate=.06, forward=100)
    call = om.black76_greeks(100, 100, .01 / 365, .06, .005, 'CE')
    put = om.black76_greeks(100, 100, .01 / 365, .06, .005, 'PE')
    assert result['straddle'] == pytest.approx(call['price'] + put['price'])
    assert result['gamma'] == pytest.approx(call['gamma'] + put['gamma'])
    assert restored.compute_straddle_greeks(100, 100, -1, 20, rate=.06, forward=101) is None
    expired = restored.compute_straddle_greeks(100, 100, 0, 20, rate=.06, forward=101)
    assert expired['straddle'] == 1
    assert expired['theta_day'] == 0
    missing, _ = restored._compute_straddle_series_vectorized([100], [100], [2], [20])
    assert np.isnan(missing).all()


def test_intraday_hv_alignment_recomputes_when_session_completes(tmp_path):
    from volatility_lab.storage import snapshot_history
    path = tmp_path / 'monitor.sqlite'
    ohlc().to_csv(tmp_path / 'nifty_one_year.csv', index=False)
    for stamp in ('2026-11-13T15:35:00+05:30', '2026-11-13T15:40:00+05:30'):
        snapshot_history.save(path, 'cfg', {'Instrument': 'NIFTY', 'Expiry': '2026-11-17',
            'As of': stamp, 'IV': 20, 'Source': 'Upstox supplied IV (Google Cloud)', 'Capture source': 'Google Drive'})
    result = snapshot_history.load(path, 'NIFTY', '2026-11-17', 'cfg')
    assert result['RV date'].tolist() == ['2026-11-12', '2026-11-13']
    assert result.HV30.iloc[0] != result.HV30.iloc[1]


def test_recorded_context_chooses_front_expiry_independently_each_day(tmp_path):
    from volatility_lab.services.option_context import recorded_context
    path = tmp_path / 'monitor.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE expiry_observations (symbol TEXT,date TEXT,expiry TEXT,iv REAL,spread REAL,source TEXT,config TEXT)')
        rows = [('2026-11-11', '2026-11-11', 20),
                ('2026-11-12', '2026-11-12', 19),
                ('2026-11-12', '2026-11-17', 18)]
        con.executemany('INSERT INTO expiry_observations VALUES (?,?,?,?,?,?,?)',
            [('NIFTY', day, expiry, iv, 3, 'Upstox supplied IV (Google Cloud)', 'cfg') for day, expiry, iv in rows])
    result = recorded_context(path, 'NIFTY', 'cfg')
    assert result.Date.dt.strftime('%Y-%m-%d').tolist() == ['2026-11-11', '2026-11-12']
    assert result.Expiry.tolist() == ['2026-11-11', '2026-11-17']
    assert result.IV.tolist() == [20, 18]


def test_unavailable_pricing_inputs_do_not_block_broker_capture(tmp_path):
    from volatility_lab.storage import input_provenance
    (tmp_path / 'risk_free_rate.json').write_text(json.dumps({'model_rate': .06}))
    (tmp_path / 'dividend_yields.json').write_text(json.dumps({'NIFTY': {'model_yield': .01}}))
    with patch.object(input_provenance, 'ROOT', tmp_path):
        result = input_provenance.snapshot(None, None, 'NIFTY')
        assert result['rate'] is None and result['dividend'] is None
        assert len(result['warnings']) == 2
        json.dumps(result, allow_nan=False)


def test_dividend_cache_rejects_nonfinite_and_future_checked_inputs():
    from volatility_lab.pricing import pricing_inputs
    today = pd.Timestamp.now(tz='Asia/Kolkata')
    valid = {'as_of': today.date().isoformat(), 'checked_at': today.isoformat(),
             'model_yield': .0142, 'sha256': 'test',
             'source': 'https://www.niftyindices.com/Factsheet/ind_nifty50.pdf'}
    for updates in ({'model_yield': float('inf')}, {'checked_at': (today + pd.Timedelta(days=1)).isoformat()}):
        payload = {'NIFTY': {**valid, **updates}}
        with patch.object(pricing_inputs, '_read', return_value=payload), patch.object(pricing_inputs, 'ensure_refresh'):
            assert pricing_inputs.get_yields()['NIFTY']['model_yield'] is None
