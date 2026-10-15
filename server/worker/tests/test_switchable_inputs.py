import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector.input_contract import normalize,mapped_api_records,identity

class InputContracts(unittest.TestCase):
    def test_bounded_reference_sample_does_not_weaken_full_snapshot_guard(self):
        from trainer.runner import _snapshot_rows
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,*args):pass
            def fetchall(self):return [(i,{},'train',None) for i in range(4)]
        class Connection:
            def cursor(self):return Cursor()
        with self.assertRaisesRegex(ValueError,'snapshot_row_limit_exceeded'):
            _snapshot_rows(Connection(),'id','train',3)
        self.assertEqual(len(_snapshot_rows(Connection(),'id','train',3,allow_sample=True)),3)

    def test_db_csv_columns_nulls_and_separate_body(self):
        value=normalize('M',{'MESSAGE_ID':'m','TRANSACTION_ID':'t','PROCESS_ID':'p','PROCESS_DT':'2026-10-01 10:00:00',
            'SUCCESS_COUNT':'\\N','MESSAGE_BODY':'secret','Authorization':'token','RESPONSE_MESSAGE':'secret'})
        self.assertIsNone(value['success_count']);self.assertIn('process_datetime_raw',value)
        self.assertNotIn('secret',str(value));self.assertNotIn('token',str(value))
        self.assertEqual(normalize('B',{'MESSAGE_ID':'m','MESSAGE_BODY':'secret','MESSAGE_IN':'secret'}),{'message_id':'m'})
        self.assertEqual(identity('P',{'transaction_id':'t','process_id':'p'}),['t','p','unknown'])
        self.assertEqual(identity('P',{'transaction_id':'t','process_id':'p','retry_count':'0.0'}),['t','p','0'])
        self.assertIsNone(identity('P',{'transaction_id':'t','process_id':'p','retry_count':-1}))

    def test_configured_nested_api_matches_csv_projection(self):
        mapping={'version':1,'streams':[{'layer':'P','path':['processes'],'fields':{'PROCESS_ID':['id'],'ERROR_COUNT':['errors'],'TOTAL_COUNT':['total']},'inherit':{'TRANSACTION_ID':['id']}}]}
        records=list(mapped_api_records([{'id':'t','processes':[{'id':'p','errors':2,'total':10,'body':'secret'}]}],mapping))
        self.assertEqual(records,[('P',normalize('P',{'TRANSACTION_ID':'t','PROCESS_ID':'p','ERROR_COUNT':2,'TOTAL_COUNT':10}))])

    def test_settings_are_not_runtime_logs_and_empty_does_not_invent_records(self):
        self.assertEqual(list(mapped_api_records([])),[])
        with self.assertRaisesRegex(ValueError,'mapping_required'):
            list(mapped_api_records([{'interfaceId':'I','revision':1,'processInfo':[{'processId':'p'}]}]))
        self.assertEqual(list(mapped_api_records([{'layer':'P','values':{'PROCESS_ID':'p','ERROR_COUNT':1}}]))[0][1]['error_count'],1)

if __name__=='__main__':unittest.main()
