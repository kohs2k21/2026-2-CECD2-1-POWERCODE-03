"""Replay timing and input contracts without a database."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo.runtime import Pacer
from demo.sources import csv_rows,numeric_features,project_process


class DemoContracts(unittest.TestCase):
    def test_rate_bound_and_no_catchup_after_slow_write(self):
        now=[0.0]
        def sleep(delay):now[0]+=delay
        pacer=Pacer(10,clock=lambda:now[0],sleep=sleep)
        pacer.before_send();now[0]+=.005;pacer.after_send();pacer.before_send()
        self.assertAlmostEqual(now[0],.105)
        now[0]+=2;pacer.after_send();pacer.before_send()
        self.assertAlmostEqual(now[0],2.205)
        for invalid in (0,-1,float('nan'),float('inf'),10001):
            with self.assertRaises(ValueError):Pacer(invalid)

    def test_no_body_response_or_auth_values(self):
        raw=project_process({'SUCCESS_COUNT':'3','BODY':'secret','Authorization':'token','RESPONSE_MESSAGE':'raw'})
        self.assertEqual(raw,{'success_count':'3'})
        self.assertEqual(numeric_features([{'raw':{'success_count':i,'retry_count':0}} for i in range(20)]),
            [{'name':'demo_success_count','expression':{'op':'field','field':'P.success_count'}}])
        with self.assertRaises(ValueError):numeric_features([{'raw':{'success_count':1}}]*1000)

    def test_csv_extension_and_safe_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'EC_PROCESS_MONITORING.csv'
            with path.open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['TRANSACTION_ID','PROCESS_ID','RETRY_COUNT','SUCCESS_COUNT','BODY'])
                writer.writerow(['t','p',0,3,'secret'])
                writer.writerow(['t','p',0,4,'secret'])
            rows=list(csv_rows(path,10))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['group'],rows[1]['group'])
            self.assertNotIn('BODY',str(rows))
            self.assertNotIn('secret',str(rows))
            self.assertIsNone(rows[0]['label'])

if __name__=='__main__':unittest.main()
