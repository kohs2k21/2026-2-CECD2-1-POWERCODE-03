"""Same-model synthetic method evaluation on immutable PostgreSQL snapshots."""
from collections import Counter
import math
import uuid

import numpy as np

import model_adapters
from detector.computation import compute, model_reference
from trainer.metrics import measurement
from trainer.selection import select_training
from trainer.synthetic_generation import fit_generator, generate
from trainer.synthetic_metrics import PATHS, VERSION, prediction, score
from trainer.synthetic_profiles import digest, validate_request
from trainer.synthetic_snapshot import MAX_COHORT_ROWS, rows_for_generation, validate_source
from trainer.synthetic_storage import store


def _model_manifest(conn, spec, context, root):
    reference = model_reference(context, root)
    model_adapters.prepare_iforest(reference)
    with conn.cursor() as cur:
        cur.execute('''SELECT cm.model_artifact_id,m.snapshot_id,m.metadata,cm.fit_artifact_id,f.snapshot_id,
                              f.metadata,c.configuration_spec,m.fit_artifact_id,s.manifest_hash,s.source_id
                       FROM ml_config_models cm JOIN ml_artifacts m ON m.id=cm.model_artifact_id
                       JOIN ml_artifacts f ON f.id=cm.fit_artifact_id
                       JOIN ml_config_versions c ON c.id=cm.config_version_id
                       JOIN data_snapshots s ON s.id=m.snapshot_id AND s.status='ready'
                       WHERE cm.config_version_id=%s''', (spec['configVersionId'],))
        rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError('synthetic_model_manifest_missing')
    model_id, model_snapshot, model_meta, fit_id, fit_snapshot, fit_meta, config, linked_fit, train_hash, source_id = rows[0]
    settings = context['models'][0][5]
    if model_snapshot != fit_snapshot or model_snapshot != uuid.UUID(config.get('trainingSnapshotId', '')) or \
            model_snapshot != uuid.UUID(settings.get('snapshotId', '')) or linked_fit != fit_id or \
            model_meta.get('trainSplit') != 'train' or fit_meta.get('trainSplit') != 'train' or \
            settings.get('split') != 'train' or source_id != uuid.UUID(spec['sourceId']):
        raise ValueError('synthetic_model_fit_lineage_invalid')
    from feature_fit import requires_fit, validate_state
    if any(requires_fit(item['expression']) for item in context['definitions']):
        import joblib
        preprocessing = joblib.load(reference['fitPath']).get('preprocessing')
        validate_state(context['definitions'], preprocessing)
        if uuid.UUID(preprocessing['reference'].get('snapshotId', '')) != model_snapshot:
            raise ValueError('synthetic_preprocessing_snapshot_mismatch')
    return reference, {'modelKey': 'iforest', 'modelArtifactId': str(model_id), 'fitArtifactId': str(fit_id),
                       'modelHash': context['models'][0][2], 'fitHash': context['models'][0][4],
                       'trainingSnapshotId': str(model_snapshot), 'trainingSnapshotHash': train_hash,
                       'featureNames': list(reference['featureNames']), 'featureSetId': context['featureSetId'],
                       'definitionsHash': reference['definitionsHash'], 'threshold': context['models'][0][6],
                       'trainingSelection': config.get('trainingSelection'),
                       'fitPolicy': 'existing_train_only_feature_state_and_scaler_no_refit',
                       'rawFeatureDefinitions': context['definitions']}


def _train_rows(conn, snapshot_id):
    with conn.cursor() as cur:
        cur.execute('''SELECT m.observation_id,o.allowed_values,m.split,m.label_evidence
                       FROM data_snapshot_members m JOIN data_observations o ON o.id=m.observation_id
                       WHERE m.snapshot_id=%s AND m.split='train' ORDER BY m.observation_id LIMIT 10001''', (snapshot_id,))
        rows = cur.fetchall()
    if len(rows) > 10000:
        raise ValueError('synthetic_training_reference_limit_exceeded')
    return rows


