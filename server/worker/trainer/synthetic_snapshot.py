"""Freeze parent/dependency context at the existing snapshot's ingestion cutoff."""
from collections import defaultdict

from trainer.synthetic_generation import parent_key
from trainer.synthetic_profiles import validate_binding

MAX_COHORT_ROWS = 2000
MAX_CONTEXT_ROWS = 10000


def validate_source(conn, spec, profile):
    with conn.cursor() as cur:
        cur.execute('''SELECT s.schema_version,s.field_catalog,d.schema_version,d.manifest_hash,
                              d.observation_cutoff_at,d.source_id
                       FROM ops_sources s JOIN data_snapshots d ON d.source_id=s.id
                       WHERE s.id=%s AND d.id=%s AND d.status='ready' ''',
                    (spec['sourceId'], spec['snapshotId']))
        row = cur.fetchone()
    if row is None or row[2] != profile['snapshotSchemaVersion']:
        raise ValueError('synthetic_snapshot_contract_mismatch')
    validate_binding(profile, row[0], row[1], spec.get('syntheticProfileBinding'))
    if spec.get('syntheticProfileBinding') is None:
        raise ValueError('synthetic_profile_binding_required')
    return {'snapshotId': spec['snapshotId'], 'snapshotHash': row[3], 'cutoff': row[4].isoformat(),
            'sourceId': str(row[5]), 'sourceSchemaVersion': row[0]}


def rows_for_generation(conn, rows, spec, snapshot):
    if len(rows) > MAX_COHORT_ROWS:
        raise ValueError('synthetic_cohort_limit_exceeded')
    transactions = sorted({str(row[1]['transaction_id']) for row in rows if row[1].get('transaction_id') is not None})
    with conn.cursor() as cur:
        # Observations are immutable. Resolve revisions once under the fixed
        # cutoff and persist the chosen identities/values in the result artifact.
        cur.execute('''SELECT id,allowed_values,layer FROM (
                        SELECT DISTINCT ON (execution_id) id,allowed_values,layer,execution_id
                        FROM data_observations
                        WHERE source_id=%s AND layer IN ('T','P') AND execution_id IS NOT NULL
                          AND persisted_at<=%s::timestamptz
                          AND allowed_values->>'transaction_id'=ANY(%s)
                        ORDER BY execution_id,revision_no DESC,persisted_at DESC,id DESC
                      ) selected ORDER BY id LIMIT %s''',
                    (spec['sourceId'], snapshot['cutoff'], transactions, MAX_CONTEXT_ROWS + 1))
        context = cur.fetchall()
    if len(context) > MAX_CONTEXT_ROWS:
        raise ValueError('synthetic_parent_context_limit_exceeded')
    parents = defaultdict(list)
    siblings = []
    for observation_id, raw, layer in context:
        entry = {'rowId': str(observation_id), 'raw': raw}
        if layer == 'T': parents[parent_key(raw)].append(entry)
        else: siblings.append(entry)
    output = []
    for observation_id, raw, split, labels in rows:
        matches = parents[parent_key(raw)]
        output.append({'rowId': str(observation_id), 'raw': raw, 'split': split,
                       'label': labels.get('label') if isinstance(labels, dict) else None,
                       'parent': matches[0] if len(matches) == 1 else None})
    return output, {'parents': [item for matches in parents.values() for item in matches],
                    'siblings': siblings, 'selection': 'latest_revision_per_execution_at_snapshot_cutoff',
                    'completeness': 'observed_context_only_unobserved_dependencies_not_proven_absent'}
