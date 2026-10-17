"""Independent synthetic service QA against a fresh loopback qa_synthetic_service DB.

Uses only controlled T/P observations, real IF/fit artifacts and temporary accounts.
Set PIPELINE_TEST_DATABASE_URL and build server/backend before execution.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from unittest.mock import patch

import joblib
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector.storage import ingest_observation
from integration_research_pipeline import source,observe
from integration_trainer_service_contract import Service,state,retry_now
from trainer.catalog import register_feature_set
from trainer.snapshot import build_snapshot
from trainer import runner
from detector.computation import load_context,compute,model_reference
from pg_queue import claim,enqueue

REPO=Path(__file__).resolve().parents[3]
PROFILE='esb-process-research-v1'
FIELD={'op':'field','field':'P.total_count'}
DEFINITIONS=[
 {'name':'count_raw','expression':FIELD},
 {'name':'count_centered','expression':{'op':'group_centered','groupBy':'P.adapter_type','value':FIELD}},
 {'name':'count_gap','expression':{'op':'subtract','left':FIELD,'right':{'op':'add','left':{'op':'field','field':'P.success_count'},'right':{'op':'field','field':'P.error_count'}}}},
 {'name':'duration_ms','expression':{'op':'derived','ast':{'op':'datetime_diff','unit':'ms','args':[{'op':'column','table':'EC_PROCESS_MONITORING','column':'END_TIME'},{'op':'column','table':'EC_PROCESS_MONITORING','column':'START_TIME'}]},'outputType':'number','unit':'ms','provenance':{'kind':'controlled_synthetic_test'}}},
]

def put(service,path,body):
 headers={'Content-Type':'application/json','Authorization':'Bearer '+service.token}
 request=Request(service.base+path,data=json.dumps(body).encode(),headers=headers,method='PUT')
 try:
  with urlopen(request,timeout=30) as response:return response.status,json.load(response)
 except HTTPError as response:return response.code,json.load(response)

def source_hash(conn,source_id):
 rows=conn.execute('SELECT id,layer,content_hash,allowed_values,quality_flags FROM data_observations WHERE source_id=%s ORDER BY id',(source_id,)).fetchall()
 members=conn.execute('SELECT m.snapshot_id,m.observation_id,m.split,m.label_evidence FROM data_snapshot_members m JOIN data_snapshots s ON s.id=m.snapshot_id WHERE s.source_id=%s ORDER BY m.snapshot_id,m.observation_id',(source_id,)).fetchall()
 conn.commit()
 return hashlib.sha256(json.dumps([rows,members],default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def seed(conn):
 """1000 independent execution groups; source timestamps and persisted_at differ."""
 sid=source(conn,'Issue60 independent controlled research fixture')
 prefix=str(uuid.uuid4())
 persisted_start=datetime.now(timezone.utc)-timedelta(hours=2)
 raw_start=datetime(2026,1,1,tzinfo=timezone(timedelta(hours=9)))
 for index in range(1000):
  started=raw_start+timedelta(seconds=index)
  total=20+index%13
  raw={'transaction_id':f'{prefix}-tx-{index}','process_hub_id':'fixture-hub','retry_count':0,
       'process_id':'process','depend_process_id':None,'adapter_type':'A' if index%2 else 'B',
       'channel_id':'fixture-channel','status':'S','response_code':'000',
       'start_time_raw':started.isoformat(),'end_time_raw':(started+timedelta(milliseconds=100+index%5)).isoformat(),
       'total_count':total,'success_count':total,'error_count':0}
  if index<6:raw['status']=['F','P','N'][index%3]
  if index in range(760,780) or index in range(910,930):raw['adapter_type']='UNSEEN'
  parent={'transaction_id':raw['transaction_id'],'process_hub_id':raw['process_hub_id'],'retry_count':0,
          'interface_id':'fixture-interface','status':'S','start_time_raw':started.isoformat(),
          'end_time_raw':(started+timedelta(seconds=1)).isoformat()}
  if index not in (780,930):
   parent_execution=uuid.uuid4()
   conn.execute('''INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at) VALUES(%s,%s,'T',%s,'{}'::jsonb,'controlled-v1',clock_timestamp())''',(parent_execution,sid,f'{prefix}-parent-{index}'))
   result=ingest_observation(conn,source_id=sid,layer='T',dedup_key=f'{prefix}-parent-{index}',raw=parent,execution_id=parent_execution,revision_no=1)
   conn.execute('UPDATE data_observations SET persisted_at=%s WHERE id=%s',(persisted_start+timedelta(seconds=index,microseconds=-1),result.observation_id))
  observe(conn,sid,f'{prefix}-p-{index}',raw,label='anomaly' if index in (10,11) else None,persisted=persisted_start+timedelta(seconds=index))
  if index==700:
   external={**raw,'process_id':'external-dependency','depend_process_id':'process',
             'start_time_raw':(started+timedelta(milliseconds=110)).isoformat(),
             'end_time_raw':(started+timedelta(milliseconds=200)).isoformat()}
   observe(conn,sid,f'{prefix}-outside-request-window',external,persisted=persisted_start-timedelta(seconds=2))
 features=register_feature_set(conn,DEFINITIONS,'independent-qa',definition_kind='operator_defined')
 rule_id=uuid.uuid4()
 rule={'schemaVersion':1,'state':'draft','condition':{'field':'F.count_gap','operator':'gt','value':0},
       'outcome':{'severity':'warning','decisionCode':'COUNT_RESEARCH'}}
 conn.execute('''INSERT INTO ml_rule_versions(id,rule_key,version,rule_spec,input_spec,evidence_spec,content_hash,created_by_actor_id,created_at)
                 VALUES(%s,%s,1,%s::jsonb,%s::jsonb,'{}'::jsonb,%s,'independent-qa',clock_timestamp())''',
              (rule_id,prefix+'-research-count',json.dumps(rule),json.dumps({'featureSetId':str(features)}),hashlib.sha256(json.dumps(rule).encode()).hexdigest()))
 conn.commit()
 return {'kind':'train','sourceId':str(sid),'featureSetId':str(features),'ruleVersionIds':[str(rule_id)],
         'from':(persisted_start-timedelta(seconds=1)).isoformat().replace('+00:00','Z'),'until':(persisted_start+timedelta(seconds=1001)).isoformat().replace('+00:00','Z'),
         'detectionPolicy':{'mode':'parallel','deadlineMs':30000,'combination':'any_anomaly'}}

def submit(service,spec):
 status,payload=service.request('/api/jobs',spec,service.token,str(uuid.uuid4()))
 assert status==201,{'status':status,'response':payload}
 return payload['id']

def registration(service,source_id,profile=PROFILE):
 status,payload=put(service,f'/api/jobs/sources/{source_id}/synthetic-profiles',
                    {'profileId':profile,'profileVersion':'1','sourceSchemaVersion':'v1'})
 assert status in (200,201),{'status':status,'response':payload}

def artifact(service,family):
 reference=family['details']['manifestRef']
 path=(service.artifacts/reference['relativePath']).resolve(strict=True)
 assert path.is_relative_to(service.artifacts.resolve())
 payload=path.read_bytes()
 assert hashlib.sha256(payload).hexdigest()==reference['sha256']
 assert len(payload)==reference['byteSize']
 return json.loads(payload)

def manual_metrics(truth,before,after,path):
 pairs=[(before[row['rowId']].get(path),after.get(row['rowId'],{}).get(path)) for row in truth]
 count=len(pairs)
 detected=sum(post is True for pre,post in pairs)
 pending=path=='sr' and all(post is None for pre,post in pairs)
 return {'injectedCount':count,'detectedCount':detected,
         'unjudgedCount':sum(post is None for pre,post in pairs),
         'detectionRate':detected/count if count and not pending else None,
         'baselineAlertCount':sum(pre is True for pre,post in pairs),
         'newlyDetectedCount':sum(pre is False and post is True for pre,post in pairs),
         'persistentAlertCount':sum(pre is True and post is True for pre,post in pairs),
         'lostAlertCount':sum(pre is True and post is False for pre,post in pairs),
         'unchangedNonalertCount':sum(pre is False and post is False for pre,post in pairs),
         'pairedUnjudgedCount':sum(pre is None or post is None for pre,post in pairs)}

def verify_esb(service,conn,trained,spec,result):
 from trainer.synthetic_metrics import prediction
 from trainer.synthetic_profiles import digest
 import jsonschema
 family=result['evaluationEnvelope']['synthetic']
 cost=family['details']['syntheticProcessingCost']
 assert cost['unit']=='ms' and cost['rowUnit']=='PROCESS' and cost['originalBaselineIncluded'] is False
 assert cost['totalMs']>=0 and cost['validationGenerationScoringMs']>=0 and cost['storageMs']>=0
 assert abs(cost['totalMs']-cost['validationGenerationScoringMs']-cost['storageMs'])<1e-5
 assert result['executionCost']['details']['scope']=='offline_original_snapshot_only'
 assert result['executionCost']['details']['syntheticProcessingIncluded'] is False
 jsonschema.validate(result['evaluationEnvelope'],json.loads((REPO/'server/contracts/trainer-evaluation-envelope.schema.json').read_text()))
 assert family==result['syntheticEvaluation'] and family['status']=='measured'
 assert result['applicability']=='unmeasured'
 assert 'synthetic_not_operational_validation' in result['reasonCodes']
 document=artifact(service,family)
 assert document['profileHash']==digest(document['profile'])
 assert document['snapshot']['snapshotId'].lower()==spec['snapshotId'].lower()
 manifest=document['modelManifest']
 assert manifest['modelHash']==trained['modelManifest']['modelHash']
 assert manifest['fitHash']==trained['modelManifest']['fitHash']
 assert manifest['trainingSnapshotId']==trained['snapshotId']
 assert manifest['featureNames']==[item['name'] for item in DEFINITIONS]
 assert document['latency']=={'value':None,'reason':'static_evaluation_not_ESB_latency_measurement'}
 assert 'syntheticProcessingCost' not in document and 'syntheticProcessingCost' not in document['result']
 context=load_context(conn,uuid.UUID(trained['configVersionId']),uuid.UUID(spec['sourceId']));conn.commit()
 data=document['result'];base={row['rowId']:row for row in data['baseRows']}
 assert data['baseRowsHash']==digest(data['baseRows'])
 assert data['parentContextHash']==digest(data['parentContext'])
 assert data['generatorFitHash']==digest(data['generatorFit'])
 expected_baseline={key:prediction(compute(context,row['raw'],service.artifacts)) for key,row in base.items()}
 assert data['baselinePredictions']==expected_baseline
 for trial in data['trials']:
  assert trial['parentChanges']==[]
  assert trial['seed']==spec['syntheticEvaluation']['seed']+trial['repetition']-1
  assert {item['field'] for item in trial['changes']}<=({'total_count'} if trial['scenario']=='P04' else {'end_time_raw'})
  reconstructed=copy.deepcopy(base)
  for change in trial['changes']:
   raw=reconstructed[change['rowId']]['raw'];assert raw[change['field']]==change['before'];raw[change['field']]=change['after']
  for truth in trial['truth']:
   assert truth['label']==1 and truth['parentId']==base[truth['rowId']]['parent']['rowId']
   expected=prediction(compute(context,reconstructed[truth['rowId']]['raw'],service.artifacts))
   assert expected==trial['predictions'][truth['rowId']]
   if trial['scenario']=='P04':
    assert expected['hr'] is False, 'research count Rule must not replace status HR'
    assert expected['combined'] is True
  for path in ('hr','sr','ml','combined'):
   expected=manual_metrics(trial['truth'],expected_baseline,trial['predictions'],path)
   for name,value in expected.items():assert trial['metrics'][path][name]==value,(trial['scenario'],path,name)
   assert trial['metrics'][path]['precision'] is None and trial['metrics'][path]['falsePositiveRate'] is None
  assert trial['metrics']['sr']['detectionRate'] is None
  assert trial['metrics']['sr']['reason']=='sr_experiment_pending'
 total=sum(len(trial['truth']) for trial in data['trials'])
 assert family['metrics']['generated']['value']==total
 assert family['metrics']['srDetectionRate']['value'] is None
 assert family['metrics']['srDetectionRate']['denominator']==total
 assert family['metrics']['srDetectionRate']['reason']=='sr_experiment_pending'
 assert family['details']['uniqueInjectedProcessCount']==len({r['rowId'] for trial in data['trials'] for r in trial['truth']})
 assert len(data['trials'])==len(spec['syntheticEvaluation']['scenarios'])*spec['syntheticEvaluation']['repetitions']
 return document

def verify_adbench(service,trained,result):
 from trainer.synthetic_profiles import digest
 family=result['evaluationEnvelope']['synthetic'];document=artifact(service,family);data=document['result']
 assert family['details']['unit']=='numeric_feature_row'
 assert family['details']['syntheticProcessingCost']['rowUnit']=='numeric_feature_row'
 assert family['details']['syntheticProcessingCost']['originalBaselineIncluded'] is False
 assert 'hrDetectionRate' not in family['metrics']
 assert data['truthMeaning']=='generator_distribution_membership_not_ESB_fault'
 assert data['testRowsUsedForFit'] is False
 assert data['parentRelations']=='not_applicable_numeric_rows'
 assert data['featureNames']==trained['modelManifest']['featureNames']
 assert len(data['generatorReferenceIds'])==692
 for trial in data['trials']:
  assert 'truth' not in trial and 'parentChanges' not in trial
  assert trial['matrixHash']==digest(trial['values']) and trial['labelHash']==digest(trial['labels'])
  assert len(trial['values'])==len(trial['labels'])==len(trial['predictions'])
  for label in (0,1):
   count=sum(value==label for value in trial['labels'])
   hits=sum(value==label and predicted for value,predicted in zip(trial['labels'],trial['predictions']))
   assert trial['metrics'][str(label)]=={'rows':count,'predictedAnomalyCount':hits,'predictedAnomalyRate':hits/count}
 return document

def run_worker(service):
 with patch.dict(os.environ,service.env),patch('trainer.runner.load_dotenv'):
  runner.run(once=True)

def verify_context(conn,trained,source_id):
 from trainer.synthetic_snapshot import rows_for_generation
 from trainer.synthetic_generation import generate,fit_generator
 from trainer.synthetic_profiles import PROFILES
 snapshot_id=trained['snapshotId']
 snapshot=conn.execute('SELECT observation_cutoff_at,manifest_hash FROM data_snapshots WHERE id=%s',(snapshot_id,)).fetchone()
 rows=conn.execute('''SELECT m.observation_id,o.allowed_values,m.split,m.label_evidence FROM data_snapshot_members m
  JOIN data_observations o ON o.id=m.observation_id WHERE m.snapshot_id=%s AND m.split='validation'
  ORDER BY o.persisted_at LIMIT 1''',(snapshot_id,)).fetchall()
 # A later parent revision cannot replace the immutable cutoff's chosen parent.
 parent=conn.execute('''SELECT id,execution_id,allowed_values,revision_no FROM data_observations
  WHERE source_id=%s AND layer='T' AND allowed_values->>'transaction_id'=%s''',(source_id,rows[0][1]['transaction_id'])).fetchone()
 future_raw={**parent[2],'end_time_raw':parent[2]['start_time_raw']}
 future=ingest_observation(conn,source_id=uuid.UUID(source_id),layer='T',dedup_key='future-'+str(uuid.uuid4()),
  raw=future_raw,execution_id=parent[1],revision_no=parent[3]+1)
 conn.execute('UPDATE data_observations SET persisted_at=%s WHERE id=%s',(snapshot[0]+timedelta(seconds=1),future.observation_id));conn.commit()
 base,context=rows_for_generation(conn,rows,{'sourceId':source_id},{'cutoff':snapshot[0].isoformat()});conn.commit()
 assert base[0]['parent']['rowId']==str(parent[0])
 assert str(future.observation_id) not in {row['rowId'] for row in context['parents']}
 cohort={str(row[0]) for row in rows}
 assert any(row['rowId'] not in cohort and row['raw'].get('depend_process_id')=='process' for row in context['siblings'])
 trained_rows=conn.execute('''SELECT o.allowed_values FROM data_snapshot_members m JOIN data_observations o ON o.id=m.observation_id
  WHERE m.snapshot_id=%s AND m.split='train' AND o.allowed_values->>'status'='S' AND coalesce(m.label_evidence->>'label','')!='anomaly' ''',(snapshot_id,)).fetchall();conn.commit()
 fit=fit_generator([{'raw':row[0],'split':'train'} for row in trained_rows])
 rejected=generate(base,fit,PROFILES[PROFILE],scenario='P02',severity=1,seed=1,target_rows=1,context_siblings=context['siblings'])
 assert rejected['generatedCount']==0 and any(row['reason']=='insufficient_parent_or_dependency_headroom' for row in rejected['rejects'])

def count_evaluations(conn,identifier):
 value=conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(identifier,)).fetchone()[0];conn.commit();return value

def verify_failures(service,conn,evalspec,reference):
 from trainer import synthetic_execution
 single={**evalspec,'syntheticEvaluation':{**evalspec['syntheticEvaluation'],'scenarios':[{'scenario':'P04','severity':1}],'repetitions':1,'targetRows':2}}
 # An admitted request with no valid contiguous block must not publish success.
 impossible={**single,'syntheticEvaluation':{**single['syntheticEvaluation'],'scenarios':[{'scenario':'P03','severity':1}],'targetRows':100}}
 identifier=submit(service,impossible);service.worker()
 status,result,error,attempt=state(conn,identifier)
 assert status=='failed' and result is None and error=='synthetic_generation_failed' and count_evaluations(conn,identifier)==0
 # A persisted older/transport request still receives the worker's P01 refusal.
 identifier=str(uuid.uuid4());legacy=copy.deepcopy(single)
 legacy['syntheticEvaluation']['scenarios']=[{'scenario':'P01','severity':1}]
 catalog=conn.execute('SELECT field_catalog FROM ops_sources WHERE id=%s',(single['sourceId'],)).fetchone()[0]
 legacy['syntheticProfileBinding']=catalog['syntheticProfiles'][PROFILE]
 conn.execute("INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,created_at) VALUES(%s,'evaluate','queued','independent-qa','admin',%s,%s::jsonb,3,0,clock_timestamp(),clock_timestamp())",(identifier,hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).hexdigest(),json.dumps(legacy)))
 enqueue(conn,'training',{'schemaVersion':1,'jobId':identifier});conn.commit()
 service.worker();status,result,error,attempt=state(conn,identifier)
 assert status=='failed' and result is None and error=='synthetic_unsupported' and count_evaluations(conn,identifier)==0
 # Invalid persisted model manifests are rejected even when file hashes are valid.
 config=conn.execute('SELECT source_id,configuration_spec FROM ml_config_versions WHERE id=%s',(single['configVersionId'],)).fetchone()
 for defect,expected in [('training_snapshot','synthetic_model_fit_lineage_invalid'),('feature_order','model_feature_schema_mismatch')]:
  clone=uuid.uuid4();config_spec=copy.deepcopy(config[1])
  if defect=='training_snapshot':config_spec['trainingSnapshotId']=single['snapshotId']
  conn.execute("INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at) VALUES(%s,%s,%s::jsonb,'{}'::jsonb,%s,'independent-qa',clock_timestamp())",(clone,config[0],json.dumps(config_spec),hashlib.sha256(json.dumps(config_spec,sort_keys=True).encode()).hexdigest()))
  settings=conn.execute('SELECT training_settings FROM ml_config_models WHERE config_version_id=%s',(single['configVersionId'],)).fetchone()[0]
  if defect=='feature_order':settings['featureNames']=list(reversed(settings['featureNames']))
  conn.execute('''INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,training_settings,threshold_spec)
   SELECT %s,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,%s::jsonb,threshold_spec FROM ml_config_models WHERE config_version_id=%s''',(clone,json.dumps(settings),single['configVersionId']))
  conn.commit();identifier=submit(service,{**single,'configVersionId':str(clone)});service.worker()
  status,result,error,attempt=state(conn,identifier)
  assert status=='failed' and result is None and error==expected and count_evaluations(conn,identifier)==0,(defect,status,error)
 # A file may exist before publication; a failing transaction exposes no evaluation.
 publication=submit(service,single)
 with patch('trainer.service_contract.enrich_result',side_effect=RuntimeError('controlled publication interruption')):run_worker(service)
 assert state(conn,publication)[0]=='queued' and count_evaluations(conn,publication)==0
 files=list((service.artifacts/'evaluations'/publication).glob('*.json'));assert len(files)==1
 stored_payload=files[0].read_bytes()
 retry_now(conn,publication);service.worker()
 status,recovered,error,attempt=state(conn,publication)
 assert status=='succeeded' and attempt==2 and count_evaluations(conn,publication)==1
 assert files[0].read_bytes()==stored_payload
 enqueue(conn,'training',{'schemaVersion':1,'jobId':publication});conn.commit();service.worker()
 assert state(conn,publication)[3]==2 and count_evaluations(conn,publication)==1
 # Lose both leases immediately after storing the real result file.
 lost=submit(service,single);original_store=synthetic_execution.store
 def store_then_expire(*args,**kwargs):
  value=original_store(*args,**kwargs)
  with psycopg.connect(service.dsn) as other:
   other.execute("UPDATE powercode_queue.training_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE payload->>'jobId'=%s",(lost,))
   other.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s AND status='running'",(lost,))
  return value
 with patch('trainer.synthetic_execution.store',side_effect=store_then_expire):run_worker(service)
 assert state(conn,lost)[0]=='running' and state(conn,lost)[1] is None and count_evaluations(conn,lost)==0
 service.worker()
 status,restarted,error,attempt=state(conn,lost)
 assert status=='succeeded' and attempt==2 and count_evaluations(conn,lost)==1
 # Simulate a process exit after claim, then prevent its stale finalization.
 crashed=submit(service,single)
 message=claim(conn,'training',lease_seconds=60,job_kinds=('train','evaluate'));conn.commit()
 job,attempt_id,token=runner._claim_job(conn,message,'controlled-crashed-worker');conn.commit()
 assert str(job['id'])==crashed
 conn.execute("UPDATE powercode_queue.training_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(message.id,))
 conn.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(attempt_id,));conn.commit()
 try:runner._finish(conn,message,job,attempt_id,token,{},None,True)
 except RuntimeError as exc:assert str(exc)=='training_ownership_lost'
 else:raise AssertionError('stale lease published result')
 conn.rollback();service.worker()
 assert state(conn,crashed)[0]=='succeeded' and state(conn,crashed)[3]==2 and count_evaluations(conn,crashed)==1
 # Both frozen artifacts are checked at execution, not only request admission.
 for name in ['modelPath','fitPath']:
  identifier=submit(service,single);path=Path(reference[name]);original=path.read_bytes()
  try:
   path.write_bytes(original+b'controlled-tamper');service.worker()
   status,result,error,attempt=state(conn,identifier)
   assert status=='failed' and result is None and count_evaluations(conn,identifier)==0
  finally:path.write_bytes(original)
 # Worker rechecks an invalidated pinned source binding even after API admission.
 identifier=submit(service,single)
 catalog=conn.execute('SELECT field_catalog FROM ops_sources WHERE id=%s',(single['sourceId'],)).fetchone()[0]
 changed=copy.deepcopy(catalog);changed['syntheticProfiles'][PROFILE]['staticSpecHash']='0'*64
 conn.execute('UPDATE ops_sources SET field_catalog=%s::jsonb WHERE id=%s',(json.dumps(changed),single['sourceId']));conn.commit()
 try:
  service.worker();status,result,error,attempt=state(conn,identifier)
  assert status=='failed' and result is None and error=='synthetic_source_profile_mismatch' and count_evaluations(conn,identifier)==0
 finally:
  conn.execute('UPDATE ops_sources SET field_catalog=%s::jsonb WHERE id=%s',(json.dumps(catalog),single['sourceId']));conn.commit()
 print('PASS publication rollback/retry, content reuse, duplicate finalization, lease loss/restart, model/fit hash and source-binding rejection',flush=True)
def main():
 dsn=os.environ['PIPELINE_TEST_DATABASE_URL'];parsed=urlsplit(dsn)
 assert parsed.hostname in {'127.0.0.1','localhost'} and parsed.path=='/qa_synthetic_service'
 with tempfile.TemporaryDirectory(prefix='powercode-synthetic60-') as directory:
  service=Service(directory,dsn)
  try:
   with psycopg.connect(dsn) as conn:
    training_spec=seed(conn)
    original_active=conn.execute('SELECT * FROM ops_system_state').fetchall();conn.commit()
    trained_id=submit(service,training_spec);service.worker()
    status,trained,error,attempt=state(conn,trained_id)
    assert status=='succeeded',(status,error)
    assert trained['splitCounts']=={'train':700,'validation':150,'test':150}
    assert trained['executionCost']['details']['scope']=='offline_training'
    assert trained['dataContract']['trainingSelection']['selectedCount']==692
    registration(service,training_spec['sourceId']);registration(service,training_spec['sourceId'],'adbench-numeric-v1')
    verify_context(conn,trained,training_spec['sourceId'])
    range_start=datetime.fromisoformat(training_spec['from'].replace('Z','+00:00'))+timedelta(seconds=721)
    evaluation_snapshot=build_snapshot(conn,source_id=uuid.UUID(training_spec['sourceId']),
     from_time=range_start.isoformat(),until_time=(range_start+timedelta(seconds=80)).isoformat(),actor_id='independent-qa')
    assert evaluation_snapshot.counts=={'train':56,'validation':12,'test':12}
    original=source_hash(conn,training_spec['sourceId'])
    context=load_context(conn,uuid.UUID(trained['configVersionId']),uuid.UUID(training_spec['sourceId']));conn.commit()
    reference=model_reference(context,service.artifacts)
    files={name:Path(reference[name]).read_bytes() for name in ['modelPath','fitPath']}
    preprocessing=joblib.load(reference['fitPath'])['preprocessing']
    assert sum(preprocessing['features']['count_centered']['referenceCounts'].values())==692
    evalspec={'kind':'evaluate','sourceId':training_spec['sourceId'].upper(),'snapshotId':str(evaluation_snapshot.id).upper(),
              'configVersionId':trained['configVersionId'].upper(),'purpose':'validation',
              'syntheticEvaluation':{'profileId':PROFILE,'profileVersion':'1','scenarios':[{'scenario':'P02','severity':1},{'scenario':'P03','severity':2},{'scenario':'P04','severity':3}],
                                     'seed':11,'repetitions':2,'targetRows':3}}
    original_eval=submit(service,{key:value for key,value in evalspec.items() if key!='syntheticEvaluation'});service.worker()
    status,legacy,error,attempt=state(conn,original_eval);assert status=='succeeded',(status,error)
    assert legacy['executionCost']['details']['scope']=='offline_snapshot'
    assert 'syntheticProcessingIncluded' not in legacy['executionCost']['details']
    assert legacy['evaluationEnvelope']['synthetic']['status']=='unavailable'
    evaluation_id=submit(service,evalspec);service.worker()
    status,result,error,attempt=state(conn,evaluation_id);assert status=='succeeded',(status,error)
    assert service.request('/api/jobs/'+evaluation_id,token=service.token)[1]['resultSummary']==result
    first=verify_esb(service,conn,trained,evalspec,result)
    repeated_id=submit(service,evalspec);service.worker()
    status,repeated,error,attempt=state(conn,repeated_id);assert status=='succeeded',(status,error)
    second=verify_esb(service,conn,trained,evalspec,repeated)
    assert first==second,'same snapshot/settings/seed semantic artifact differs'
    assert result['evaluationEnvelope']['synthetic']['details']['manifestRef']['sha256']==repeated['evaluationEnvelope']['synthetic']['details']['manifestRef']['sha256']
    adbench_spec={**evalspec,'syntheticEvaluation':{'profileId':'adbench-numeric-v1','profileVersion':'1',
      'scenarios':[{'scenario':'global','severity':1}], 'seed':23,'repetitions':1,'targetRows':3}}
    numeric_id=submit(service,adbench_spec);service.worker()
    status,numeric,error,attempt=state(conn,numeric_id);assert status=='succeeded',(status,error)
    verify_adbench(service,trained,numeric)
    verify_failures(service,conn,evalspec,reference)
    assert source_hash(conn,training_spec['sourceId'])==original
    assert conn.execute('SELECT * FROM ops_system_state').fetchall()==original_active;conn.commit()
    for name,payload in files.items():assert Path(reference[name]).read_bytes()==payload
    print('PASS real IF/train-only fit, ESB raw-copy/paired/SR-null, repeated artifact, ADBench separate numeric rows, source/active/model immutable',flush=True)
  finally:service.close()

if __name__=='__main__':main()