def _esb(conn, spec, context, rows, train, root, snapshot, baseline):
    base, parent_context = rows_for_generation(conn, rows, spec, snapshot)
    fit = fit_generator([{'rowId': str(row_id), 'raw': raw, 'split': split,
                          'label': evidence.get('label') if isinstance(evidence, dict) else None}
                         for row_id, raw, split, evidence in train])
    if baseline is None:
        baseline = {row['rowId']: prediction(compute(context, row['raw'], root)) for row in base}
    trials = []
    for repeat in range(spec['syntheticEvaluation']['repetitions']):
        seed = spec['syntheticEvaluation']['seed'] + repeat
        for item in spec['syntheticEvaluation']['scenarios']:
            generated = generate(base, fit, validate_request(spec['syntheticEvaluation']), scenario=item['scenario'],
                                 severity=item['severity'], seed=seed, target_rows=spec['syntheticEvaluation']['targetRows'],
                                 context_siblings=parent_context['siblings'])
            identities = {truth['rowId'] for truth in generated['truth']}
            post = {row['rowId']: prediction(compute(context, row['raw'], root))
                    for row in generated.pop('rows') if row['rowId'] in identities}
            generated.update(repetition=repeat + 1, predictions=post,
                             metrics=score(generated['truth'], baseline, post))
            trials.append(generated)
    artifact = {'baseRows': base, 'baseRowsHash': digest(base), 'baselinePredictions': baseline,
                'parentContext': parent_context, 'parentContextHash': digest(parent_context),
                'generatorFit': fit, 'generatorFitHash': digest(fit), 'trials': trials,
                'truthMeaning': 'controlled_injected_deviation_not_proven_customer_incident',
                'interpretation': 'service_fixed_model_method_connection_not_research_model_reproduction',
                'hrMeaning': 'actual_registered_status_HR_not_research_count_rule',
                'rulePolicy': context['policy'].document(), 'statusRulePolicy': context.get('statusRulePolicy')}
    summary_trials = [{key: value for key, value in trial.items() if key not in ('truth', 'changes', 'parentChanges', 'rejects', 'predictions')}
                      for trial in trials]
    counts = Counter(trial['status'] for trial in trials)
    artifact['uniqueInjectedProcessCount'] = len({truth['rowId'] for trial in trials for truth in trial['truth']})
    metrics = {'generated': measurement(sum(trial['generatedCount'] for trial in trials), 'count'),
               'rejected': measurement(sum(trial['rejectedCount'] for trial in trials), 'count'),
               'unsupportedTrials': measurement(counts['unsupported'], 'count'),
               'failedGenerationTrials': measurement(counts['generation_failed'], 'count')}
    for path in PATHS:
        denominator = sum(trial['metrics'][path]['injectedCount'] for trial in trials)
        numerator = sum(trial['metrics'][path]['detectedCount'] for trial in trials)
        unjudged = sum(trial['metrics'][path]['unjudgedCount'] for trial in trials)
        sr_unavailable = path == 'sr' and unjudged == denominator
        metrics[path + 'DetectionRate'] = measurement(numerator / denominator if denominator and not sr_unavailable else None, 'ratio',
            numerator=numerator, denominator=denominator, coverage=(denominator-unjudged) / denominator if denominator else None,
            reason='sr_experiment_pending' if sr_unavailable else 'no_generated_rows' if not denominator else None)
    return artifact, metrics, summary_trials, counts


def _adbench(spec, context, rows, train, root, reference):
    from trainer.synthetic_adbench import generate as generate_numeric, score as score_numeric
    selected, selection = select_training(train)
    prepared = []
    for row_id, raw, _, _ in selected:
        values, missing = model_adapters.transform_iforest_features(reference, context['definitions'], raw)
        if not missing:
            prepared.append((str(row_id), [float(values[name]) for name in reference['featureNames']]))
    rng = np.random.default_rng(spec['syntheticEvaluation']['seed'])
    indices = np.sort(rng.choice(len(prepared), 1000, replace=False)) if len(prepared) > 1000 else np.arange(len(prepared))
    training = [prepared[int(index)][1] for index in indices]
    reference_ids = [prepared[int(index)][0] for index in indices]
    threshold = context['models'][0][6]
    boundary = float(threshold['value'])
    if not math.isfinite(boundary) or threshold.get('direction') not in ('higher_is_anomaly', 'lower_is_anomaly'):
        raise ValueError('synthetic_invalid_threshold')
    trials = []
    for repeat in range(spec['syntheticEvaluation']['repetitions']):
        for item in spec['syntheticEvaluation']['scenarios']:
            seed = spec['syntheticEvaluation']['seed'] + repeat
            generated = generate_numeric(training, mode=item['scenario'], seed=seed,
                                         target_rows=min(spec['syntheticEvaluation']['targetRows'], len(rows)))
            scores = [model_adapters.score_iforest(reference, dict(zip(reference['featureNames'], values)))
                      for values in generated['values']]
            predictions = [value >= boundary if threshold['direction'] == 'higher_is_anomaly' else value <= boundary for value in scores]
            trials.append({**generated, 'scenario': item['scenario'], 'severity': 1, 'repetition': repeat + 1,
                           'seed': seed, 'status': 'generated', 'predictions': predictions,
                           'metrics': score_numeric(generated['labels'], predictions),
                           'matrixHash': digest(generated['values']), 'labelHash': digest(generated['labels'])})
    artifact = {'trainingSelection': selection, 'generatorReferenceIds': reference_ids,
                'generatorReferenceHash': digest(training), 'generatorFitSelection': 'seeded_without_replacement_max_1000_complete_training_rows',
                'featureNames': reference['featureNames'], 'trials': trials,
                'generationStage': 'after_frozen_feature_transform_before_frozen_scaler',
                'scorerStage': 'existing_frozen_scaler_then_existing_forest_once',
                'truthMeaning': 'generator_distribution_membership_not_ESB_fault',
                'testRowsUsedForFit': False, 'parentRelations': 'not_applicable_numeric_rows',
                'precision': None, 'falsePositiveRate': None, 'normalLabelReason': 'ESB_normal_truth_not_supplied'}
    summaries = [{key: value for key, value in trial.items() if key not in ('values', 'labels', 'predictions')}
                 for trial in trials]
    total = sum(trial['metrics']['1']['rows'] for trial in trials)
    hits = sum(trial['metrics']['1']['predictedAnomalyCount'] for trial in trials)
    metrics = {'generated': measurement(total, 'count'),
               'distributionOutlierDetectionRate': measurement(hits / total if total else None, 'ratio',
                    numerator=hits, denominator=total, reason=None if total else 'no_generated_rows')}
    return artifact, metrics, summaries, Counter({'generated': len(trials)})


