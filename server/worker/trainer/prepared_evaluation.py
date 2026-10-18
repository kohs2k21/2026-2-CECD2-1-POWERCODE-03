"""Prepared numeric evaluation; no raw HR/SR, PROCESS or ESB latency invention."""
from collections import defaultdict
import json
import math
import time
import uuid

from detector.artifacts import verified_artifact_path
from telemetry import cost_family
from trainer.metrics import measurement
from trainer.prepared_contract import digest
from trainer.prepared_models import FORMAT_VERSION,check_evaluation_lineage,check_scaling_lineage,load,scores
from trainer.prepared_reader import load_dataset
from trainer.lease import lock_training_ownership


def _model(conn, spec, dataset, root):
    with conn.cursor() as cur:
        cur.execute('''SELECT c.configuration_spec,c.source_id,cm.training_settings,cm.threshold_spec,
                              m.relative_path,m.content_hash,m.format_version,m.snapshot_id,m.fit_artifact_id,
                              f.relative_path,f.content_hash,f.format_version,f.snapshot_id,cm.fit_artifact_id,
                              cm.feature_set_id,m.feature_set_id,f.feature_set_id,m.metadata,f.metadata
                       FROM ml_config_versions c JOIN ml_config_models cm ON cm.config_version_id=c.id
                       JOIN ml_artifacts m ON m.id=cm.model_artifact_id JOIN ml_artifacts f ON f.id=cm.fit_artifact_id
                       WHERE c.id=%s AND cm.model_key='iforest' ''',(spec['configVersionId'],))
        rows = cur.fetchall()
    if len(rows)!=1: raise ValueError('prepared_model_not_ready')
    row = rows[0]
    if row[1] != uuid.UUID(spec['sourceId']) or row[0].get('preparedInputContract') is None or row[6]!=FORMAT_VERSION or row[11]!=FORMAT_VERSION:
        raise ValueError('prepared_model_input_descriptor_unavailable')
    if row[0].get('detectionPolicy',{}).get('mode')!='ml_only' or row[0].get('ruleVersionIds'):
        raise ValueError('prepared_ml_only_required')
    if row[7]!=row[12] or row[8]!=row[13] or row[14]!=row[15] or row[14]!=row[16] or \
            str(row[7])!=row[0].get('trainingSnapshotId') or str(row[7])!=row[2].get('snapshotId') or \
            row[17].get('trainSplit')!='train' or row[18].get('trainSplit')!='train' or row[2].get('split')!='train':
        raise ValueError('prepared_model_fit_lineage_mismatch')
    model_path,fit_path = verified_artifact_path(root,row[4],row[5]),verified_artifact_path(root,row[9],row[10])
    if model_path is None or fit_path is None: raise ValueError('prepared_model_artifact_hash_invalid')
    forest,reference = load(model_path,fit_path,dataset['manifest'],row[2])
    if reference.get('snapshotId')!=str(row[7]) or row[2].get('featureNames')!=[feature['name'] for feature in dataset['manifest']['features']]:
        raise ValueError('prepared_model_fit_lineage_mismatch')
    with conn.cursor() as cur:
        cur.execute('SELECT schema_version,content_hash,output_schema FROM ml_feature_sets WHERE id=%s',(row[14],))
        feature_set=cur.fetchone()
    descriptor=row[2]['preparedInputContract']
    if feature_set is None or feature_set[0]!='prepared-features-v1' or feature_set[1]!=digest(descriptor) or feature_set[2]!=descriptor:
        raise ValueError('prepared_model_feature_schema_mismatch')
    threshold = row[3]
    if threshold.get('direction')!='higher_is_anomaly': raise ValueError('prepared_invalid_threshold')
    try: boundary = float(threshold['value'])
    except (KeyError,ValueError,TypeError): raise ValueError('prepared_invalid_threshold') from None
    if not math.isfinite(boundary): raise ValueError('prepared_invalid_threshold')
    return forest,reference,boundary


def report(rows, predictions):
    counts = dict.fromkeys(('tp','fp','tn','fn'),0)
    unlabeled = 0
    for row,prediction in zip(rows,predictions):
        label = row['label']
        if label=='unknown': unlabeled+=1; continue
        key = 'tp' if prediction and label=='anomaly' else 'fp' if prediction else 'fn' if label=='anomaly' else 'tn'
        counts[key]+=1
    tp,fp,tn,fn = (counts[key] for key in ('tp','fp','tn','fn'))
    return {**counts,'rowCount':len(rows),'unlabeledCount':unlabeled,
            'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
            'falsePositiveRate':fp/(fp+tn) if fp+tn else None,
            'labelInterpretation':'provided_labels_not_independently_verified',
            'hr':{'value':None,'reason':'raw_rule_inputs_unavailable'},
            'sr':{'value':None,'reason':'raw_rule_inputs_unavailable'},
            'esbLatency':{'value':None,'reason':'static_prepared_data_no_ESB_latency'}}


