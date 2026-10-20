"""Independent completed CSV checks on a fresh, loopback-only QA database."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
from unittest.mock import patch

import psycopg
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration_trainer_service_contract import Service
from integration_research_pipeline import source
from trainer.prepared_contract import digest
from trainer import runner, prepared_import
from pg_queue import enqueue
from trainer.prepared_parser import parse_csv, training_eligible
from detector.applications import prepare_config, process_application_once
from jsonschema import validate


def manifest(source_id, count=3):
    return {'contractVersion': 'prepared-csv-v1', 'sourceId': str(source_id), 'sourceSchemaVersion': 'v1',
            'domain': '통제 CSV 🔬', 'rowCount': count, 'inputStage': 'model_input',
            'features': [{'name': 'startTimeHourSin', 'type': 'number', 'unit': 'ratio'}],
            'preprocessing': {'definitionHash': hashlib.sha256(b'identity:v1').hexdigest(),
                              'scaling': 'none', 'fitHash': None, 'fitSplit': 'unknown', 'trainRowIdsHash': None},
            'split': {'version': 'provided-v1', 'timeBasis': 'unspecified'},
            'columns': {'rowId': 'row_id', 'split': 'split', 'label': 'label', 'status': 'status',
                        'isSynthetic': 'is_synthetic', 'scenario': None}}


def admission(service, conn):
    preflight=Request(service.base+'/api/prepared-datasets',method='OPTIONS',headers={
        'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST',
        'Access-Control-Request-Headers':'content-type,authorization,idempotency-key'})
    with urlopen(preflight,timeout=10) as response:
        assert response.status==204
        allowed={value.strip().lower() for value in response.headers['Access-Control-Allow-Headers'].split(',')}
        assert {'content-type','authorization','idempotency-key'}<=allowed
    sid = source(conn, 'Issue62 independent completed CSV')
    csv = ('row_id,split,label,status,is_synthetic,startTimeHourSin\n'
           'r1,train,normal,S,false,0.25\n'
           'r2,validation,normal,S,false,0.3\n'
           'r3,test,anomaly,F,true,9.8\n')
    request = lambda body, token=service.token, key=None: service.request('/api/prepared-datasets', body, token, key)
    base = {'csvText': csv, 'manifest': manifest(sid)}
    assert request(base, token=None)[0] == 401
    assert request(base, token=service.user)[0] == 403
    identifiers = []
    for intensity in (0.0001, 1e-5, 1e-7, 5e-324, 1.0, 0):
        body = copy.deepcopy(base)
        body['manifest']['sourceId'] = str(sid).upper()
        body['manifest']['synthetic'] = {'generator': '독립 생성기 🔬', 'version': 'v1', 'seed': 42,
                                          'intensity': intensity, 'baselineReference': '통제 원본'}
        key = str(uuid.uuid4())
        code, created = request(body, key=key)
        assert code == 201, (code, created)
        row = conn.execute('SELECT manifest,manifest_hash,original_hash,original_relative_path,created_job_id '
                           'FROM data_prepared_datasets WHERE id=%s', (created['id'],)).fetchone()
        assert digest(row[0]) == row[1], {'intensity': intensity, 'reason': 'cross_runtime_manifest_hash'}
        original = service.artifacts / row[3]
        assert original.read_bytes() == csv.encode()
        assert hashlib.sha256(original.read_bytes()).hexdigest() == row[2]
        assert str(row[0]['sourceId']) == str(sid)
        assert request(body, key=key) == (200, created)
        changed = copy.deepcopy(body); changed['csvText'] += '\n'
        assert request(changed, key=key)[0] == 409
        code, read = service.request('/api/prepared-datasets/' + created['id'], token=service.token)
        assert code == 200 and read['manifestHash'] == row[1] and read['status'] == 'queued'
        assert not any('path' in field.lower() for field in read)
        payload = conn.execute('SELECT input_spec FROM run_jobs WHERE id=%s', (row[4],)).fetchone()[0]
        assert payload == {'kind': 'import_csv', 'sourceId': str(sid), 'preparedDatasetId': created['id']}
        assert conn.execute("SELECT count(*) FROM powercode_queue.training_jobs WHERE payload->>'jobId'=%s",
                            (created['jobId'],)).fetchone()[0] == 1
        identifiers.append(created)
        conn.commit()
    bad = copy.deepcopy(base); bad['manifest']['sourceSchemaVersion'] = 'wrong'
    assert request(bad)[0] == 409
    bad = copy.deepcopy(base); bad['filePath'] = '../outside.csv'
    assert request(bad)[0] == 400
    print('PASS HTTP admin admission, six numeric/Unicode PG-to-Python hash cases, immutable bytes, '
          'idempotency conflict, metadata and queue binding', flush=True)
    for created in identifiers:
        service.worker()
        code, ready = service.request('/api/prepared-datasets/' + created['id'], token=service.token)
        assert code == 200 and ready['status'] == 'ready' and ready['jobStatus'] == 'succeeded', ready
        assert ready['rowCount'] == 3 and ready['snapshotId']
        row = conn.execute('SELECT normalized_relative_path,normalized_hash,row_metadata,original_relative_path '
                           'FROM data_prepared_datasets WHERE id=%s', (created['id'],)).fetchone()
        normalized = (service.artifacts / row[0]).read_bytes()
        assert hashlib.sha256(normalized).hexdigest() == row[1] == ready['normalizedHash']
        values = [json.loads(line) for line in normalized.splitlines()]
        assert [value['features'] for value in values] == [[.25], [.3], [9.8]]
        assert len(row[2]) == 3 and all('features' not in metadata for metadata in row[2])
        assert (service.artifacts / row[3]).read_bytes() == csv.encode()
        assert ready['validationResult']['operationalEligible'] is False
        assert conn.execute('SELECT status,schema_version FROM data_snapshots WHERE id=%s',
                            (ready['snapshotId'],)).fetchone() == ('ready', 'prepared-features-v1')
        conn.commit()
    print('PASS six actual leased imports, atomic dataset/snapshot ready and API results, unchanged features/bytes', flush=True)
    return base


def import_recovery(service, conn, body):
    request = lambda value, key=None: service.request('/api/prepared-datasets', value, service.token, key)
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: request(body, key), range(12)))
    assert sum(code == 201 for code, _ in responses) == 1
    assert all(code in {200, 201} for code, _ in responses)
    assert len({item['id'] for _, item in responses}) == 1
    raced = responses[0][1]
    service.worker()
    assert service.request('/api/prepared-datasets/'+raced['id'], token=service.token)[1]['status'] == 'ready'

    def submit(value=body):
        code, created = request(value, str(uuid.uuid4()))
        assert code == 201, (code, created)
        return created

    def get(created):
        conn.commit()
        return service.request('/api/prepared-datasets/'+created['id'], token=service.token)[1]

    malformed = copy.deepcopy(body)
    malformed['csvText'] = malformed['csvText'].replace('startTimeHourSin', 'unexpected')
    failed = submit(malformed); service.worker()
    state = get(failed)
    assert state['status'] == state['jobStatus'] == 'failed' and state['snapshotId'] is None
    assert state['errorCode'] == 'prepared_csv_header_mismatch'

    tampered = submit()
    path = service.artifacts/'prepared'/tampered['id']/'original.csv'
    path.write_bytes(path.read_bytes()+b'corruption')
    service.worker(); state = get(tampered)
    assert state['status'] == 'failed' and state['errorCode'] == 'prepared_artifact_hash_mismatch'
    assert state['snapshotId'] is None

    def run_once():
        with patch.dict(os.environ, service.env), patch('trainer.runner.load_dotenv'):
            runner.run(once=True)

    def retry(created):
        conn.execute("UPDATE powercode_queue.training_jobs SET available_at=clock_timestamp() WHERE payload->>'jobId'=%s",
                     (created['jobId'],))
        conn.execute('UPDATE run_jobs SET next_run_at=clock_timestamp() WHERE id=%s', (created['jobId'],))
        conn.commit(); service.worker()
        assert get(created)['status'] == 'ready'

    original_finish = runner._finish
    calls = []
    def fail_after_ack(*args, **kwargs):
        original_finish(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('controlled publication interruption')

    rolled_back = submit()
    with patch('trainer.runner._finish', side_effect=fail_after_ack):
        run_once()
    state = get(rolled_back)
    assert state['status'] == 'queued' and state['snapshotId'] is None and state['normalizedHash'] is None
    assert conn.execute("SELECT count(*) FROM data_snapshots WHERE selection_spec->>'preparedDatasetId'=%s",
                        (rolled_back['id'],)).fetchone()[0] == 0
    conn.commit(); retry(rolled_back)
    assert len(list((service.artifacts/'prepared'/rolled_back['id']).glob('*.jsonl'))) == 1

    lost = submit()
    original_store = prepared_import.store
    def expire_after_store(*args, **kwargs):
        stored = original_store(*args, **kwargs)
        with psycopg.connect(service.dsn) as owner:
            owner.execute("UPDATE powercode_queue.training_jobs SET lease_until=clock_timestamp()-interval '1 second' "
                          "WHERE payload->>'jobId'=%s", (lost['jobId'],))
            owner.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                          (lost['jobId'],))
        return stored
    with patch('trainer.prepared_import.store', side_effect=expire_after_store):
        run_once()
    state = get(lost)
    assert state['status'] == 'queued' and state['snapshotId'] is None
    service.worker()
    assert get(lost)['status'] == 'ready'
    assert conn.execute('SELECT count(*) FROM run_job_attempts WHERE job_id=%s', (lost['jobId'],)).fetchone()[0] == 2
    before = get(lost)['snapshotId']
    enqueue(conn, 'training', {'schemaVersion': 1, 'jobId': lost['jobId']}); conn.commit()
    service.worker()
    assert get(lost)['snapshotId'] == before
    assert conn.execute("SELECT count(*) FROM data_snapshots WHERE selection_spec->>'preparedDatasetId'=%s",
                        (lost['id'],)).fetchone()[0] == 1
    conn.commit()
    print('PASS twelve concurrent idempotent requests, terminal validation/hash errors, '
          'post-ACK rollback/retry, real lease expiry/reclaim and duplicate finalization', flush=True)


def completed_fixture(sid):
    prefix = uuid.uuid4().hex
    m = manifest(sid, 663)
    m['features'].append({'name': '_completedRatio', 'type': 'number', 'unit': 'ratio'})
    m['columns'].update(originRowId='origin', groupId='group', scenario='scenario')
    m['preprocessing'].update(scaling='applied', fitHash='b'*64, fitSplit='train',
                              trainRowIdsHash=digest(sorted(f'{prefix}-train-{i}' for i in range(650))))
    m['labelSource'] = {'kind': 'manual', 'reference': 'controlled-fixed-label-table'}
    m['synthetic'] = {'generator': 'controlled-import-only', 'version': 'v1', 'seed': 42,
                      'intensity': .5, 'baselineReference': 'independent completed inputs'}
    rows = []
    for i in range(650):
        rid = f'{prefix}-train-{i}'
        status, synthetic, label, scenario = 'S', 'false', 'unknown', ''
        if i in (0, 9): label = 'anomaly'
        if i == 1: synthetic = 'true'
        if i in (2, 3, 4, 5, 6): status = {2:'F', 3:'P', 4:'N', 5:'', 6:'unknown'}[i]
        if i == 7: synthetic = ''
        if i == 8: scenario = 'declared-injection'
        rows.append([rid,'train',label,status,synthetic,scenario,'origin-'+rid,'group-'+rid,
                     math.sin(i*.17),math.cos(i*.13)])
    for split,count in (('validation',8),('test',5)):
        for i in range(count):
            rid=f'{prefix}-{split}-{i}'
            value=50.0 if i%2 else 0.0
            label=('normal','anomaly','unknown','normal','anomaly','unknown','normal','anomaly')[i]
            rows.append([rid,split,label,'F' if i%2 else 'S','true' if i%2 else 'false',
                         'declared-tail' if i%2 else '', 'origin-'+rid,'group-'+rid,value,value])
    stream=io.StringIO(newline=''); writer=csv.writer(stream)
    writer.writerow([*m['columns'].values(),*[feature['name'] for feature in m['features']]])
    writer.writerows(rows)
    return {'csvText':stream.getvalue(),'manifest':m}


def worker_in_process(service):
    with patch.dict(os.environ, service.env), patch('trainer.runner.load_dotenv'):
        runner.run(once=True)


def submit_job(service, spec):
    code, created=service.request('/api/jobs',spec,service.token,str(uuid.uuid4()))
    assert code==201,(code,created)
    return created['id']


def job_result(service, identifier, *, status='succeeded'):
    code, job=service.request('/api/jobs/'+identifier,token=service.token)
    assert code==200 and job['status']==status,job
    return job


def training_and_metrics(service, conn):
    sid=source(conn,'Issue62 independent real prepared IF')
    body=completed_fixture(sid)
    code, created=service.request('/api/prepared-datasets',body,service.token,str(uuid.uuid4()))
    assert code==201,(code,created)
    service.worker()
    dataset=service.request('/api/prepared-datasets/'+created['id'],token=service.token)[1]
    assert dataset['status']=='ready' and dataset['validationResult']['trainingEligibleCount']==640
    assert dataset['validationResult']['trainingPossible'] is True
    parsed=parse_csv(body['csvText'].encode(),body['manifest'])['rows']
    spec={'kind':'train','sourceId':str(sid),'snapshotId':dataset['snapshotId'],
          'detectionPolicy':{'mode':'ml_only','deadlineMs':30000,'combination':'selected_path'}}
    identifier=submit_job(service,spec)
    actual_fit=[]
    original_fit=IsolationForest.fit
    def capture_fit(forest, matrix, *args, **kwargs):
        actual_fit.append(np.asarray(matrix).copy())
        return original_fit(forest,matrix,*args,**kwargs)
    with patch.object(IsolationForest,'fit',new=capture_fit), \
         patch('feature_fit.fit_features',side_effect=AssertionError('prepared feature fit repeated')), \
         patch('feature_fit.transform',side_effect=AssertionError('prepared feature transform repeated')), \
         patch('trainer.runner.calculate_features',side_effect=AssertionError('prepared feature calculation repeated')), \
         patch('sklearn.preprocessing.RobustScaler.fit',side_effect=AssertionError('scaler fit repeated')), \
         patch('sklearn.preprocessing.RobustScaler.transform',side_effect=AssertionError('scaler transform repeated')):
        worker_in_process(service)
    result=job_result(service,identifier)['resultSummary']
    selected=[row for row in parsed if training_eligible(row)]
    order=np.random.default_rng(42).permutation(640)
    assert len(actual_fit)==1
    np.testing.assert_array_equal(actual_fit[0],np.asarray([selected[int(i)]['features'] for i in order[:-128]]))
    assert result['trainingSelection']['selectedCount']==640 and result['trainingSelection']['excludedCount']==10
    assert result['trainingSelection']['normalGroundTruth'] is False
    reference=result['preparedTrainingReference']
    assert reference['eligibleRowIds']==sorted(row['rowId'] for row in selected)
    assert reference['trainRowIdsHash']==body['manifest']['preprocessing']['trainRowIdsHash']
    trainrows=[row for row in parsed if row['split']=='train']
    assert reference['trainRowIds']==sorted(row['rowId'] for row in trainrows)
    assert reference['trainOriginIds']==sorted({row['originRowId'] for row in trainrows})
    assert reference['trainGroupIds']==sorted({row['groupId'] for row in trainrows})
    assert result['dataContract']['schemaVersion']=='prepared-features-v1'
    assert result['dataContract']['operationalObservationMembership'] is False
    assert result['modelManifest']['preparedInputContract']==result['preparedInputContract']
    assert result['operationalEligibility']['eligible'] is False
    stored=conn.execute('''SELECT m.relative_path,f.relative_path,c.threshold_spec,c.training_settings,m.content_hash,f.content_hash
                           FROM ml_config_models c JOIN ml_artifacts m ON m.id=c.model_artifact_id
                           JOIN ml_artifacts f ON f.id=c.fit_artifact_id WHERE c.config_version_id=%s''',
                        (result['configVersionId'],)).fetchone()
    conn.commit()
    model_path,fit_path=(service.artifacts/stored[0],service.artifacts/stored[1])
    model,fit=joblib.load(model_path),joblib.load(fit_path)
    assert model['trainingReference']==fit['trainingReference']==stored[3]['preparedTrainingReference']==reference
    assert model['formatVersion']=='prepared-input-iforest-v1' and fit['additionalTransformApplied'] is False
    assert 'scaler' not in fit and 'scaler' not in model
    assert hashlib.sha256(model_path.read_bytes()).hexdigest()==stored[4]
    assert hashlib.sha256(fit_path.read_bytes()).hexdigest()==stored[5]
    assert stored[2]['referenceCount']==128
    code, config=service.request('/api/configurations/'+result['configVersionId'],token=service.token)
    assert code==200 and config['configurationSpec']['preparedDatasetId']==dataset['id']
    assert conn.execute('SELECT count(*) FROM data_snapshot_members WHERE snapshot_id=%s',
                        (dataset['snapshotId'],)).fetchone()[0]==0
    conn.commit()
    print('PASS actual prepared IF fit512/calibration128 from eligible640 of train650, exact vectors '
          'and zero feature/scaler reprocessing, artifact/fit/config/HTTP lineage',flush=True)

    evaluations={}
    for split in ('validation','test'):
        evalspec={'kind':'evaluate','sourceId':str(sid),'configVersionId':result['configVersionId'],
                  'snapshotId':dataset['snapshotId'],'purpose':split}
        eid=submit_job(service,evalspec)
        with patch('feature_fit.transform',side_effect=AssertionError('evaluation transform repeated')), \
             patch('sklearn.preprocessing.RobustScaler.transform',side_effect=AssertionError('evaluation scaler repeated')):
            worker_in_process(service)
        outcome=job_result(service,eid)['resultSummary']
        splitrows=[row for row in parsed if row['split']==split]
        scores=-model['forest'].score_samples(np.asarray([row['features'] for row in splitrows]))
        flags=scores>=float(stored[2]['value'])
        expected=dict(tp=0,fp=0,tn=0,fn=0)
        for row,flag in zip(splitrows,flags):
            if row['label']=='unknown': continue
            truth=row['label']=='anomaly'
            expected['tp' if flag and truth else 'fp' if flag else 'fn' if truth else 'tn']+=1
        metrics=outcome['metrics']
        assert {key:metrics[key] for key in expected}==expected
        for name,numerator,denominator in (
            ('precision',expected['tp'],expected['tp']+expected['fp']),
            ('recall',expected['tp'],expected['tp']+expected['fn']),
            ('falsePositiveRate',expected['fp'],expected['fp']+expected['tn'])):
            assert metrics[name]==(numerator/denominator if denominator else None)
        assert metrics['labelInterpretation']=='provided_labels_not_independently_verified'
        assert metrics['unlabeledCount']==sum(row['label']=='unknown' for row in splitrows)
        assert metrics['rowCount']==outcome['scoredCount']==len(splitrows)
        assert outcome['applicability']=='unmeasured' and outcome['operationalEligible'] is False
        for name in ('hr','sr','esbLatency'): assert metrics[name]['value'] is None and metrics[name]['reason']
        for name,group in metrics['scenarios'].items():
            assert group['rowCount']==sum((row['scenario'] or '<unspecified>')==name for row in splitrows)
        family=outcome['syntheticEvaluation']
        assert family==outcome['evaluationEnvelope']['synthetic']
        assert family['details']['generationPerformed'] is False
        assert family['details']['rowIdentity']=='prepared_numeric_feature_row'
        positive=[i for i,row in enumerate(splitrows) if (row['isSynthetic'] is True or row['scenario']) and row['label']=='anomaly']
        rate=family['metrics']['labeledAnomalyDetectionRate']
        assert rate['denominator']==len(positive) and rate['numerator']==sum(bool(flags[i]) for i in positive)
        db=conn.execute('SELECT metrics,applicability,population_count,scored_count FROM run_evaluations WHERE job_id=%s',(eid,)).fetchone()
        assert db[0]==metrics and db[1:] == ('unmeasured',len(splitrows),len(splitrows))
        conn.commit(); evaluations[split]=(evalspec,eid,outcome)
        print('PASS actual '+split+' scoring/manual labeled counts '+json.dumps(expected)+
              ', unknown='+str(metrics['unlabeledCount'])+', scenario/numeric/HR-SR-static boundaries',flush=True)
    return {'sourceId':str(sid),'body':body,'dataset':dataset,'trainSpec':spec,'trainId':identifier,
            'result':result,'evaluations':evaluations,'modelPath':model_path,'fitPath':fit_path,'parsed':parsed}


def subset_body(body, *, split='validation'):
    result=copy.deepcopy(body)
    records=list(csv.reader(io.StringIO(body['csvText'])))
    selected=[row for row in records[1:] if row[1]==split]
    result['manifest']['rowCount']=len(selected)
    stream=io.StringIO(newline=''); writer=csv.writer(stream)
    writer.writerow(records[0]); writer.writerows(selected)
    result['csvText']=stream.getvalue()
    return result


def register_dataset(service, body):
    code, created=service.request('/api/prepared-datasets',body,service.token,str(uuid.uuid4()))
    assert code==201,(code,created)
    worker_in_process(service)
    dataset=service.request('/api/prepared-datasets/'+created['id'],token=service.token)[1]
    assert dataset['status']=='ready',dataset
    return dataset


def evaluate_dataset(service, context, dataset, *, status='succeeded', config_id=None):
    spec={'kind':'evaluate','sourceId':context['sourceId'],'snapshotId':dataset['snapshotId'],
          'configVersionId':config_id or context['result']['configVersionId'],'purpose':'validation'}
    identifier=submit_job(service,spec)
    worker_in_process(service)
    return job_result(service,identifier,status=status)


def compatibility_boundaries(service, conn, context):
    base=subset_body(context['body'])
    valid=register_dataset(service,base)
    assert evaluate_dataset(service,context,valid)['resultSummary']['scoredCount']==8
    for name,change in (
        ('unit',lambda m:m['features'][0].update(unit='ms')),
        ('domain',lambda m:m.update(domain='other controlled domain')),
        ('definition',lambda m:m['preprocessing'].update(definitionHash='c'*64)),
        ('fit',lambda m:m['preprocessing'].update(fitHash='c'*64)),
        ('scaling',lambda m:m['preprocessing'].update(scaling='none'))):
        changed=copy.deepcopy(base);change(changed['manifest'])
        dataset=register_dataset(service,changed)
        failed=evaluate_dataset(service,context,dataset,status='failed')
        assert failed['errorCode']=='prepared_model_input_contract_mismatch',(name,failed)
    changed=copy.deepcopy(base)
    records=list(csv.reader(io.StringIO(changed['csvText'])))
    for row in records:row[-2:]=reversed(row[-2:])
    changed['manifest']['features'].reverse()
    stream=io.StringIO(newline='');csv.writer(stream).writerows(records);changed['csvText']=stream.getvalue()
    failed=evaluate_dataset(service,context,register_dataset(service,changed),status='failed')
    assert failed['errorCode']=='prepared_model_input_contract_mismatch'

    for field,value,reason in (
        ('origin',context['parsed'][10]['rowId'],'prepared_evaluation_training_origin_overlap'),
        ('row_id',context['parsed'][10]['originRowId'],'prepared_evaluation_training_origin_overlap'),
        ('group',context['parsed'][10]['groupId'],'prepared_evaluation_training_group_overlap')):
        changed=copy.deepcopy(base)
        records=list(csv.reader(io.StringIO(changed['csvText'])))
        records[1][records[0].index(field)]=value
        stream=io.StringIO(newline='');csv.writer(stream).writerows(records);changed['csvText']=stream.getvalue()
        failed=evaluate_dataset(service,context,register_dataset(service,changed),status='failed')
        assert failed['errorCode']==reason,failed

    for excluded in context['parsed'][:3]:
        assert not training_eligible(excluded)
        for field,value in (('row_id',excluded['rowId']),('origin',excluded['rowId']),
                            ('origin',excluded['originRowId']),('group',excluded['groupId'])):
            changed=copy.deepcopy(base)
            records=list(csv.reader(io.StringIO(changed['csvText'])))
            records[1][records[0].index(field)]=value
            stream=io.StringIO(newline='');csv.writer(stream).writerows(records);changed['csvText']=stream.getvalue()
            failed=evaluate_dataset(service,context,register_dataset(service,changed),status='failed')
            assert failed['errorCode'].startswith('prepared_evaluation_training_'),(field,failed)

    unknown=copy.deepcopy(context['body'])
    unknown['manifest']['preprocessing'].update(fitHash=None,fitSplit='unknown',trainRowIdsHash=None)
    dataset=register_dataset(service,unknown)
    assert 'fit_lineage_unverified' in dataset['validationResult']['reasonCodes']
    identifier=submit_job(service,{**context['trainSpec'],'snapshotId':dataset['snapshotId']})
    worker_in_process(service)
    model=job_result(service,identifier)['resultSummary']
    assert evaluate_dataset(service,context,dataset,config_id=model['configVersionId'])['status']=='succeeded'
    cross=register_dataset(service,subset_body(unknown))
    failed=evaluate_dataset(service,context,cross,config_id=model['configVersionId'],status='failed')
    assert failed['errorCode']=='prepared_cross_dataset_scaling_fit_unverified',failed

    # A separate immutable legacy configuration lacks the prepared input descriptor.
    legacy=uuid.uuid4()
    configuration=conn.execute('SELECT configuration_spec FROM ml_config_versions WHERE id=%s',
                               (context['result']['configVersionId'],)).fetchone()[0]
    for key in ('preparedInputContract','preparedDatasetId','operationalEligibility'):configuration.pop(key,None)
    conn.execute('''INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
                    VALUES(%s,%s,%s::jsonb,'{}',%s,'independent-qa',clock_timestamp())''',
                 (legacy,context['sourceId'],json.dumps(configuration),digest(configuration)))
    conn.execute('''INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,
                   adapter_spec,training_settings,threshold_spec)
                   SELECT %s,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,training_settings,threshold_spec
                   FROM ml_config_models WHERE config_version_id=%s''',(legacy,context['result']['configVersionId']))
    conn.commit()
    failed=evaluate_dataset(service,context,valid,config_id=str(legacy),status='failed')
    assert failed['errorCode']=='prepared_model_input_descriptor_unavailable'
    print('PASS actual compatible cross-dataset evaluation; unit/domain/definition/fit/scaling/order and '
          'legacy descriptor rejection; row/origin/group overlap; same-dataset unknown fit allowed, '
          'cross-dataset unknown scaled fit rejected; twelve excluded-train alias/group paths rejected',flush=True)


def operational_state(conn):
    tables=('data_observations','data_executions','data_processes','data_transactions','data_messages',
            'data_label_evidence','run_process_decisions','run_decision_events','ops_system_state',
            'ops_input_controls','ops_input_runs','data_input_origins',
            'powercode_queue.detection_logs','powercode_queue.feature_observations',
            'powercode_queue.llm_explanation_jobs')
    result={table:hashlib.sha256(json.dumps(conn.execute('SELECT to_jsonb(t) FROM '+table+' t ORDER BY to_jsonb(t)::text').fetchall(),
                                           sort_keys=True,default=str).encode()).hexdigest() for table in tables}
    conn.commit()
    return result


def model_recovery_and_isolation(service,conn,context):
    before=operational_state(conn)
    original_register=runner._register_artifact
    calls=[]
    def fail_second_artifact(*args,**kwargs):
        calls.append(kwargs['kind'])
        if len(calls)==2:raise RuntimeError('controlled second artifact interruption')
        return original_register(*args,**kwargs)
    identifier=submit_job(service,context['trainSpec'])
    with patch('trainer.runner._register_artifact',side_effect=fail_second_artifact):worker_in_process(service)
    job_result(service,identifier,status='queued')
    assert conn.execute('SELECT count(*) FROM ml_artifacts WHERE created_job_id=%s',(identifier,)).fetchone()[0]==0
    conn.commit()
    # Issue69: prepared 학습도 롤백 뒤 tmp/<attempt>·승격 미등록 model/fit 파일을 남기지 않는다.
    def workspace_clean():
        temporary=service.artifacts/'tmp'
        leftovers=sorted(path.name for path in temporary.iterdir()) if temporary.is_dir() else []
        assert leftovers==[],leftovers
        for kind,directory in (('model','models'),('fit','fit')):
            files=len(list((service.artifacts/directory).glob('*.joblib'))) if (service.artifacts/directory).is_dir() else 0
            registered=conn.execute('SELECT count(*) FROM ml_artifacts WHERE kind=%s',(kind,)).fetchone()[0];conn.commit()
            assert files==registered,(kind,files,registered)
    workspace_clean()
    conn.execute("UPDATE powercode_queue.training_jobs SET available_at=clock_timestamp() WHERE payload->>'jobId'=%s",(identifier,))
    conn.commit();worker_in_process(service)
    recovered=job_result(service,identifier)['resultSummary']
    assert conn.execute('SELECT count(*) FROM ml_artifacts WHERE created_job_id=%s',(identifier,)).fetchone()[0]==2
    conn.commit()
    workspace_clean()

    evalspec=context['evaluations']['validation'][0]
    original_finish=runner._finish
    finalizations=[]
    def interrupt_after_ack(*args,**kwargs):
        original_finish(*args,**kwargs);finalizations.append(True)
        if len(finalizations)==1:raise RuntimeError('controlled evaluation publication interruption')
    eid=submit_job(service,evalspec)
    with patch('trainer.runner._finish',side_effect=interrupt_after_ack):worker_in_process(service)
    job_result(service,eid,status='queued')
    assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(eid,)).fetchone()[0]==0
    conn.execute("UPDATE powercode_queue.training_jobs SET available_at=clock_timestamp() WHERE payload->>'jobId'=%s",(eid,))
    conn.commit();worker_in_process(service)
    completed=job_result(service,eid)['resultSummary']
    enqueue(conn,'training',{'schemaVersion':1,'jobId':eid});conn.commit();worker_in_process(service)
    assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(eid,)).fetchone()[0]==1
    conn.commit()

    from trainer import prepared_evaluation
    original_scores=prepared_evaluation.scores
    expired=submit_job(service,evalspec)
    def expire_after_scoring(*args,**kwargs):
        values=original_scores(*args,**kwargs)
        with psycopg.connect(service.dsn) as owner:
            owner.execute("UPDATE powercode_queue.training_jobs SET lease_until=clock_timestamp()-interval '1 second' "
                          "WHERE payload->>'jobId'=%s",(expired,))
            owner.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                          (expired,))
        return values
    with patch('trainer.prepared_evaluation.scores',side_effect=expire_after_scoring):worker_in_process(service)
    job_result(service,expired,status='running')
    assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(expired,)).fetchone()[0]==0
    conn.commit();worker_in_process(service)
    job_result(service,expired)
    assert conn.execute('SELECT count(*) FROM run_job_attempts WHERE job_id=%s',(expired,)).fetchone()[0]==2
    conn.commit()

    for name in ('modelPath','fitPath'):
        path=Path(context[name]);original=path.read_bytes()
        try:
            path.write_bytes(original+b'controlled-corruption')
            failed=evaluate_dataset(service,context,context['dataset'],status='failed')
            assert failed['errorCode']=='prepared_model_artifact_hash_invalid',failed
            assert conn.execute('SELECT count(*) FROM run_evaluations WHERE job_id=%s',(failed['id'],)).fetchone()[0]==0
            conn.commit()
        finally:path.write_bytes(original)
    relative=conn.execute('SELECT normalized_relative_path FROM data_prepared_datasets WHERE id=%s',
                          (context['dataset']['id'],)).fetchone()[0]
    conn.commit();path=service.artifacts/relative;original=path.read_bytes()
    try:
        path.write_bytes(original+b'controlled-corruption')
        failed=evaluate_dataset(service,context,context['dataset'],status='failed')
        assert failed['errorCode']=='prepared_artifact_hash_mismatch',failed
    finally:path.write_bytes(original)

    code,catalog=service.request('/api/jobs/feature-sets?sourceId='+context['sourceId'],token=service.token)
    assert code==200
    item=next(item for item in catalog['items'] if item['id']==context['result']['featureSetId'])
    assert item['supported'] is False and item['reason']=='prepared_dataset_path_required',item
    active=service.request('/api/configurations/active',token=service.token)[1]
    for evaluation_id in (context['evaluations']['validation'][2]['evaluationId'],completed['evaluationId']):
        code,rejected=service.request('/api/configurations/'+context['result']['configVersionId']+'/applications',
          {'operation':'apply','evaluationId':evaluation_id,'expectedRevision':active['revision'],
           'expectedActiveVersionId':active['configVersionId']},service.token,str(uuid.uuid4()))
        assert code==409,(code,rejected)
    try:prepare_config(conn,uuid.UUID(context['result']['configVersionId']),service.artifacts)
    except ValueError as error:assert str(error)=='prepared_detector_reproduction_unverified'
    else:raise AssertionError('Detector accepted prepared candidate')
    conn.rollback()

    # Even a directly inserted local application cannot bypass Detector preparation.
    application=uuid.uuid4()
    conn.execute('''INSERT INTO run_applications(id,operation,target_config_version_id,expected_active_version_id,
                    expected_system_revision,evaluation_id,requested_by_actor_id,requester_role,approved_at,
                    request_hash,status,gate_evidence,transition_audit,created_at)
                    VALUES(%s,'apply',%s,%s,%s,%s,'independent-qa','admin',clock_timestamp(),'controlled',
                    'queued','{}','{}',clock_timestamp())''',
                 (application,context['result']['configVersionId'],active['configVersionId'],active['revision'],completed['evaluationId']))
    conn.commit();assert process_application_once(conn,service.artifacts,'independent-prepared-qa');conn.commit()
    assert conn.execute('SELECT status,error_code FROM run_applications WHERE id=%s',(application,)).fetchone()==(
        'failed','prepared_detector_reproduction_unverified')
    conn.commit()
    assert operational_state(conn)==before
    envelope=json.loads((Path(__file__).resolve().parents[2]/'contracts/trainer-evaluation-envelope.schema.json').read_text())
    validate(completed['evaluationEnvelope'],envelope)
    print('PASS prepared artifact registration and evaluation ACK rollback/retry, real scoring lease expiry/reclaim, duplicate result, '
          'model/fit/normalized tamper rejection, explicit catalog unsupported, API and Detector gates, '
          'operational observations/queues/active unchanged, result envelope schema',flush=True)


def main():
    dsn = os.environ.get('PIPELINE_TEST_DATABASE_URL')
    if not dsn:
        raise RuntimeError('PIPELINE_TEST_DATABASE_URL is required')
    parsed = urlsplit(dsn)
    if parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.path != '/qa_prepared_csv_service':
        raise RuntimeError('only the dedicated loopback qa_prepared_csv_service is allowed')
    with tempfile.TemporaryDirectory(prefix='powercode-prepared-csv-qa-') as directory:
        service = Service(directory, dsn)
        try:
            assert service.env['DATABASE_URL'] == dsn
            assert service.env['ARTIFACT_ROOT'] == str(service.artifacts)
            with psycopg.connect(dsn) as conn:
                before=operational_state(conn)
                body = admission(service, conn)
                import_recovery(service, conn, body)
                context=training_and_metrics(service,conn)
                compatibility_boundaries(service,conn,context)
                model_recovery_and_isolation(service,conn,context)
                assert operational_state(conn)==before
        finally:
            service.close()


if __name__ == '__main__':
    main()
