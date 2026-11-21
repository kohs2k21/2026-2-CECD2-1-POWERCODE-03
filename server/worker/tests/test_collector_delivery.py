"""Collector -> Detector direct delivery announces only committed jobs, off the poll loop; API polls start-to-start."""
import json
import os
import sys
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from psycopg.pq import TransactionStatus
from collector import input_runner

TOKEN='t'*32
# Empty limit settings mean the defaults; set them so a value in the caller's environment cannot leak in.
ENV={'DETECTOR_DELIVERY_URL':'http://detector:8011','DETECTOR_DELIVERY_TOKEN':TOKEN,
     'DETECTOR_DELIVERY_PER_TICK':'','DETECTOR_DELIVERY_MAX_AGE_SECONDS':''}


class Opener:
    def __init__(self,fail=False,gate=None):
        self.requests=[];self.fail=fail;self.gate=gate;self.entered=threading.Event()
    def open(self,request,timeout):
        self.entered.set()
        if self.gate:self.gate.wait(5)
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


class Clock:
    """Manual monotonic clock: a notice's age is exactly what the test sets, never real elapsed time."""
    def __init__(self,now=0.0):self.now=now
    def __call__(self):return self.now


class Delivery(unittest.TestCase):
    def deliver(self,jobs,env=ENV,capacity=input_runner.DELIVERY_CAPACITY,clock=None,**kwargs):
        opener=Opener(**kwargs)
        with mock.patch.dict(os.environ,env,clear=False),mock.patch('urllib.request.build_opener',return_value=opener):
            notifier=input_runner.Delivery.from_environment(capacity=capacity,clock=clock or Clock())
            queued=notifier.offer(jobs)
            self.assertTrue(notifier.drain(5))
        return notifier,queued,opener.requests

    def test_posts_only_job_id_and_manifest_hash(self):
        _,queued,requests=self.deliver([{**job(1),'sourceId':'s','observationId':'o'}])
        self.assertEqual(queued,1)
        request,timeout=requests[0]
        self.assertEqual(request.full_url,'http://detector:8011/detect');self.assertEqual(timeout,2)
        self.assertEqual(json.loads(request.data),job(1))
        self.assertEqual(request.get_header('Authorization'),'Bearer '+TOKEN)

    def test_offer_never_blocks_and_overflow_is_dropped(self):
        gate=threading.Event();opener=Opener(gate=gate)
        with mock.patch.dict(os.environ,ENV),mock.patch('urllib.request.build_opener',return_value=opener):
            notifier=input_runner.Delivery.from_environment(capacity=3,per_tick=20,clock=Clock())
            # Hold the sender inside a POST of job 0 so the queue really fills: it is empty and nothing drains it.
            self.assertEqual(notifier.offer([job(0)]),1)
            self.assertTrue(opener.entered.wait(5))
            started=time.monotonic()
            queued=notifier.offer([job(i) for i in range(1,11)])
            self.assertLess(time.monotonic()-started,.5)   # a stuck Detector does not hold the caller
            gate.set();self.assertTrue(notifier.drain(5))
        # exactly the capacity is accepted (jobs 1-3); the 7 others are dropped and stay in the DB queue only
        self.assertEqual((queued,notifier.overflow,notifier.capped),(3,7,0))
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in opener.requests],[job(i)['jobId'] for i in range(4)])

    def test_many_jobs_are_all_posted_in_order(self):
        _,queued,requests=self.deliver([job(i) for i in range(25)],env={**ENV,'DETECTOR_DELIVERY_PER_TICK':'25'})
        self.assertEqual(queued,25)
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in requests],[job(i)['jobId'] for i in range(25)])

    def test_defaults(self):
        with mock.patch.dict(os.environ,ENV):
            self.assertEqual(input_runner.delivery_limits(),(8,.2))

    def test_per_tick_cap_offers_the_first_jobs_only(self):
        notifier,queued,requests=self.deliver([job(i) for i in range(20)])
        # default cap 8: jobs 0-7 are posted in order, 8-19 are left to Detector queue polling
        self.assertEqual(queued,8)
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in requests],[job(i)['jobId'] for i in range(8)])
        self.assertEqual(notifier.counters(),{'offered':8,'capped':12,'overflow':0,'stale':0,'sent':8,'failed':0})

    def test_cap_applies_per_offer(self):
        opener=Opener()
        with mock.patch.dict(os.environ,{**ENV,'DETECTOR_DELIVERY_PER_TICK':'2'}),mock.patch('urllib.request.build_opener',return_value=opener):
            notifier=input_runner.Delivery.from_environment(clock=Clock())
            self.assertEqual([notifier.offer([job(1),job(2),job(3)]),notifier.offer([job(4)])],[2,1])
            self.assertTrue(notifier.drain(5))
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in opener.requests],[job(i)['jobId'] for i in (1,2,4)])
        self.assertEqual((notifier.offered,notifier.capped),(3,1))

    def test_zero_per_tick_posts_nothing(self):
        notifier,queued,requests=self.deliver([job(1),job(2)],env={**ENV,'DETECTOR_DELIVERY_PER_TICK':'0'})
        self.assertEqual((queued,requests),(0,[]))
        self.assertEqual(notifier.counters(),{'offered':0,'capped':2,'overflow':0,'stale':0,'sent':0,'failed':0})

    def test_stale_notices_are_skipped(self):
        gate=threading.Event();opener=Opener(gate=gate);clock=Clock(0.0)
        with mock.patch.dict(os.environ,ENV),mock.patch('urllib.request.build_opener',return_value=opener):
            notifier=input_runner.Delivery.from_environment(clock=clock)
            self.assertEqual(notifier.offer([job(0)]),1)
            self.assertTrue(opener.entered.wait(5))          # the sender is inside the POST of job 0
            self.assertEqual(notifier.offer([job(1),job(2)]),2)   # stamped at 0.0
            clock.now=.2                                   # exactly the max age: still sent
            gate.set();self.assertTrue(notifier.drain(5))
            gate.clear();opener.entered.clear()
            self.assertEqual(notifier.offer([job(3)]),1)      # stamped at 0.2; held while job 3 is posted
            self.assertTrue(opener.entered.wait(5))
            self.assertEqual(notifier.offer([job(4),job(5)]),2)   # stamped at 0.2
            clock.now=.41                                  # older than 0.2 s when the sender reaches them
            gate.set();self.assertTrue(notifier.drain(5))
        self.assertEqual([json.loads(r.data)['jobId'] for r,_ in opener.requests],[job(i)['jobId'] for i in range(4)])
        self.assertEqual(notifier.counters(),{'offered':6,'capped':0,'overflow':0,'stale':2,'sent':4,'failed':0})

    def test_max_age_from_environment(self):
        clock=Clock()
        with mock.patch.dict(os.environ,{**ENV,'DETECTOR_DELIVERY_MAX_AGE_SECONDS':'1.5'}),\
             mock.patch('urllib.request.build_opener',return_value=Opener()):
            notifier=input_runner.Delivery.from_environment(clock=clock)
        self.assertEqual(notifier.max_age,1.5)

    def test_limits_are_validated(self):
        for key,value,code in (('DETECTOR_DELIVERY_PER_TICK','-1','per_tick'),('DETECTOR_DELIVERY_PER_TICK','257','per_tick'),
                               ('DETECTOR_DELIVERY_PER_TICK','2.5','per_tick'),('DETECTOR_DELIVERY_PER_TICK','x','per_tick'),
                               ('DETECTOR_DELIVERY_MAX_AGE_SECONDS','0','max_age'),('DETECTOR_DELIVERY_MAX_AGE_SECONDS','-1','max_age'),
                               ('DETECTOR_DELIVERY_MAX_AGE_SECONDS','nan','max_age'),('DETECTOR_DELIVERY_MAX_AGE_SECONDS','inf','max_age'),
                               ('DETECTOR_DELIVERY_MAX_AGE_SECONDS','61','max_age'),('DETECTOR_DELIVERY_MAX_AGE_SECONDS','x','max_age')):
            with self.subTest(key=key,value=value),mock.patch.dict(os.environ,{**ENV,key:value}):
                with self.assertRaisesRegex(ValueError,f'detector_delivery_{code}_rejected'):
                    input_runner.Delivery.from_environment()

    def test_empty_url_disables_delivery(self):
        with mock.patch.dict(os.environ,{'DETECTOR_DELIVERY_URL':''}):
            self.assertIsNone(input_runner.Delivery.from_environment())

    def test_configuration_still_validated(self):
        for url,token in (('http://evil.example:8011',TOKEN),('http://detector:8011?x=1',TOKEN),('http://detector:8011','short'),
                          ('ftp://detector:8011',TOKEN),('http://u:p@detector:8011',TOKEN)):
            with self.subTest(url=url),mock.patch.dict(os.environ,{'DETECTOR_DELIVERY_URL':url,'DETECTOR_DELIVERY_TOKEN':token}):
                with self.assertRaisesRegex(ValueError,'detector_delivery_configuration_rejected'):
                    input_runner.Delivery.from_environment()

    def test_network_failure_is_ignored(self):
        notifier,queued,requests=self.deliver([job(1),job(2)],fail=True)
        self.assertEqual((queued,len(requests),notifier.sent,notifier.failed),(2,2,2,2))


