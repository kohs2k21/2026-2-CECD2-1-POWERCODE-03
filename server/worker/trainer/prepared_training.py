"""Train a new isolated candidate from one immutable prepared dataset."""
import json
from pathlib import Path
import time
import uuid

from detector.policy import parse_policy
from telemetry import cost_family
from trainer.prepared_catalog import register
from trainer.prepared_contract import digest
from trainer.prepared_models import train
from trainer.prepared_reader import load_dataset
from trainer.lease import lock_training_ownership


def execute(conn, job, dataset, root, message, attempt_id, attempt_token):
    started = time.monotonic()
    policy = parse_policy(job['input'], required=True)
    if policy.mode != 'ml_only' or job['input'].get('modelKey', 'iforest') != 'iforest':
        raise ValueError('prepared_ml_only_required')
    if root is None: raise ValueError('prepared_artifact_root_required')
    root = Path(root).resolve(strict=True)
    temporary = root/'tmp'
    if temporary.is_symlink(): raise ValueError('prepared_artifact_path_invalid')
    temporary.mkdir(exist_ok=True)
    output = temporary/str(attempt_id)
    if output.is_symlink(): raise ValueError('prepared_artifact_path_invalid')
    output.mkdir(exist_ok=False)
    trained = train(dataset['rows'],dataset['manifest'],output,dataset_id=dataset['id'],snapshot_id=dataset['snapshotId'])
    from trainer.runner import _promote_output,_register_artifact,_finish
    fit_path = _promote_output(root,output,trained['fitPath'],'fit')
    model_path = _promote_output(root,output,trained['modelPath'],'model')
    config_id = uuid.uuid4()
    descriptor = trained['inputContract']
    with conn.transaction():
        if not lock_training_ownership(conn,message,job['id'],attempt_id,attempt_token):
            raise RuntimeError('training_ownership_lost')
        load_dataset(conn,dataset['id'],job['input']['sourceId'],root)
        feature_set_id = register(conn,dataset['manifest'],job['actor'])
        fit_id = _register_artifact(conn,root=root,path_value=fit_path,kind='fit',snapshot_id=uuid.UUID(dataset['snapshotId']),
            feature_set_id=feature_set_id,job_id=job['id'],attempt_id=attempt_id,runtime_version=trained['runtimeVersion'],format_version=trained['formatVersion'])
        model_id = _register_artifact(conn,root=root,path_value=model_path,kind='model',snapshot_id=uuid.UUID(dataset['snapshotId']),
            feature_set_id=feature_set_id,job_id=job['id'],attempt_id=attempt_id,runtime_version=trained['runtimeVersion'],
            format_version=trained['formatVersion'],fit_artifact_id=fit_id)
        config = {'schemaVersion':1,'sourceId':job['input']['sourceId'],'featureSetId':str(feature_set_id),
                  'trainingSnapshotId':dataset['snapshotId'],'preparedDatasetId':dataset['id'],
                  'preparedInputContract':descriptor,'trainingSelection':trained['trainingSelection'],
                  'ruleVersionIds':[],'detectionPolicy':policy.document(),
                  'operationalEligibility':{'eligible':False,'reason':'prepared_detector_reproduction_unverified'}}
        config['detectionPolicy'].pop('legacy',None)
        names = [feature['name'] for feature in dataset['manifest']['features']]
        settings = {'featureNames':names,'snapshotId':dataset['snapshotId'],'split':'train','preparedInputContract':descriptor,
                    'preparedTrainingReference':trained['trainingReference'],'definitionsHash':descriptor['preprocessing']['definitionHash'],
                    'inputStage':'model_input','additionalTransformApplied':False}
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
                           VALUES(%s,%s,%s::jsonb,'{}'::jsonb,%s,%s,clock_timestamp())''',
                        (config_id,job['input']['sourceId'],json.dumps(config),digest(config),job['actor']))
            cur.execute('''INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,
                           adapter_spec,training_settings,threshold_spec)
                           VALUES(%s,'iforest',%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)''',
                        (config_id,feature_set_id,model_id,fit_id,json.dumps({'name':'prepared-iforest','formatVersion':trained['formatVersion']}),
                         json.dumps(settings),json.dumps(trained['thresholdSpec'])))
        cost = cost_family({'totalMs':(time.monotonic()-started)*1000,'trainMs':(time.monotonic()-started)*1000},
                           batch_size=trained['trainingSelection']['selectedCount'])
        cost['details']['scope']='offline_prepared_training_without_transforms'
        result = {'configVersionId':str(config_id),'snapshotId':dataset['snapshotId'],'preparedDatasetId':dataset['id'],
                  'featureSetId':str(feature_set_id),'artifactId':str(model_id),'trainingSelection':trained['trainingSelection'],
                  'preparedTrainingReference':trained['trainingReference'],'operationalEligibility':config['operationalEligibility'],
                  'preparedInputContract':descriptor,'executionCost':cost}
        _finish(conn,message,job,attempt_id,attempt_token,result,None,True)
    return {**result,'_finalized':True}
