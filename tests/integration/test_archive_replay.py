import copy
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from volatility_lab.ingestion.google_archive_sync import validate_report


def report(stamp='2026-11-13T10:30:00+05:30'):
    rows = []
    for strike in (24950, 25000, 25050):
        rows.append({
            'expiry': '2026-11-17', 'strike_price': strike, 'underlying_spot_price': 25000,
            'call_options': {'option_greeks': {'iv': 20, 'delta': .5}, 'market_data': {'ltp': 100}},
            'put_options': {'option_greeks': {'iv': 22, 'delta': -.5}, 'market_data': {'ltp': 95}},
        })
    return {
        'schema': 1, 'provider': 'Upstox', 'mode': 'snapshot', 'status': 'complete',
        'day': '2026-11-13', 'retrieved_at': stamp, 'nfo_session_validated': True,
        'catalogue': {'indices': {'NIFTY': {'futures': [], 'option_expiries': ['2026-11-17']}}},
        'records': [
            {'label': 'quotes', 'retrieved_at': stamp, 'data': {
                'NSE_INDEX|Nifty 50': {'instrument_token': 'NSE_INDEX|Nifty 50', 'timestamp': stamp,
                                     'last_price': 25000, 'net_change': 10},
            }},
            {'label': 'NIFTY|2026-11-17', 'retrieved_at': stamp, 'data': rows},
        ],
    }


def test_historical_replay_uses_dated_late_close_and_special_session():
    for stamp in ('2026-11-13T15:39:00+05:30', '2026-11-08T14:30:00+05:30'):
        saved = report(stamp)
        saved['day'] = stamp[:10]
        saved.pop('nfo_session_validated')
        validate_report(saved, historical=True)
    saved = report('2026-11-08T13:50:00+05:30')
    saved['day'] = '2026-11-08'
    saved.pop('nfo_session_validated')
    with pytest.raises(ValueError, match='session'):
        validate_report(saved, historical=True)


def test_seed_replays_missing_greeks_and_replaces_legacy_model_rows(tmp_path):
    archives = tmp_path / 'archives'
    archives.mkdir()
    store = tmp_path / 'data'
    first = report()
    later = report('2026-11-13T10:31:00+05:30')
    for row in later['records'][1]['data']:
        row['call_options'].pop('option_greeks')
        row['put_options'].pop('option_greeks')
    for minute, obj in [('103000', first), ('103100', later)]:
        (archives / f'2026-11-13_snapshot_{minute}_complete.json.gz').write_bytes(gzip.compress(json.dumps(obj).encode()))
    env = {**os.environ, 'VOL_LAB_DATA_DIR': str(store), 'VOL_LAB_ARCHIVE_DIR': str(archives),
           'VOL_LAB_OFFLINE': 'true'}
    command = [sys.executable, 'scripts/fetch_market_data.py', '--seed']
    run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout.splitlines()[0])['broker_rows_processed'] == 2
    database = store / 'monitor.sqlite'
    with sqlite3.connect(database) as con:
        rows = con.execute('SELECT as_of,payload FROM intraday_observations ORDER BY as_of').fetchall()
        assert len(rows) == 2
        assert json.loads(rows[0][1])['IV'] == 21
        assert pd.isna(json.loads(rows[1][1])['IV'])
        assert con.execute('SELECT iv FROM expiry_observations').fetchall() == [(21.0,)]
        old = json.loads(rows[0][1])
        old.update({'IV': 99, 'Source': 'Upstox L1 LTP + Black-76 Forward Model', 'Greeks source': 'Black-76'})
        con.execute('UPDATE intraday_observations SET payload=? WHERE as_of=?', (json.dumps(old), rows[0][0]))
        con.execute('UPDATE expiry_observations SET iv=99')
    rerun = subprocess.run(command, env=env, capture_output=True, text=True, timeout=45)
    assert rerun.returncode == 0, rerun.stderr
    with sqlite3.connect(database) as con:
        assert con.execute('SELECT COUNT(*) FROM intraday_observations').fetchone()[0] == 2
        assert con.execute('SELECT iv FROM expiry_observations').fetchall() == [(21.0,)]
        assert json.loads(con.execute('SELECT payload FROM intraday_observations ORDER BY as_of').fetchone()[0])['IV'] == 21


