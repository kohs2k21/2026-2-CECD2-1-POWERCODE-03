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

    def test_explicit_config_pin_accepts_only_legacy_schema_versions(self):
        from collector.input_store import switch
        class Result:
            def __init__(self,row):self.row=row
            def fetchone(self):return self.row
        class Connection:
            # config: None = no row for (id, source); otherwise (schemaVersion as psycopg decodes jsonb,).
            def __init__(self,config,pinned=None):self.config=config;self.pinned=pinned;self.updates=[]
            def execute(self,sql,params=()):
                if 'FROM ops_input_controls' in sql:return Result((0,None,self.pinned))
                if 'FROM ml_config_versions' in sql:return Result(self.config)
                if sql.lstrip().startswith('UPDATE ops_input_controls'):self.updates.append(params)
                return Result(None)
        def pin(conn,config,mode='api'):return switch(conn,'s',mode=mode,expected_revision=0,actor='t',settings={'pollSeconds':2},config=config)
        for value in (1,2,2.0):
            conn=Connection((value,));self.assertEqual(pin(conn,'c')['configVersionId'],'c');self.assertEqual(conn.updates[0][2],'c')
        for value in (3,None,'2',True,0,[2],{'v':2}):
            conn=Connection((value,))
            with self.assertRaisesRegex(ValueError,'^config_schema_not_supported_yet$'):pin(conn,'c')
            self.assertEqual(conn.updates,[],value)
        conn=Connection(None) # Wrong source keeps precedence over the schema check.
        with self.assertRaisesRegex(ValueError,'^input_config_source_mismatch$'):pin(conn,'c')
        self.assertEqual(conn.updates,[])
        conn=Connection(None);self.assertIsNone(pin(conn,None)['configVersionId']) # --config active
        conn=Connection((2,),pinned='old');self.assertEqual(pin(conn,'keep')['configVersionId'],'old')
        conn=Connection((3,)) # pausing admits nothing, so an explicit v3 pin is not refused for schema
        self.assertEqual(pin(conn,'c',mode='paused')['configVersionId'],'c')
        conn=Connection((3,),pinned='old');self.assertEqual(pin(conn,'keep',mode='paused')['configVersionId'],'old')
        conn=Connection((3,),pinned='old') # ...but resuming with the kept v3 config is refused
        with self.assertRaisesRegex(ValueError,'^config_schema_not_supported_yet$'):pin(conn,'keep')
        self.assertEqual(conn.updates,[])
        conn=Connection(None) # ...but the source match still applies when paused
        with self.assertRaisesRegex(ValueError,'^input_config_source_mismatch$'):pin(conn,'c',mode='paused')

if __name__=='__main__':unittest.main()
