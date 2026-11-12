"""Collector -> Detector direct delivery announces only jobs committed by this tick."""
import json
import os
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from psycopg.pq import TransactionStatus
from collector import input_runner,input_store

TOKEN='t'*32
ENV={'DETECTOR_DELIVERY_URL':'http://detector:8011','DETECTOR_DELIVERY_TOKEN':TOKEN}


class Opener:
    def __init__(self,fail=False):
        self.requests=[];self.fail=fail
    def open(self,request,timeout):
        self.requests.append((request,timeout))
        if self.fail:raise OSError('connection refused')
        @contextmanager
        def response():
            yield SimpleNamespace(read=lambda size:b'{}')
        return response()


class Connection:
    """Records SQL so a test can prove no queue scan happens."""
    def __init__(self,status=TransactionStatus.IDLE):
        self.sql=[];self.info=SimpleNamespace(transaction_status=status)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        self.sql.append(sql)
        return SimpleNamespace(fetchone=lambda:(True,) if 'advisory_lock' in sql else None)
    def commit(self):pass
    def rollback(self):pass


def job(i):
    return {'jobId':str(uuid.UUID(int=i)),'inputManifestHash':f'{i:064x}'}


class Delivery(unittest.TestCase):
    def deliver(self,jobs,env=ENV,**kwargs):
        opener=Opener(**kwargs)
        with mock.patch.dict(os.environ,env,clear=False),mock.patch('urllib.request.build_opener',return_value=opener):
            sent=input_runner.delivery(jobs)
        return sent,opener.requests

    def test_posts_only_job_id_and_manifest_hash(self):
        sent,requests=self.deliver([{**job(1),'sourceId':'s','observationId':'o'}])
        self.assertEqual(sent,1)
        request,timeout=requests[0]
        self.assertEqual(request.full_url,'http://detector:8011/detect');self.assertEqual(timeout,2)
        self.assertEqual(json.loads(request.data),job(1))
        self.assertEqual(request.get_header('Authorization'),'Bearer '+TOKEN)

    def test_limit_per_tick(self):
        sent,requests=self.deliver([job(i) for i in range(25)])
        self.assertEqual(sent,input_runner.DELIVERY_LIMIT);self.assertEqual(len(requests),10)
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in requests],[job(i)['jobId'] for i in range(10)])

    def test_empty_url_does_nothing(self):
        with mock.patch.dict(os.environ,{'DETECTOR_DELIVERY_URL':''}),mock.patch('urllib.request.build_opener') as build:
            self.assertEqual(input_runner.delivery([job(1)]),0)
        build.assert_not_called()

    def test_configuration_still_validated(self):
        for url,token in (('http://evil.example:8011',TOKEN),('http://detector:8011?x=1',TOKEN),('http://detector:8011','short')):
            with self.subTest(url=url),mock.patch.dict(os.environ,{'DETECTOR_DELIVERY_URL':url,'DETECTOR_DELIVERY_TOKEN':token}):
                with self.assertRaisesRegex(ValueError,'detector_delivery_configuration_rejected'):
                    input_runner.delivery([job(1)])

    def test_network_failure_is_ignored(self):
        sent,requests=self.deliver([job(1),job(2)],fail=True)
        self.assertEqual((sent,len(requests)),(2,2))


class Admission(unittest.TestCase):
    def admit(self,payload):
        saved=SimpleNamespace(observation_id=uuid.uuid4(),created=True)
        with mock.patch.object(input_store,'ingest_observation',return_value=saved),\
             mock.patch.object(input_store,'enqueue_observation',return_value=payload) as enqueue:
            outcome=input_store.admit(Connection(),uuid.uuid4(),uuid.uuid4(),'api','P',
                {'transaction_id':'t','process_id':'p','retry_count':0},namespace='api')
        enqueue.assert_called_once()
        return outcome

    def test_created_job_is_reported(self):
        payload={'schemaVersion':1,'eventId':job(7)['jobId'],**job(7),'sourceId':'s','observationId':'o','inputManifestId':'m'}
        self.assertEqual(self.admit(payload)['detectJob'],job(7))

    def test_waiting_config_item_is_not_reported(self):
        outcome=self.admit({'schemaVersion':1,'eventId':'e','sourceId':'s','observationId':'o'})
        self.assertTrue(outcome['created']);self.assertNotIn('detectJob',outcome)


class RunLoop(unittest.TestCase):
    def run_once(self,result,status=TransactionStatus.IDLE):
        conn=Connection(status);opener=Opener()
        env={**ENV,'DATABASE_URL':'postgresql://unused','COLLECTOR_SOURCE_KEY':'k'}
        with mock.patch.dict(os.environ,env),mock.patch.object(input_runner,'load_dotenv'),\
             mock.patch.object(input_runner.psycopg,'connect',return_value=conn),\
             mock.patch.object(input_runner,'ensure_source',return_value=uuid.uuid4()),\
             mock.patch.object(input_runner,'tick',return_value=result),\
             mock.patch('urllib.request.build_opener',return_value=opener):
            os.environ.pop('COLLECTOR_LOG_MAPPING_FILE',None)
            input_runner.run(once=True)
        return conn,opener.requests

    def test_only_this_tick_jobs_are_posted(self):
        conn,requests=self.run_once({'accepted':3,'duplicates':0,'detectJobs':[job(1),job(2)]})
        self.assertEqual([json.loads(r.data) for r,_ in requests],[job(1),job(2)])
        self.assertFalse([s for s in conn.sql if 'detection_logs' in s or 'run_jobs' in s])

    def test_waiting_items_make_no_http_or_queue_query(self):
        conn,requests=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[]})
        self.assertEqual(requests,[])
        self.assertFalse([s for s in conn.sql if 'detection_logs' in s or 'run_jobs' in s])

    def test_uncommitted_connection_is_not_announced(self):
        _,requests=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[job(1)]},status=TransactionStatus.INTRANS)
        self.assertEqual(requests,[])


if __name__=='__main__':unittest.main()
