"""IF on completed model-input vectors: no feature transform or scaler fit/apply."""
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from trainer.prepared_contract import MIN_TRAIN_ROWS, digest
from trainer.prepared_parser import training_eligible

FORMAT_VERSION = 'prepared-input-iforest-v1'


def input_contract(manifest):
    import uuid
    return {'sourceId': str(uuid.UUID(manifest['sourceId'])), 'sourceSchemaVersion': manifest['sourceSchemaVersion'],
            'domain': manifest['domain'], 'inputStage': manifest['inputStage'], 'features': manifest['features'],
            'preprocessing': manifest['preprocessing']}


def train(rows, manifest, output, *, dataset_id, snapshot_id):
    train_rows = [row for row in rows if row['split'] == 'train']
    if not train_rows:
        raise ValueError('prepared_train_split_unavailable')
    selected = [row for row in train_rows if training_eligible(row)]
    if not selected:
        raise ValueError('prepared_training_eligibility_unavailable')
    if len(selected) < MIN_TRAIN_ROWS:
        raise ValueError('prepared_fit_calibration_population_insufficient')
    matrix = np.asarray([row['features'] for row in selected], dtype=np.float64)
    if not np.isfinite(matrix).all() or len(np.unique(matrix, axis=0)) < 2:
        raise ValueError('prepared_invalid_training_matrix')
    order = np.random.default_rng(42).permutation(len(selected))
    threshold_count = max(100, int(np.ceil(len(selected)*.2)))
    fit_indices, threshold_indices = order[:-threshold_count], order[-threshold_count:]
    if len(fit_indices) < 500:
        raise ValueError('prepared_insufficient_eligible_training_rows')
    forest = IsolationForest(n_estimators=128, max_samples=min(512, len(fit_indices)), contamination='auto',
                             max_features=1.0, bootstrap=False, random_state=42, n_jobs=2).fit(matrix[fit_indices])
    threshold = float(np.quantile(-forest.score_samples(matrix[threshold_indices]), .99))
    descriptor = input_contract(manifest)
    names = [feature['name'] for feature in manifest['features']]
    reference = {'datasetId': str(dataset_id), 'snapshotId': str(snapshot_id), 'split': 'train',
                 'trainRowIdsHash': digest(sorted(row['rowId'] for row in train_rows)),
                 'trainRowIds': sorted(row['rowId'] for row in train_rows),
                 'trainOriginIds': sorted({row['originRowId'] for row in train_rows if row['originRowId'] is not None}),
                 'trainGroupIds': sorted({row['groupId'] for row in train_rows if row['groupId'] is not None}),
                 'eligibleRowIdsHash': digest(sorted(row['rowId'] for row in selected)),
                 'eligibleRowIds': sorted(row['rowId'] for row in selected),
                 'eligibleOriginIds': sorted({row['originRowId'] for row in selected if row['originRowId'] is not None}),
                 'eligibleGroupIds': sorted({row['groupId'] for row in selected if row['groupId'] is not None}),
                 'modelFitRowIdsHash': digest(sorted(selected[int(index)]['rowId'] for index in fit_indices)),
                 'thresholdRowIdsHash': digest(sorted(selected[int(index)]['rowId'] for index in threshold_indices))}
    metadata = {'formatVersion': FORMAT_VERSION, 'sklearnVersion': sklearn.__version__, 'featureNames': names,
                'inputContract': descriptor, 'inputContractHash': digest(descriptor), 'trainingReference': reference,
                'additionalTransformApplied': False}
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    model_path, fit_path = output/'model.joblib', output/'fit.joblib'
    joblib.dump({**metadata, 'forest': forest}, model_path)
    joblib.dump(metadata, fit_path)
    return {'modelPath': str(model_path), 'fitPath': str(fit_path), 'formatVersion': FORMAT_VERSION,
            'runtimeVersion': sklearn.__version__, 'inputContract': descriptor, 'trainingReference': reference,
            'thresholdSpec': {'direction': 'higher_is_anomaly', 'value': str(threshold), 'quantile': .99,
                              'referenceCount': len(threshold_indices)},
            'trainingSelection': {'policyVersion': 'prepared-train-success-explicit-nonsynthetic-v1',
                                  'populationCount': len(train_rows), 'selectedCount': len(selected),
                                  'excludedCount': len(train_rows)-len(selected), 'normalGroundTruth': False}}


def load(model_path, fit_path, manifest, settings):
    descriptor = input_contract(manifest)
    if settings.get('preparedInputContract') != descriptor:
        raise ValueError('prepared_model_input_contract_mismatch')
    model, fit = joblib.load(model_path), joblib.load(fit_path)
    for artifact in (model, fit):
        if not isinstance(artifact, dict) or artifact.get('formatVersion') != FORMAT_VERSION or artifact.get('sklearnVersion') != sklearn.__version__ or \
                artifact.get('inputContract') != descriptor or artifact.get('inputContractHash') != digest(descriptor) or \
                artifact.get('featureNames') != [feature['name'] for feature in manifest['features']] or \
                artifact.get('additionalTransformApplied') is not False:
            raise ValueError('prepared_model_artifact_incompatible')
    if model['trainingReference'] != fit['trainingReference'] or model['trainingReference'] != settings.get('preparedTrainingReference'):
        raise ValueError('prepared_model_fit_lineage_mismatch')
    forest = model.get('forest')
    if not isinstance(forest, IsolationForest) or forest.n_features_in_ != len(descriptor['features']):
        raise ValueError('prepared_model_feature_schema_mismatch')
    return forest, fit['trainingReference']


def scores(forest, rows):
    matrix = np.asarray([row['features'] for row in rows], dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != forest.n_features_in_ or not np.isfinite(matrix).all():
        raise ValueError('prepared_model_feature_schema_mismatch')
    values = -forest.score_samples(matrix)
    if not np.isfinite(values).all(): raise ValueError('prepared_invalid_model_score')
    return values.tolist()


def check_evaluation_lineage(rows, reference):
    # External preprocessing can fit rows excluded from the forest selection.
    if {row['rowId'] for row in rows} & set(reference['trainRowIds']):
        raise ValueError('prepared_evaluation_training_row_overlap')
    training_aliases = set(reference['trainOriginIds']) | set(reference['trainRowIds'])
    evaluation_aliases = {row['rowId'] for row in rows} | {row['originRowId'] for row in rows if row['originRowId'] is not None}
    if evaluation_aliases & training_aliases:
        raise ValueError('prepared_evaluation_training_origin_overlap')
    if {row['groupId'] for row in rows if row['groupId'] is not None} & set(reference['trainGroupIds']):
        raise ValueError('prepared_evaluation_training_group_overlap')


def check_scaling_lineage(manifest, reference, dataset_id):
    if str(dataset_id)==reference['datasetId'] or manifest['preprocessing']['scaling']=='none': return
    fit=manifest['preprocessing']
    if fit['fitHash'] is None or fit['fitSplit']!='train' or fit['trainRowIdsHash']!=reference['trainRowIdsHash']:
        raise ValueError('prepared_cross_dataset_scaling_fit_unverified')
