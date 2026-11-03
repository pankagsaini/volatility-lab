"""No-network regression tests for shared request pacing."""
import multiprocessing as mp
from contextlib import closing
from pathlib import Path
import queue
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError
from upstox_data import UpstoxDataClient, UpstoxDataError

import upstox_rate_limit as pacing


def _spawned_waiter(directory, gate, events):
    """Top-level target remains importable with Windows multiprocessing spawn."""
    pacing.ROOT = Path(directory)
    pacing.DB = pacing.ROOT / 'request_pacing.sqlite'
    pacing.MIN_INTERVAL = 0.02
    events.put(('ready', None))
    if not gate.wait(5):
        return
    try:
        pacing.wait_for_slot()
        events.put(('finished', time.monotonic()))
    except Exception as exc:
        events.put(('error', type(exc).__name__))


class _FakeClock:
    def __init__(self, now):
        self.now = now
        self.sleeps = []

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration
        if len(self.sleeps) > 1:
            raise AssertionError('One request re-reserved its permit after waiting')


class _FakeConnection:
    def __init__(self, previous):
        self.previous = previous
        self.reservations = []
        self.closed = False

    def execute(self, sql, args=()):
        if sql.startswith('INSERT'):
            self.reservations.append(args[0])
            self.previous = args[0]
        return self

    def fetchone(self):
        return None if self.previous is None else (self.previous,)

    def commit(self):
        pass

    def close(self):
        self.closed = True


class PacingTests(unittest.TestCase):
    def _deterministic_call(self, previous, now):
        clock = _FakeClock(now)
        connection = _FakeConnection(previous)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pacing, 'ROOT', Path(directory)), \
                 patch.object(pacing.sqlite3, 'connect', return_value=connection) as connect, \
                 patch.object(pacing.time, 'time', side_effect=lambda: clock.now), \
                 patch.object(pacing.time, 'sleep', side_effect=clock.sleep):
                pacing.wait_for_slot()
            self.assertEqual(connect.call_count, 1)
        self.assertTrue(connection.closed)
        return clock, connection

    def test_waited_permit_returns_without_reserving_again(self):
        clock, connection = self._deterministic_call(previous=100.0, now=100.0)
        self.assertEqual(len(connection.reservations), 1)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], pacing.MIN_INTERVAL)
        self.assertAlmostEqual(connection.reservations[0], 100.0 + pacing.MIN_INTERVAL)

    def test_elapsed_slot_is_immediately_available(self):
        clock, connection = self._deterministic_call(previous=1.0, now=100.0)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(connection.reservations, [100.0])

    def test_first_request_is_immediately_available(self):
        clock, connection = self._deterministic_call(previous=None, now=100.0)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(connection.reservations, [100.0])

    def test_two_threads_reserve_distinct_slots_and_both_finish(self):
        real_connect = sqlite3.connect
        reservations = []
        errors = []
        finished = []
        trace_lock = threading.Lock()
        gate = threading.Event()

        def trace(sql):
            if sql.upper().startswith('INSERT'):
                match = re.search(r'VALUES\s*\(\s*1\s*,\s*([0-9.eE+-]+)\s*\)', sql, re.I)
                if match:
                    with trace_lock:
                        reservations.append(float(match.group(1)))

        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(trace)
            return connection

        def wait():
            gate.wait(2)
            try:
                pacing.wait_for_slot()
                finished.append(time.monotonic())
            except Exception as exc:
                errors.append(type(exc).__name__)

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'request_pacing.sqlite'
            with closing(real_connect(db)) as connection, connection:
                connection.execute('CREATE TABLE pacing(id INTEGER PRIMARY KEY, last_slot REAL NOT NULL)')
                initial_slot = time.time() + 0.05
                connection.execute('INSERT INTO pacing VALUES(1,?)', (initial_slot,))
            with patch.object(pacing, 'ROOT', Path(directory)), \
                 patch.object(pacing, 'DB', db), \
                 patch.object(pacing, 'MIN_INTERVAL', 0.02), \
                 patch.object(pacing.sqlite3, 'connect', side_effect=connect):
                threads = [threading.Thread(target=wait, daemon=True) for _ in range(2)]
                for worker in threads:
                    worker.start()
                gate.set()
                for worker in threads:
                    worker.join(2)
                self.assertFalse(any(worker.is_alive() for worker in threads), 'Pacing did not release both waiting requests')
            self.assertEqual(errors, [])
            self.assertEqual(len(finished), 2)
            self.assertEqual(len(reservations), 2)
            self.assertAlmostEqual(reservations[1] - reservations[0], 0.02, places=3)

    def test_spawned_processes_share_slots_and_both_finish(self):
        context = mp.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'request_pacing.sqlite'
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('CREATE TABLE pacing(id INTEGER PRIMARY KEY, last_slot REAL NOT NULL)')
                connection.execute('INSERT INTO pacing VALUES(1,0)')
            gate, events = context.Event(), context.Queue()
            workers = [context.Process(target=_spawned_waiter, args=(directory, gate, events), daemon=True) for _ in range(2)]
            try:
                for worker in workers:
                    worker.start()
                for _ in workers:
                    self.assertEqual(events.get(timeout=10)[0], 'ready')
                with closing(sqlite3.connect(db)) as connection, connection:
                    initial_slot = time.time() + 0.10
                    connection.execute('UPDATE pacing SET last_slot=?', (initial_slot,))
                gate.set()
                outcomes = [events.get(timeout=3) for _ in workers]
                self.assertEqual([kind for kind, _ in outcomes], ['finished', 'finished'])
                for worker in workers:
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(worker.exitcode, 0)
                with closing(sqlite3.connect(db)) as connection:
                    last_slot = connection.execute('SELECT last_slot FROM pacing WHERE id=1').fetchone()[0]
                self.assertAlmostEqual(last_slot - initial_slot, 0.04, places=3)
            except queue.Empty:
                self.fail('A waiting child process never received its request permit')
            finally:
                gate.set()
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                    if worker.pid is not None:
                        worker.join(3)
                events.close()
                events.join_thread()


class AttemptPacingTests(unittest.TestCase):
    def test_every_http_retry_gets_a_permit_before_opening(self):
        client = UpstoxDataClient('test-only-no-network')
        events = []
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"success","data":{"candles":[]}}'

        def open_response(*args, **kwargs):
            events.append('open')
            if events.count('open') == 1:
                raise URLError('simulated connection failure')
            return response

        with patch.object(client._opener, 'open', side_effect=open_response), \
             patch('upstox_data.time.sleep'):
            result = client._get('/v3/historical-candle/test/days/1/2025-01-02/2025-01-01',
                                 before_attempt=lambda: events.append('permit'))
        self.assertEqual(result, {'candles': []})
        self.assertEqual(events, ['permit', 'open', 'permit', 'open'])

    def test_failed_attempts_stop_after_three_and_all_are_paced(self):
        client = UpstoxDataClient('test-only-no-network')
        permit = MagicMock()
        with patch.object(client._opener, 'open', side_effect=URLError('simulated')) as open_request, \
             patch('upstox_data.time.sleep'):
            with self.assertRaises(UpstoxDataError):
                client._get('/v2/option/contract', before_attempt=permit)
        self.assertEqual(open_request.call_count, 3)
        self.assertEqual(permit.call_count, 3)


if __name__ == '__main__':
    unittest.main()