def test_old_capture_can_replay_without_relaxing_live_validation():
    old = report()
    old.pop('nfo_session_validated')
    with pytest.raises(ValueError, match='session'):
        validate_report(old)
    assert validate_report(old, historical=True) is old
    old['nfo_session_validated'] = False
    with pytest.raises(ValueError, match='session'):
        validate_report(old, historical=True)


def test_reject_malformed_capture_before_writes():
    invalid = report()
    invalid['records'][1]['data'][0]['call_options'] = []
    with pytest.raises(ValueError, match='leg'):
        validate_report(invalid)
    invalid = report()
    invalid['records'][1]['data'].append(copy.deepcopy(invalid['records'][1]['data'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        validate_report(invalid)


def test_missing_atm_side_does_not_select_a_different_strike():
    from volatility_lab.backtest.broker_options import chain_from_response
    from volatility_lab.services.market_table import atm_values

    rows = report()['records'][1]['data']
    rows[1]['put_options']['option_greeks'].pop('iv')
    chain = chain_from_response(rows, '2026-11-17')
    call, put, mean = atm_values(chain, [24950, 25000, 25050], 25000, use_black76=False)
    assert call == 20 and pd.isna(put) and pd.isna(mean)
    rows[1]['put_options']['option_greeks']['iv'] = 180
    chain = chain_from_response(rows, '2026-11-17')
    assert atm_values(chain, [24950, 25000, 25050], 25000, use_black76=False) == (20, 180, 100)


def test_replaying_older_quotes_does_not_regress_latest_file(tmp_path):
    from volatility_lab.backtest.trade_review import archive_quotes

    newer = {'provider': 'Upstox', 'valuation': '2026-11-13T10:31:00+05:30', 'spot': 25000}
    older = {**newer, 'valuation': '2026-11-13T10:30:00+05:30', 'spot': 24000}
    path = archive_quotes(tmp_path, 'NIFTY', '2026-11-17', newer)
    archive_quotes(tmp_path, 'NIFTY', '2026-11-17', older)
    assert json.loads(path.read_text()) == newer


def test_fresh_history_reader_is_read_only_and_model_iv_is_unavailable(tmp_path):
    from volatility_lab.services.option_context import recorded_context
    from volatility_lab.storage.snapshot_history import historical_fields

    path = tmp_path / 'missing.sqlite'
    assert recorded_context(path, 'NIFTY', 'cfg').empty
    assert not path.exists()
    old = {'IV': 99, 'Price': 25000, 'Capture source': 'Google Drive', 'Source': 'Black-76'}
    assert historical_fields(old) == {**old, 'IV': None}


def test_bundled_snapshot_matches_its_recorded_broker_chain():
    from volatility_lab import PACKAGE_ROOT

    data = PACKAGE_ROOT / 'data'
    snapshot = json.loads((data / 'expiry_latest.json').read_text())
    for row in snapshot['rows']:
        payload = json.loads((data / f"{row['Instrument'].lower()}_option_quotes_{row['Expiry']}.json").read_text())
        atm = next(item for item in payload['broker_option_chain'] if item['strike_price'] == row['ATM strike'])
        call, put = [atm[side]['option_greeks']['iv'] for side in ('call_options', 'put_options')]
        assert row['Source'] == 'Upstox supplied Greeks'
        assert row['Call IV'] == call and row['Put IV'] == put and row['IV'] == (call + put) / 2
        assert row['Archive SHA256'] == payload['archive_sha256']


def test_legacy_modeled_daily_row_is_quarantined_until_replayed(tmp_path):
    from volatility_lab.services.option_context import recorded_context

    path = tmp_path / 'history.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE expiry_observations(date,symbol,expiry,config,iv,spread,source)')
        con.execute('CREATE TABLE intraday_observations(symbol,expiry,config,as_of,payload)')
        con.execute('INSERT INTO expiry_observations VALUES(?,?,?,?,?,?,?)',
                    ('2026-11-13', 'NIFTY', '2026-11-17', 'cfg', 99, 85, 'Upstox supplied IV (Google Cloud)'))
        con.execute('INSERT INTO intraday_observations VALUES(?,?,?,?,?)',
                    ('NIFTY', '2026-11-17', 'cfg', '2026-11-13T10:30:00+05:30',
                     json.dumps({'IV': 99, 'Source': 'Upstox L1 LTP + Black-76 Forward Model'})))
    assert recorded_context(path, 'NIFTY', 'cfg').empty