class RunLoop(unittest.TestCase):
    def run_once(self,result,status=TransactionStatus.IDLE,env=None):
        conn=Connection(status);opener=Opener()
        # A generous max age: the real clock runs here, and these tests are not about staleness.
        env={**(ENV if env is None else env),'DATABASE_URL':'postgresql://unused','COLLECTOR_SOURCE_KEY':'k'}
        env['DETECTOR_DELIVERY_MAX_AGE_SECONDS']=env.get('DETECTOR_DELIVERY_MAX_AGE_SECONDS') or '60'
        out=[]
        with mock.patch.dict(os.environ,env),mock.patch.object(input_runner,'load_dotenv'),\
             mock.patch.object(input_runner.psycopg,'connect',return_value=conn),\
             mock.patch.object(input_runner,'ensure_source',return_value=uuid.uuid4()),\
             mock.patch.object(input_runner,'tick',return_value=result) as tick,\
             mock.patch('builtins.print',side_effect=lambda *a,**k:out.append(a[0])),\
             mock.patch('urllib.request.build_opener',return_value=opener):
            os.environ.pop('COLLECTOR_LOG_MAPPING_FILE',None)
            input_runner.run(once=True)
        self.assertIsInstance(tick.call_args.kwargs['schedule'],dict)
        return conn,opener.requests,out

    def test_only_this_tick_jobs_are_posted(self):
        conn,requests,_=self.run_once({'accepted':3,'duplicates':0,'detectJobs':[job(1),job(2)]})
        self.assertEqual([json.loads(r.data) for r,_ in requests],[job(1),job(2)])
        self.assertFalse([s for s in conn.sql if 'detection_logs' in s or 'run_jobs' in s])

    def test_waiting_items_make_no_http_or_queue_query(self):
        conn,requests,_=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[]})
        self.assertEqual(requests,[])
        self.assertFalse([s for s in conn.sql if 'detection_logs' in s or 'run_jobs' in s])

    def test_uncommitted_connection_is_not_announced(self):
        _,requests,_=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[job(1)]},status=TransactionStatus.INTRANS)
        self.assertEqual(requests,[])

    def test_once_reports_counters(self):
        _,requests,out=self.run_once({'accepted':3,'duplicates':0,'detectJobs':[job(i) for i in range(10)]})
        self.assertEqual(len(requests),8)
        self.assertEqual(json.loads(out[-1]),{'delivery':{'offered':8,'capped':2,'overflow':0,'stale':0,'sent':8,'failed':0}})

    def test_rejected_limits_keep_collecting(self):
        _,requests,out=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[job(1)]},
            env={**ENV,'DETECTOR_DELIVERY_PER_TICK':'-1'})
        self.assertEqual(requests,[])
        self.assertEqual(json.loads(out[0]),{'deliveryError':'detector_delivery_per_tick_rejected'})

    def test_rejected_configuration_keeps_collecting(self):
        _,requests,out=self.run_once({'accepted':1,'duplicates':0,'detectJobs':[job(1)]},
            env={'DETECTOR_DELIVERY_URL':'http://evil.example:1','DETECTOR_DELIVERY_TOKEN':TOKEN})
        self.assertEqual(requests,[])
        self.assertEqual(json.loads(out[0]),{'deliveryError':'detector_delivery_configuration_rejected'})


