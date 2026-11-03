import gzip,json,tempfile,unittest
from pathlib import Path
from candle_validation import validate_contract

class CandleValidationTests(unittest.TestCase):
 def test_duplicates_conflicts_invalid_and_zero_volume_are_separate(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);meta={'symbol':'NIFTY','key':'option','start':'2024-10-01','end':'2024-10-01','contract':{'expiry':'2024-10-01'}}
   first=['2024-10-01T09:15:00+05:30',10,12,9,11,0,1]
   rows=[first,first,['2024-10-01T09:15:00+05:30',10,13,9,12,1,1],
         ['2024-10-01T09:16:00+05:30',10,9,8,11,1,1],
         ['2024-10-01T09:17:00',10,12,9,11,1,1],
         ['2024-10-02T09:15:00+05:30',10,12,9,11,1,1]]
   (root/'x.gz').write_bytes(gzip.compress(json.dumps({'meta':meta,'data':{'candles':rows}}).encode()))
   r=validate_contract(root,[{'file':'x.gz','meta':meta,'rows':6}])
   self.assertEqual(r['identical_duplicate_rows'],1);self.assertEqual(r['conflicting_rows'],1)
   self.assertEqual(r['invalid_values'],1);self.assertEqual(r['invalid_timestamp'],1)
   self.assertEqual(r['zero_volume_rows'],2);self.assertEqual(r['after_expiry'],1)
   self.assertEqual(r['outside_request'],1);self.assertEqual(r['unique_valid_timestamps'],2)

 def test_cross_file_comparison_and_unreadable_file(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);meta={'symbol':'NIFTY','key':'index','start':'2024-10-01','end':'2024-10-01'}
   body={'meta':meta,'data':{'candles':[['2024-10-01T03:45:00+00:00',10,11,9,10,0]]}}
   jobs=[]
   for name in ['a.gz','b.gz']:
    (root/name).write_bytes(gzip.compress(json.dumps(body).encode()));jobs.append({'file':name,'meta':meta,'rows':1})
   jobs.append({'file':'missing.gz','meta':meta,'rows':1})
   r=validate_contract(root,jobs)
   self.assertEqual(r['identical_duplicate_rows'],1);self.assertEqual(r['unreadable_files'],1)
   self.assertEqual(r['first_observed'],'2024-10-01 09:15')
   self.assertEqual(r['unobserved_regular_slots'],374)

if __name__=='__main__':unittest.main()