def evaluate(conn, spec, context, rows, root, job_id, *, baseline=None):
    request = spec['syntheticEvaluation']
    profile = validate_request(request)
    if not rows or len(rows) > MAX_COHORT_ROWS:
        raise ValueError('synthetic_cohort_limit_exceeded')
    snapshot = validate_source(conn, spec, profile)
    reference, manifest = _model_manifest(conn, spec, context, root)
    train = _train_rows(conn, manifest['trainingSnapshotId'])
    if {str(row[0]) for row in train} & {str(row[0]) for row in rows}:
        raise ValueError('synthetic_training_evaluation_overlap')
    with conn.cursor() as cur:
        cur.execute('''SELECT EXISTS(
            SELECT 1 FROM data_snapshot_members tr JOIN data_observations tro ON tro.id=tr.observation_id
            JOIN data_observations ev ON ev.execution_id=tro.execution_id
            JOIN data_snapshot_members te ON te.observation_id=ev.id
            WHERE tr.snapshot_id=%s AND tr.split='train' AND te.snapshot_id=%s AND te.split=%s)''',
                    (manifest['trainingSnapshotId'], spec['snapshotId'], spec['purpose']))
        if cur.fetchone()[0]:
            raise ValueError('synthetic_training_evaluation_execution_overlap')
    conn.commit()
    original_hash = digest([(str(row[0]), row[1], row[2], row[3]) for row in rows])
    if profile['unit'] == 'PROCESS':
        artifact, metrics, trials, statuses = _esb(conn, spec, context, rows, train, root, snapshot, baseline)
    else:
        artifact, metrics, trials, statuses = _adbench(spec, context, rows, train, root, reference)
    if original_hash != digest([(str(row[0]), row[1], row[2], row[3]) for row in rows]):
        raise ValueError('synthetic_original_input_changed')
    model_reference(context, root)
    document = {'schemaVersion': 'synthetic-evaluation-artifact-v1', 'profile': profile, 'profileHash': digest(profile),
                'request': request, 'sourceBinding': spec['syntheticProfileBinding'], 'snapshot': snapshot,
                'modelManifest': manifest, 'modelManifestHash': digest(manifest),
                'originalRowsHash': original_hash,
                'featureVersion': 'server-frozen-feature-runtime-v1', 'result': artifact,
                'latency': {'value': None, 'reason': 'static_evaluation_not_ESB_latency_measurement'}}
    artifact_ref = store(root, job_id, document)
    generated = statuses['generated']
    family = {'status': 'measured' if generated else 'unavailable',
              'reason': None if generated else 'synthetic_unsupported' if statuses['unsupported'] else 'synthetic_generation_failed',
              'metricVersion': VERSION, 'metrics': metrics if generated else None,
              'details': {'profileId': profile['profileId'], 'profileVersion': profile['profileVersion'],
                          'profileHash': digest(profile), 'unit': profile['unit'], 'manifestRef': artifact_ref,
                          'trials': trials, 'trialStatuses': dict(statuses),
                          'totalTrialCount': len(trials),
                          'uniqueInjectedProcessCount': artifact.get('uniqueInjectedProcessCount'),
                          'interpretation': profile['truthMeaning'], 'scope': 'offline_synthetic_method_evaluation',
                          'normalLabelReason': 'verified_normal_labels_unavailable', 'srReason': 'sr_experiment_pending',
                          'latencyReason': 'static_evaluation_not_ESB_latency_measurement',
                          'repeatInterpretation': 'repeated_injections_not_independent_unique_PROCESS_population',
                          'operationalValidation': False}}
    return family
