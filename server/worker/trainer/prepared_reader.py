"""External snapshot reader; never joins or creates operational observations."""
import json
import uuid

from trainer.prepared_contract import canonical, digest, validate_manifest
from trainer.prepared_storage import MAX_NORMALIZED_BYTES, read


def dataset_for_snapshot(conn, snapshot_id, source_id):
    with conn.cursor() as cur:
        cur.execute('''SELECT s.schema_version,d.id FROM data_snapshots s LEFT JOIN data_prepared_datasets d
                       ON d.snapshot_id=s.id AND d.status='ready'
                       WHERE s.id=%s AND s.source_id=%s AND s.status='ready' ''', (snapshot_id, source_id))
        row = cur.fetchone()
    if row is None or row[0]!='prepared-features-v1': return None
    if row[1] is None: raise ValueError('prepared_dataset_not_ready')
    return row[1]


def load_dataset(conn, dataset_id, source_id, root):
    with conn.cursor() as cur:
        cur.execute('''SELECT d.manifest,d.manifest_hash,d.original_relative_path,d.original_hash,d.original_byte_size,
                              d.normalized_relative_path,d.normalized_hash,d.row_count,d.row_metadata,d.validation_result,
                              d.snapshot_id,s.schema_version,p.schema_version
                       FROM data_prepared_datasets d JOIN data_snapshots s ON s.id=d.snapshot_id
                       JOIN ops_sources p ON p.id=d.source_id
                       WHERE d.id=%s AND d.source_id=%s AND d.status='ready' AND s.status='ready' ''', (dataset_id, source_id))
        row = cur.fetchone()
    if row is None: raise ValueError('prepared_dataset_not_ready')
    manifest = validate_manifest(row[0])
    if digest(manifest) != row[1] or uuid.UUID(manifest['sourceId']) != uuid.UUID(str(source_id)) or \
            row[11] != 'prepared-features-v1' or manifest['sourceSchemaVersion'] != row[12]:
        raise ValueError('prepared_dataset_contract_mismatch')
    original = read(root, row[2], row[3])
    if len(original) != row[4]: raise ValueError('prepared_original_size_mismatch')
    normalized = read(root, row[5], row[6], maximum=MAX_NORMALIZED_BYTES)
    try: rows = [json.loads(line) for line in normalized.splitlines()]
    except (ValueError, UnicodeDecodeError): raise ValueError('prepared_normalized_invalid') from None
    if len(rows) != row[7] or row[7] != manifest['rowCount'] or \
            [{key: value for key, value in item.items() if key != 'features'} for item in rows] != row[8]:
        raise ValueError('prepared_row_metadata_mismatch')
    return {'id': str(dataset_id), 'snapshotId': str(row[10]), 'manifest': manifest, 'manifestHash': row[1],
            'normalizedHash': row[6], 'rows': rows, 'validationResult': row[9]}
