"""CSV replay staging: inside one transaction group the TRANSACTION row is committed last."""
import csv
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector import input_store


class Connection:
    """Captures the frozen replay rows stage_csv writes; no database."""
    def __init__(self,old=None):
        self.rows=None;self.ready=None;self.old=old
    def execute(self,sql,params=None):
        if 'FROM data_input_replays' in sql:return SimpleNamespace(fetchone=lambda:self.old)
        if sql.startswith('UPDATE data_input_replays'):self.ready=params
        return SimpleNamespace(fetchone=lambda:None)
    def cursor(self):
        connection=self
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def executemany(self,sql,rows):connection.rows=list(rows)
        return Cursor()


def write(directory,name,headers,rows):
    path=Path(directory)/name
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(headers);w.writerows(rows)
    return path


class ReplayOrderTest(unittest.TestCase):
    def stage(self,files,conn=None):
        conn=conn or Connection()
        input_store.stage_csv(conn,uuid.uuid4(),'order',files)
        return conn,[(position,layer,json.loads(values)) for _,position,layer,values,_ in conn.rows]

    def fixture(self,temp):
        # T file order (t2 before t1) defines transaction order; P/M/B files list them the other way round.
        return {
            'T':write(temp,'T.csv',['TRANSACTION_ID','RETRY_COUNT'],[['t2',0],['t1',0],['t2',1],['',0]]),
            'P':write(temp,'P.csv',['TRANSACTION_ID','PROCESS_ID','RETRY_COUNT'],
                [['t1','p1',0],['t2','p1',0],['t1','p2',0],['t3','p1',0],['t2','p2',0],['','px',0]]),
            'M':write(temp,'M.csv',['TRANSACTION_ID','PROCESS_ID','MESSAGE_ID'],[['t1','p1','m1'],['t2','p2','m2']]),
            'B':write(temp,'B.csv',['MESSAGE_ID','MESSAGE_BODY'],[['m2','x'],['m1','x'],['orphan','x']]),
        }

    def test_transaction_row_follows_its_process_message_and_body_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            conn,rows=self.stage(self.fixture(temp))
        self.assertEqual([p for p,_,_ in rows],list(range(len(rows))))
        groups={}
        for position,layer,values in rows:
            tx=values.get('transaction_id') or ({'m1':'t1','m2':'t2'}.get(values.get('message_id')) if layer=='B' else None)
            if tx:groups.setdefault(tx,[]).append((position,layer))
        for tx in ('t1','t2'):
            last_t=max(p for p,l in groups[tx] if l=='T');other=[p for p,l in groups[tx] if l!='T']
            self.assertTrue(other and min(p for p,l in groups[tx] if l=='T')>max(other),(tx,groups[tx]))
            self.assertEqual(last_t,max(p for p,_ in groups[tx]))
            # Within a group: P, then M, then B (B is grouped through its M), then T.
            self.assertEqual([l for _,l in sorted(groups[tx])],sorted([l for _,l in groups[tx]],key='PMBT'.index))
        # Transaction order across groups is still the T file order; groups stay contiguous.
        self.assertLess(max(p for p,_ in groups['t2']),min(p for p,_ in groups['t1']))
        self.assertEqual(sorted(p for p,_ in groups['t2']),list(range(len(groups['t2']))))
        # Retried T rows keep their file order at the end of the group.
        self.assertEqual([v['retry_count'] for _,l,v in rows if l=='T' and v.get('transaction_id')=='t2'],['0','1'])
        # Counts are unchanged by reordering.
        self.assertEqual(conn.ready[0],15)
        self.assertEqual(json.loads(conn.ready[1]),{'T':4,'P':6,'M':2,'B':3})

    def test_unmatched_rows_keep_their_own_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            _,rows=self.stage(self.fixture(temp))
        layers=[l for _,l,_ in rows]
        # Unmatched T (no id) is its own group right after the T-file groups that precede it; t3 has no T row.
        unmatched_t=next(p for p,l,v in rows if l=='T' and not v.get('transaction_id'))
        t1_end=max(p for p,l,v in rows if v.get('transaction_id')=='t1' or v.get('message_id')=='m1')
        self.assertEqual(unmatched_t,t1_end+1)
        tail=[(l,v.get('transaction_id') or v.get('message_id')) for _,l,v in rows[unmatched_t+1:]]
        self.assertEqual(tail,[('P','t3'),('P',None),('B','orphan')])
        self.assertEqual(layers.count('B'),3)

    def test_existing_ready_replay_with_old_manifest_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            files=self.fixture(temp)
            conn,_=self.stage(files)
            manifest=conn.ready[2]
            # Same key, same files, same (new) order: idempotent.
            again=Connection(old=('ready',manifest))
            input_store.stage_csv(again,uuid.uuid4(),'order',files)
            self.assertIsNone(again.rows)
            # A replay frozen earlier in another order keeps its stored rows; re-staging the key is refused.
            with self.assertRaisesRegex(ValueError,'replay_key_conflict_use_new_key'):
                input_store.stage_csv(Connection(old=('ready','0'*64)),uuid.uuid4(),'order',files)


if __name__=='__main__':unittest.main()
