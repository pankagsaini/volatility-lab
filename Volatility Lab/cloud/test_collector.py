import unittest
from datetime import datetime
from collector import Archive, CHUNK, IST, collect, packed

class Table:
    def __init__(self):
        self.items = {}
        self.fail = False
    def get_item(self, Key, **kw):
        item = self.items.get((Key['pk'], Key['sk']))
        return {'Item': item} if item else {}
    def put_item(self, Item):
        if self.fail and Item['sk'].startswith('blob|'):
            raise RuntimeError('simulated write failure')
        self.items[(Item['pk'], Item['sk'])] = Item

class Context:
    def get_remaining_time_in_millis(self): return 200000

class Broker:
    def __init__(self, fail=False): self.fail=fail; self.calls=0
    def get(self, path, params=None):
        self.calls += 1
        if self.fail and path.endswith('/chain'): raise RuntimeError('Broker HTTP 503')
        return {'candles': [['2026-10-15T00:00:00+05:30', 1, 2, 1, 2, 3]]} if '/historical-candle/' in path else [{'iv': 12.34}]

class Tests(unittest.TestCase):
    def archive(self):
        archive = Archive(Table())
        archive.save('2026-10-16', 'catalogue', {'NIFTY': {'futures': [], 'option_expiries': ['2026-10-20']}})
        return archive
    def test_roundtrip_and_integrity(self):
        import random
        rng = random.Random(42)
        value = {'rows': [rng.getrandbits(64) for _ in range(40000)]}
        self.assertGreater(len(packed(value)), CHUNK)
        archive = self.archive(); archive.save('d', 'k', value)
        self.assertEqual(archive.read('d', 'k'), value)
        manifest = archive.table.items[('d', 'k')]
        archive.table.items[('d', f"blob|{manifest['sha256']}|0000")]['payload'] = b'broken'
        with self.assertRaisesRegex(ValueError, 'integrity'): archive.read('d', 'k')
    def test_failed_write_keeps_previous(self):
        archive = self.archive(); archive.save('d', 'k', {'old': True}); archive.table.fail=True
        with self.assertRaises(RuntimeError): archive.save('d', 'k', {'new': True})
        self.assertEqual(archive.read('d', 'k'), {'old': True})
    def test_complete_slot_not_downloaded_twice(self):
        archive=self.archive(); broker=Broker(); now=datetime(2026,10,16,10,1,tzinfo=IST)
        self.assertEqual(collect(broker,archive,now,Context())['status'], 'complete')
        calls=broker.calls
        self.assertEqual(collect(broker,archive,now,Context())['status'], 'already_saved')
        self.assertEqual(broker.calls,calls)
    def test_partial_is_retryable(self):
        archive=self.archive(); now=datetime(2026,10,16,10,1,tzinfo=IST)
        self.assertEqual(collect(Broker(True),archive,now,Context())['status'], 'partial')
        self.assertEqual(collect(Broker(),archive,now,Context())['status'], 'complete')
    def test_eod_candle_not_published_yet(self):
        result=collect(Broker(),self.archive(),datetime(2026,10,16,17,tzinfo=IST),Context(),'eod')
        self.assertEqual(result['status'],'partial'); self.assertTrue(result['pending'])

if __name__ == '__main__': unittest.main()
