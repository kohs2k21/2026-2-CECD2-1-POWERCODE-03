"""One leased import publishes an external snapshot and immutable file metadata."""
import json
import uuid

from trainer.prepared_contract import digest, validate_manifest
from trainer.prepared_parser import parse_csv
from trainer.prepared_storage import read, store
from trainer.lease import lock_training_ownership


def execute(conn, job, root, message, attempt_id, attempt_token):
    dataset_id = uuid.UUID(job['input']['preparedDatasetId'])
    with conn.cursor() as cur:
        cur.execute('''SELECT d.source_id,d.status,d.manifest,d.manifest_hash,d.original_relative_path,
                              d.original_hash,d.original_byte_size,s.schema_version
                       FROM data_prepared_datasets d JOIN ops_sources s ON s.id=d.source_id
                       WHERE d.id=%s AND d.created_job_id=%s''', (dataset_id, job['id']))
        row = cur.fetchone()
    if row is None or row[1] != 'queued': raise ValueError('prepared_import_not_queued')
    manifest = validate_manifest(row[2])
    if uuid.UUID(job['input']['sourceId']) != row[0] or uuid.UUID(manifest['sourceId']) != row[0] or \
            manifest['sourceSchemaVersion'] != row[7] or digest(manifest) != row[3]:
        raise ValueError('prepared_import_contract_mismatch')
    conn.commit()
    content = read(root, row[4], row[5])
    if len(content) != row[6]: raise ValueError('prepared_original_size_mismatch')
    parsed = parse_csv(content, manifest)
    normalized = store(root, dataset_id, parsed['normalizedBytes'])
    # Recheck both complete files before making the dataset visible.
    read(root, row[4], row[5])
    snapshot_id = uuid.uuid4()
    selection = {'preparedDatasetId': str(dataset_id), 'reader': 'prepared-jsonl-v1',
                 'manifestHash': row[3], 'originalHash': row[5], 'normalizedHash': normalized['sha256']}
    snapshot_hash = digest(selection)
    result = {'preparedDatasetId': str(dataset_id), 'snapshotId': str(snapshot_id), 'manifestHash': row[3],
              'normalizedHash': normalized['sha256'], 'rowCount': len(parsed['rows']),
              'validationResult': parsed['validationResult'], 'operationalEligible': False}
    with conn.transaction():
        if not lock_training_ownership(conn, message, job['id'], attempt_id, attempt_token):
            raise RuntimeError('training_ownership_lost')
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,
                           schema_version,manifest_hash,created_by_actor_id,created_at,ready_at)
                           VALUES(%s,%s,'ready',clock_timestamp(),%s::jsonb,%s::jsonb,'prepared-features-v1',%s,%s,
                                  clock_timestamp(),clock_timestamp())''',
                        (snapshot_id,row[0],json.dumps(selection),json.dumps({**manifest['split'],
                          'counts':parsed['validationResult']['splitCounts'],'membership':'external_file_provided'}),snapshot_hash,job['actor']))
            cur.execute('''UPDATE data_prepared_datasets SET status='ready',normalized_relative_path=%s,normalized_hash=%s,
                           row_count=%s,row_metadata=%s::jsonb,validation_result=%s::jsonb,snapshot_id=%s,ready_at=clock_timestamp()
                           WHERE id=%s AND status='queued' ''',
                        (normalized['relativePath'],normalized['sha256'],len(parsed['rows']),json.dumps(parsed['rowMetadata']),
                         json.dumps(parsed['validationResult']),snapshot_id,dataset_id))
            if cur.rowcount != 1: raise RuntimeError('prepared_import_ownership_lost')
        from trainer.runner import _finish
        _finish(conn,message,job,attempt_id,attempt_token,result,None,True)
    return {**result,'_finalized':True}