class Cadence(unittest.TestCase):
    NOW=datetime(2026,11,21,1,tzinfo=timezone.utc)
    RUN=uuid.UUID(int=9)

    def old(self,mode='api',attempt=None,success=None):
        return {'mode':mode,'run':self.RUN,'attempt':attempt,'success':success}

    def test_api_start_to_start(self):
        schedule={'run':self.RUN,'started':100.0}
        wait=lambda at:input_runner.remaining_wait(self.old(attempt=self.NOW),1,schedule,now=self.NOW,monotonic=at)
        self.assertAlmostEqual(wait(100.3),.7)   # poll took .3 s: next one .7 s later, 1 s after the previous start
        self.assertAlmostEqual(wait(101.0),0)
        self.assertLess(wait(103.5),0)            # overrun: due now, once (the next start resets the schedule)

    def test_api_without_in_process_start_uses_last_attempt(self):
        for schedule in (None,{},{'run':uuid.UUID(int=8),'started':100.0}):
            with self.subTest(schedule=schedule):
                got=input_runner.remaining_wait(self.old(attempt=self.NOW-timedelta(seconds=.25)),1,schedule,now=self.NOW,monotonic=100.1)
                self.assertAlmostEqual(got,.75)
        self.assertEqual(input_runner.remaining_wait(self.old(),1,{},now=self.NOW),0)

    def test_csv_pacing_unchanged(self):
        schedule={'run':self.RUN,'started':100.0}
        got=input_runner.remaining_wait(self.old('csv',attempt=self.NOW,success=self.NOW-timedelta(seconds=.04)),.1,schedule,
            now=self.NOW,monotonic=100.0)
        self.assertAlmostEqual(got,.06)

    def test_tick_records_poll_start_and_waits_only_the_rest(self):
        state={'mode':'api','revision':1,'run':self.RUN,'config':None,'settings':{'pollSeconds':1},'position':0,
               'status':'running','attempt':None,'success':None,'replay':None,'count':None,'switched':None}
        clock=iter([50.0,50.2,50.7])  # poll start, sample receipt age, end-of-tick check
        class Conn:
            def execute(self,*a,**k):return SimpleNamespace(fetchone=lambda:None)
            def transaction(self):
                @contextmanager
                def tx():yield
                return tx()
            def commit(self):pass
            def rollback(self):pass
        receipt={'finished_at':self.NOW,'received_monotonic':0.0}
        api=SimpleNamespace(in_progress=lambda:[],last_receipt=receipt)
        schedule={}
        with mock.patch.object(input_runner,'read_state',return_value=state),mock.patch.object(input_runner,'fence',return_value=True),\
             mock.patch.object(input_runner,'admit_batch',return_value=[]),\
             mock.patch.object(input_runner,'store_snapshot',return_value=(uuid.uuid4(),True)),\
             mock.patch.object(input_runner,'store_sample'),\
             mock.patch.object(input_runner.time,'monotonic',side_effect=lambda:next(clock)):
            result=input_runner.tick(Conn(),uuid.uuid4(),api_factory=lambda:api,collect_catalog=False,schedule=schedule)
        self.assertEqual(schedule,{'run':self.RUN,'started':50.0})
        self.assertAlmostEqual(result['wait'],.3)   # 1 s after the poll start, not 1 s after the end


if __name__=='__main__':unittest.main()
