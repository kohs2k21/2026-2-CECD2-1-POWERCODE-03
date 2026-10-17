"""Independent local API -> PG queue -> Trainer -> Detector contract checks.

Requires a fresh localhost qa_pipeline DB with runtime migrations. No customer input.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import joblib
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration_research_pipeline import source, observe
from trainer.catalog import register_feature_set
from trainer import runner
from detector.computation import load_context, compute, model_reference
from detector.jobs import enqueue_observation
from detector.runner import process_detection_message
from pg_queue import claim, enqueue
from feature_fit import transform
import model_adapters

REPO = Path(__file__).resolve().parents[3]
FIELD = {'op': 'field', 'field': 'P.total_count'}
DEFINITIONS = [
    {'name': 'count_raw', 'expression': FIELD},
    {'name': 'count_centered', 'expression': {'op': 'group_centered', 'groupBy': 'P.adapter_type', 'value': FIELD}},
]


class Service:
    def __init__(self, directory, dsn):
        parsed = urlsplit(dsn)
        assert parsed.hostname in {'localhost', '127.0.0.1'} and parsed.path in {'/qa_pipeline', '/qa_trainer_service'}
        self.root = Path(directory)
        self.dsn = dsn
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(exist_ok=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.env = {key: os.environ[key] for key in ['PATH', 'SYSTEMROOT', 'TEMP', 'TMP'] if key in os.environ}
        self.env.update(NODE_ENV='test', DATABASE_URL=dsn, JWT_SECRET='isolated-trainer56-fixture-only',
            SEED_ADMIN_EMAIL='trainer-admin@example.test', SEED_ADMIN_PASSWORD='fixture-admin-56',
            SEED_USER_EMAIL='trainer-user@example.test', SEED_USER_PASSWORD='fixture-user-56',
            USER_DATA_PATH=str(self.root/'users.json'), PORT=str(port), PYTHONUTF8='1',
            PYTHON_DOTENV_DISABLED='1', PYTHONPATH=str(REPO/'server/worker'), ARTIFACT_ROOT=str(self.artifacts))
        self.log = open(self.root/'api.log', 'w', encoding='utf-8')
        self.api = subprocess.Popen(['node', str(REPO/'server/backend/dist/index.js')], cwd=self.root,
                                    env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                if self.request('/health')[0] == 200: break
            except OSError: pass
            time.sleep(.1)
        else: raise AssertionError('temporary API failed to start')
        self.token = self.request('/api/auth/login', {'email': self.env['SEED_ADMIN_EMAIL'], 'password': self.env['SEED_ADMIN_PASSWORD']})[1]['token']
        self.user = self.request('/api/auth/login', {'email': self.env['SEED_USER_EMAIL'], 'password': self.env['SEED_USER_PASSWORD']})[1]['token']

    def request(self, path, body=None, token=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer '+token
        if key: headers['Idempotency-Key'] = key
        req = Request(self.base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=30) as response: return response.status, json.load(response)
        except HTTPError as response: return response.code, json.load(response)

    def worker(self):
        completed = subprocess.run([sys.executable, '-m', 'trainer.runner', '--once'], cwd=self.root,
                                   env=self.env, capture_output=True, text=True, timeout=90)
        assert completed.returncode == 0, completed.stderr[-1000:]

    def close(self):
        self.api.terminate()
        self.api.wait(timeout=15)
        self.log.close()


def seed(conn):
    sid = source(conn, 'Trainer56 controlled process fixture')
    start = datetime.now(timezone.utc)-timedelta(hours=2)
    for index in range(1000):
        raw = {'adapter_type': 'A' if index % 2 else 'B', 'total_count': str(50+index%13),
               'success_count': str(50+index%13), 'error_count': '0', 'status': 'S'}
        if index >= 700: raw['total_count'] = str(10000+index)
        if index >= 850: raw['adapter_type'] = 'UNSEEN'
        observe(conn, sid, f'trainer56-{index}', raw, 'normal' if index%2 else 'anomaly' if index>=700 else 'normal',
                persisted=start+timedelta(seconds=index))
    features = register_feature_set(conn, DEFINITIONS, 'fixture-admin', definition_kind='operator_defined')
    conn.commit()
    return {'kind': 'train', 'sourceId': str(sid), 'featureSetId': str(features),
            'from': (start-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),
            'until': (start+timedelta(seconds=1100)).isoformat().replace('+00:00','Z'),
            'detectionPolicy': {'mode': 'ml_only', 'deadlineMs': 30000, 'combination': 'selected_path'}}


def state(conn, identifier):
    row=conn.execute('SELECT status,result_summary,error_code,attempt_count FROM run_jobs WHERE id=%s',(identifier,)).fetchone()
    conn.commit()
    return row


def retry_now(conn, identifier):
    conn.execute("UPDATE powercode_queue.training_jobs SET available_at=clock_timestamp() WHERE payload->>'jobId'=%s",(identifier,))
    conn.execute('UPDATE run_jobs SET next_run_at=clock_timestamp() WHERE id=%s',(identifier,))
    conn.commit()


def verify(service, conn, spec):
    request=lambda path,body=None,key=None:service.request(path,body,service.token,key)
    assert service.request('/api/jobs/capabilities')[0]==401
    assert request('/api/jobs/capabilities')[1]['models'][0]['key']=='iforest'
    catalog=request('/api/jobs/feature-sets?sourceId='+spec['sourceId'])[1]
    assert any(x['id']==spec['featureSetId'] and x['supported'] for x in catalog['items'])
    assert request('/api/jobs/feature-sets?sourceId='+str(uuid.uuid4()))[0]==404
    assert service.request('/api/jobs',spec,service.user)[0]==403
    assert request('/api/jobs',{**spec,'modelKey':'kmeans'})[0]==422
    assert request('/api/jobs',{**spec,'modelKey':'iforest','detectionPolicy':{'mode':'rule_only','deadlineMs':1000,'combination':'selected_path'}})[0]==422
    before=conn.execute('SELECT * FROM ops_system_state').fetchall();conn.commit()
    key=str(uuid.uuid4())
    status,first=request('/api/jobs',spec,key)
    assert status==201
    identifier=first['id']
    duplicate_status,duplicate=request('/api/jobs',spec,key)
    assert duplicate_status==200 and duplicate['id']==identifier and duplicate['inputSpec']==first['inputSpec']
    assert request('/api/jobs',{**spec,'modelKey':'iforest'},key)[0]==409
    assert service.request('/api/jobs/'+identifier,token=service.user)[0]==404
    assert conn.execute("SELECT count(*) FROM powercode_queue.training_jobs WHERE payload->>'jobId'=%s",(identifier,)).fetchone()[0]==1;conn.commit()
    service.worker()
    status,result,error,attempt=state(conn,identifier)
    assert status=='succeeded',(status,error)
    assert attempt==1 and result['splitCounts']=={'train':700,'validation':150,'test':150}
    assert request('/api/jobs',spec,key)[1]['id']==identifier
    assert request('/api/jobs/'+identifier)[1]['resultSummary']==result
    assert conn.execute('SELECT * FROM ops_system_state').fetchall()==before;conn.commit()
    assert result['evaluationEnvelope']['execution_cost']['status']=='measured'
    assert all(result['evaluationEnvelope'][key]['status']=='unavailable' for key in ('rule_ml','synthetic','operational'))
    config_id=uuid.UUID(result['configVersionId']); source_id=uuid.UUID(spec['sourceId'])
    context=load_context(conn,config_id,source_id);conn.commit()
    ref=model_reference(context,service.artifacts)
    fit=joblib.load(ref['fitPath'])['preprocessing']
    assert max(map(float,fit['features']['count_centered']['groups'].values()))<100
    assert sum(fit['features']['count_centered']['referenceCounts'].values())==700
    assert conn.execute('SELECT count(*) FROM data_feature_materializations WHERE feature_set_id=%s',(spec['featureSetId'],)).fetchone()[0]==0;conn.commit()
    for raw in [{'adapter_type':'A','total_count':'100'}, {'adapter_type':'UNSEEN','total_count':'100'}]:
        expected=transform(DEFINITIONS,raw,fit)
        assert model_adapters.transform_iforest_features(ref,DEFINITIONS,raw)==expected
        verdict=compute(context,raw,service.artifacts)
        if raw['adapter_type']=='A':
            assert verdict['ml']['score']==model_adapters.score_iforest(ref,expected[0])
            assert verdict['ml']['anomalous']==(verdict['ml']['score']>=float(context['models'][0][6]['value']))
        else: assert verdict['ml']['status']=='not_ready' and verdict['ml'].get('anomalous') is None
    live_raw={'adapter_type':'A','total_count':'100'}
    live=observe(conn,source_id,'trainer56-live-contract',live_raw)
    payload=enqueue_observation(conn,live.observation_id,config_id=config_id);conn.commit()
    message=claim(conn,'detection',message_id=uuid.UUID(payload['eventId']));conn.commit()
    computed_live=[]
    def capture_compute(*args, **kwargs):
        verdict=compute(*args, **kwargs)
        computed_live.append(copy.deepcopy(verdict))
        return verdict
    with patch.dict(os.environ,service.env), patch('detector.computation.compute',side_effect=capture_compute):
        process_detection_message(conn,message,service.artifacts);conn.commit()
    expected_live=compute(context,live_raw,service.artifacts)
    stored=conn.execute('SELECT status,ml_status,anomaly_score FROM run_process_decisions WHERE process_observation_id=%s AND config_version_id=%s',
                        (live.observation_id,config_id)).fetchone()
    assert len(computed_live)==1, {'durable_compute_count':len(computed_live)}
    actual_live=computed_live[0]
    assert stored is not None, 'durable decision was not persisted'
    assert stored[:2]==('complete','succeeded'), {'stored_status':stored[:2], 'durable_result':actual_live}
    assert expected_live['status']=='complete' and expected_live['ml']['status']=='succeeded', {'direct_result':expected_live}
    assert actual_live['ml']['score']==expected_live['ml']['score'], {
        'durable_score':repr(actual_live['ml']['score']), 'direct_score':repr(expected_live['ml']['score'])}
    assert actual_live['anomalous']==expected_live['anomalous'], {
        'durable_anomalous':actual_live['anomalous'], 'direct_anomalous':expected_live['anomalous']}
    # INSERT adapts a Python float as float8, then PostgreSQL casts to NUMERIC.
    # Compare exact server-cast values, not a rounded stored value to the raw float.
    expected_numeric=conn.execute('SELECT %s::double precision::numeric',
                                  (expected_live['ml']['score'],)).fetchone()[0]
    assert stored[2]==expected_numeric, {
        'stored_numeric':str(stored[2]), 'expected_numeric':str(expected_numeric),
        'raw_score':repr(expected_live['ml']['score'])}
    assert conn.execute('SELECT count(*) FROM data_feature_materializations WHERE feature_set_id=%s',(spec['featureSetId'],)).fetchone()[0]==0;conn.commit()
    for artifact in ['fitPath','modelPath']:
        original=Path(ref[artifact]).read_bytes()
        try:
            Path(ref[artifact]).write_bytes(original+b'tamper')
            assert compute(context,live_raw,service.artifacts)['ml']['status']!='succeeded'
        finally: Path(ref[artifact]).write_bytes(original)
    evalspec={'kind':'evaluate','sourceId':spec['sourceId'],'configVersionId':str(config_id),'snapshotId':result['snapshotId'],'purpose':'validation'}
    evaluation_id=request('/api/jobs',evalspec,str(uuid.uuid4()))[1]['id']
    service.worker()
    outcome=state(conn,evaluation_id)
    assert outcome[0]=='succeeded',outcome[:3]
    assert outcome[1]['populationCount']==150 and outcome[1]['scoredCount']==150
    test_id=request('/api/jobs',{**evalspec,'purpose':'test'},str(uuid.uuid4()))[1]['id']
    service.worker()
    test=state(conn,test_id)
    assert test[0]=='succeeded' and test[1]['scoredCount']==0 and test[1]['unavailableCount']==150
    assert test[1]['applicability']=='unmeasured'
    print('PASS API/PG/Trainer/evaluation, train-only fit/Detector/hash/unknown, active unchanged',flush=True)

    # Evaluation insertion is rolled back if final job/result publication fails.
    evaluation_failure=request('/api/jobs',evalspec,str(uuid.uuid4()))[1]['id']
    with patch.dict(os.environ,service.env), patch('trainer.runner.load_dotenv'), \
            patch('trainer.service_contract.enrich_result',side_effect=RuntimeError('fixture publication failure')):
        runner.run(once=True)
    assert state(conn,evaluation_failure)[0]=='queued'
    assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(evaluation_failure,)).fetchone()[0]==0;conn.commit()
    retry_now(conn,evaluation_failure);service.worker()
    assert state(conn,evaluation_failure)[0]=='succeeded'
    enqueue(conn,'training',{'schemaVersion':1,'jobId':evaluation_failure});conn.commit();service.worker()
    assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(evaluation_failure,)).fetchone()[0]==1;conn.commit()

    # A real transaction rolls back after the first artifact registry insert.
    failed_id=request('/api/jobs',spec,str(uuid.uuid4()))[1]['id']
    original_register=runner._register_artifact
    calls=[]
    def fail_second(*args,**kwargs):
        calls.append(kwargs['kind'])
        if len(calls)==2: raise RuntimeError('fixture interruption during artifact registration')
        return original_register(*args,**kwargs)
    with patch.dict(os.environ,service.env), patch('trainer.runner.load_dotenv'), patch('trainer.runner._register_artifact',side_effect=fail_second):
        runner.run(once=True)
    assert state(conn,failed_id)[0]=='queued'
    assert conn.execute('SELECT count(*) FROM ml_artifacts WHERE created_job_id=%s',(failed_id,)).fetchone()[0]==0;conn.commit()
    retry_now(conn,failed_id);service.worker()
    recovered=state(conn,failed_id)
    assert recovered[0]=='succeeded' and recovered[3]==2,recovered[:4]
    assert conn.execute('SELECT count(*) FROM ml_artifacts WHERE created_job_id=%s',(failed_id,)).fetchone()[0]==2;conn.commit()
    enqueue(conn,'training',{'schemaVersion':1,'jobId':failed_id});conn.commit();service.worker()
    assert state(conn,failed_id)[3]==2

    # Simulated process crash after claim: old owner cannot finalize after lease expiry.
    restart_id=request('/api/jobs',spec,str(uuid.uuid4()))[1]['id']
    message=claim(conn,'training',lease_seconds=60,job_kinds=('train','evaluate'));conn.commit()
    claimed=runner._claim_job(conn,message,'fixture-crashed');conn.commit()
    oldjob,oldattempt,oldtoken=claimed
    conn.execute("UPDATE powercode_queue.training_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(message.id,))
    conn.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(oldattempt,));conn.commit()
    try:
        runner._finish(conn,message,oldjob,oldattempt,oldtoken,{},None,True)
    except RuntimeError as exc: assert str(exc)=='training_ownership_lost'
    else: raise AssertionError('expired owner finalized')
    conn.rollback();service.worker()
    restarted=state(conn,restart_id)
    assert restarted[0]=='succeeded' and restarted[3]==2,restarted[:4]
    assert conn.execute('SELECT * FROM ops_system_state').fetchall()==before;conn.commit()
    print('PASS artifact/evaluation rollback+retry, duplicate finalization blocked, expired owner/restart, active unchanged',flush=True)
    return {'sourceId':spec['sourceId'],'trainJobId':identifier,'configVersionId':str(config_id),'snapshotId':result['snapshotId'],'evaluationJobId':evaluation_id}


def main():
    dsn=os.environ['PIPELINE_TEST_DATABASE_URL']
    with tempfile.TemporaryDirectory(prefix='powercode-trainer56-') as directory:
        service=Service(directory,dsn)
        try:
            with psycopg.connect(dsn) as conn:
                verify(service,conn,seed(conn))
        finally: service.close()


if __name__=='__main__':main()
