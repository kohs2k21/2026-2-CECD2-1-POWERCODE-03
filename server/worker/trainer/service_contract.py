"""Persisted Trainer result identifiers, separate from metric calculation."""
CONTRACT_VERSION = 'trainer-service-v1'
FAMILIES = ('rule_ml', 'synthetic', 'execution_cost', 'operational')


def evaluation_envelope():
    return {key: {'status': 'unavailable', 'reason': 'not_implemented', 'metrics': None,
                  'metricVersion': 'trainer-envelope-v1'} for key in FAMILIES}


def enrich_result(conn, job, result):
    """Inside the successful job transaction; no active-version mutation."""
    snapshot_id = result.get('snapshotId') or job['input'].get('snapshotId')
    config_id = result.get('configVersionId') or job['input'].get('configVersionId')
    with conn.cursor() as cur:
        cur.execute('''SELECT source_id,manifest_hash,schema_version,selection_spec,split_spec
                       FROM data_snapshots WHERE id=%s AND status='ready' ''', (snapshot_id,))
        snapshot = cur.fetchone()
        if snapshot is None:
            raise ValueError('result_snapshot_missing')
        cur.execute('''SELECT c.configuration_spec,f.content_hash FROM ml_config_versions c
                       LEFT JOIN ml_feature_sets f ON f.id::text=c.configuration_spec->>'featureSetId'
                       WHERE c.id=%s''', (config_id,))
        config = cur.fetchone()
        if config is None:
            raise ValueError('result_config_missing')
        cur.execute('''SELECT cm.model_key,cm.model_artifact_id,m.content_hash,cm.fit_artifact_id,f.content_hash,
                     cm.feature_set_id,cm.training_settings,cm.threshold_spec
                     FROM ml_config_models cm JOIN ml_artifacts m ON m.id=cm.model_artifact_id
                     JOIN ml_artifacts f ON f.id=cm.fit_artifact_id WHERE cm.config_version_id=%s''', (config_id,))
        models = cur.fetchall()
    if len(models) > 1:
        raise ValueError('unsupported_model_combination')
    manifest = None
    if models:
        key, model_id, model_hash, fit_id, fit_hash, feature_id, settings, threshold = models[0]
        manifest = {'modelKey': key, 'modelArtifactId': str(model_id), 'modelHash': model_hash,
                    'fitArtifactId': str(fit_id), 'fitHash': fit_hash, 'featureSetId': str(feature_id),
                    'featureNames': settings.get('featureNames'), 'definitionsHash': settings.get('definitionsHash'),
                    'thresholdSpec': threshold}
    result.update({'serviceContractVersion': CONTRACT_VERSION, 'snapshotId': str(snapshot_id),
                   'configVersionId': str(config_id), 'modelManifest': manifest,
                   'dataContract': {'sourceId': str(snapshot[0]), 'snapshotId': str(snapshot_id),
                                    'snapshotHash': snapshot[1], 'schemaVersion': snapshot[2],
                                    'selectionSpec': snapshot[3], 'splitSpec': snapshot[4],
                                    'featureSetId': config[0].get('featureSetId'),
                                    'featureSetHash': config[1],
                                    'ruleVersionIds': config[0].get('ruleVersionIds', [])},
                   'evaluationEnvelope': evaluation_envelope()})