def execute(conn,job,dataset,root,message,attempt_id,attempt_token):
    started = time.monotonic()
    spec = job['input']
    rows = [row for row in dataset['rows'] if row['split']==spec['purpose']]
    if not rows: raise ValueError('prepared_evaluation_split_unavailable')
    forest,reference,boundary = _model(conn,spec,dataset,root)
    check_scaling_lineage(dataset['manifest'],reference,dataset['id'])
    check_evaluation_lineage(rows,reference)
    conn.commit()
    values = scores(forest,rows)
    predictions = [value>=boundary for value in values]
    metrics = report(rows,predictions)
    groups = defaultdict(list)
    for index,row in enumerate(rows): groups[row['scenario'] or '<unspecified>'].append(index)
    metrics['scenarios'] = {name:report([rows[index] for index in indices],[predictions[index] for index in indices]) for name,indices in groups.items()}
    reasons = ['prepared_not_operational_validation',*dataset['validationResult']['reasonCodes']]
    cost = cost_family({'totalMs':(time.monotonic()-started)*1000,'evaluationMs':(time.monotonic()-started)*1000},batch_size=len(rows))
    cost['details'].update(scope='offline_prepared_numeric_rows',rowIdentity='prepared_numeric_feature_row',additionalTransformApplied=False)
    synthetic_rows = [index for index,row in enumerate(rows) if row['isSynthetic'] is True or row['scenario'] is not None]
    synthetic = None
    if synthetic_rows:
        positive = [index for index in synthetic_rows if rows[index]['label']=='anomaly']
        hits = sum(predictions[index] for index in positive)
        synthetic = {'status':'measured','reason':None,'metricVersion':'prepared-imported-synthetic-v1',
                     'metrics':{'declaredSyntheticRows':measurement(len(synthetic_rows),'count'),
                                'labeledAnomalyDetectionRate':measurement(hits/len(positive) if positive else None,'ratio',
                                  numerator=hits,denominator=len(positive),reason=None if positive else 'explicit_anomaly_labels_unavailable')},
                     'details':{'rowIdentity':'prepared_numeric_feature_row','generationPerformed':False,
                                'generationProvenance':dataset['manifest'].get('synthetic'),'scenarioResults':metrics['scenarios'],
                                'interpretation':'imported_declared_labels_not_customer_incident_truth',
                                'srReason':'raw_rule_inputs_unavailable','latencyReason':'static_prepared_data_no_ESB_latency'}}
    evaluation_id = uuid.uuid4()
    evaluation_spec = {'metricVersion':'prepared-numeric-v1','preparedDatasetId':dataset['id'],'manifestHash':dataset['manifestHash'],
                       'normalizedHash':dataset['normalizedHash'],'inputStage':'model_input','additionalTransformApplied':False,
                       'purpose':spec['purpose'],'operationalEligible':False}
    if synthetic is not None: metrics['syntheticEvaluation']=synthetic
    with conn.transaction():
        if not lock_training_ownership(conn,message,job['id'],attempt_id,attempt_token):
            raise RuntimeError('training_ownership_lost')
        # Hashes/immutable metadata are checked again before publishing a result.
        _model(conn,spec,dataset,root)
        load_dataset(conn,dataset['id'],spec['sourceId'],root)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO run_evaluations(id,job_id,config_version_id,snapshot_id,purpose,evaluation_spec_version,
                           evaluation_spec_hash,evaluation_spec,applicability,population_count,scored_count,unavailable_count,
                           metrics,reason_codes,completed_at)
                           VALUES(%s,%s,%s,%s,%s,'prepared-numeric-v1',%s,%s::jsonb,'unmeasured',%s,%s,0,%s::jsonb,%s::jsonb,clock_timestamp())''',
                        (evaluation_id,job['id'],spec['configVersionId'],dataset['snapshotId'],spec['purpose'],digest(evaluation_spec),
                         json.dumps(evaluation_spec),len(rows),len(rows),json.dumps(metrics),json.dumps(reasons)))
        result = {'evaluationId':str(evaluation_id),'snapshotId':dataset['snapshotId'],'preparedDatasetId':dataset['id'],
                  'configVersionId':spec['configVersionId'],'applicability':'unmeasured','populationCount':len(rows),
                  'scoredCount':len(rows),'unavailableCount':0,'unlabeledCount':metrics['unlabeledCount'],
                  'metrics':metrics,'reasonCodes':reasons,'executionCost':cost,'operationalEligible':False,
                  'rowIdentity':'prepared_numeric_feature_row'}
        if synthetic is not None: result['syntheticEvaluation']=synthetic
        from trainer.runner import _finish
        _finish(conn,message,job,attempt_id,attempt_token,result,None,True)
    return {**result,'_finalized':True}
