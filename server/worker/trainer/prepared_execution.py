"""Small prepared-snapshot dispatch, keeping raw Trainer and Detector unchanged."""
from trainer.prepared_reader import dataset_for_snapshot,load_dataset


def maybe_execute(conn,job,root,message,attempt_id,attempt_token):
    spec=job['input']
    if job['kind'] not in ('train','evaluate') or not spec.get('snapshotId'): return None
    dataset_id=dataset_for_snapshot(conn,spec['snapshotId'],spec['sourceId'])
    if dataset_id is None: return None
    allowed={'kind','sourceId','snapshotId','detectionPolicy','modelKey'} if job['kind']=='train' else {
        'kind','sourceId','snapshotId','configVersionId','purpose'}
    if set(spec)-allowed: raise ValueError('prepared_invalid_job_input')
    if job['kind']=='evaluate' and spec.get('purpose') not in ('validation','test'):
        raise ValueError('prepared_evaluation_split_unavailable')
    dataset=load_dataset(conn,dataset_id,spec['sourceId'],root)
    conn.commit()
    if job['kind']=='train':
        from trainer.prepared_training import execute
    else:
        from trainer.prepared_evaluation import execute
    return execute(conn,job,dataset,root,message,attempt_id,attempt_token)
