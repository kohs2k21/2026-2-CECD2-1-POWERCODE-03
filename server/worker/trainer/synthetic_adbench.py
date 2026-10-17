"""Bounded static port of ADBench's local/cluster/global feature-space method.

Source commit f7fed68dea6901fe9a81ed51b251b1d2456790cd, data_generator.py
SHA256 bd02aa9bb09d0a9302550c865e8c3e6182ed3b2491dcd0cad686e12a98ce3439.
Only the dependency branch and upstream random dataset split are excluded.
See ADBENCH_LICENSE.txt for the retained BSD 2-Clause notice.
"""
import threading

import numpy as np
from sklearn.mixture import GaussianMixture
from threadpoolctl import threadpool_limits

COMMIT = 'f7fed68dea6901fe9a81ed51b251b1d2456790cd'
SOURCE_SHA256 = 'bd02aa9bb09d0a9302550c865e8c3e6182ed3b2491dcd0cad686e12a98ce3439'
VERSION = 'adbench-static-feature-probe-v1'
_RANDOM_LOCK = threading.Lock()


def _official_generate(reference, anomaly_count, mode, seed):
    """Same GMM BIC selection and sampling equations as the pinned upstream."""
    metrics = []
    components = list(np.arange(1, 10))
    for number in components:
        mixture = GaussianMixture(n_components=number, random_state=seed).fit(reference)
        metrics.append(mixture.bic(reference))
    mixture = GaussianMixture(n_components=components[np.argmin(metrics)], random_state=seed).fit(reference)
    normal = mixture.sample(len(reference))[0]
    if mode == 'local':
        mixture.covariances_ = 5 * mixture.covariances_
        anomalies = mixture.sample(anomaly_count)[0]
    elif mode == 'cluster':
        mixture.means_ = 5 * mixture.means_
        anomalies = mixture.sample(anomaly_count)[0]
    else:
        anomalies = []
        for index in range(normal.shape[1]):
            low = np.min(normal[:, index]) * 1.1
            high = np.max(normal[:, index]) * 1.1
            anomalies.append(np.random.uniform(low=low, high=high, size=anomaly_count))
        anomalies = np.array(anomalies).T
    return np.concatenate((normal, anomalies), axis=0), np.append(np.repeat(0, len(normal)), np.repeat(1, len(anomalies)))


def generate(train, *, mode, seed, target_rows):
    if mode not in ('local', 'cluster', 'global'):
        raise ValueError('synthetic_adbench_unsupported_mode')
    matrix = np.asarray(train, dtype=np.float64)
    if matrix.ndim != 2 or not 9 <= len(matrix) <= 1000 or not 1 <= matrix.shape[1] <= 32 or not np.isfinite(matrix).all():
        raise ValueError('synthetic_adbench_invalid_training_matrix')
    if type(target_rows) is not int or not 1 <= target_rows <= 100:
        raise ValueError('synthetic_invalid_bounds')
    variable = np.ptp(matrix, axis=0) > 1e-12
    if not variable.any():
        raise ValueError('synthetic_adbench_all_dimensions_constant')
    # The pinned method uses global np.random only for uniform draws. Preserve it
    # under a lock exactly as the validated probe did; no other worker RNG changes.
    with _RANDOM_LOCK, threadpool_limits(limits=2):
        old_state = np.random.get_state()
        try:
            np.random.seed(seed)
            # Upstream's X[y == 0] creates a contiguous training copy; preserve
            # that layout so GMM reductions match the validated method exactly.
            values, labels = _official_generate(np.ascontiguousarray(matrix[:, variable]), target_rows, mode, seed)
        finally:
            np.random.set_state(old_state)
    full = np.tile(matrix[0], (len(values), 1))
    full[:, variable] = values
    if not np.isfinite(full).all() or full.shape != (len(matrix) + target_rows, matrix.shape[1]):
        raise ValueError('synthetic_adbench_invalid_generated_matrix')
    return {'values': full.tolist(), 'labels': labels.tolist(), 'generatorVersion': VERSION,
            'sourceCommit': COMMIT, 'sourceHash': SOURCE_SHA256,
            'variableFeatureIndices': np.flatnonzero(variable).tolist(),
            'constantFeatureIndices': np.flatnonzero(~variable).tolist(),
            'fitSplit': 'train', 'generationStage': 'after_frozen_feature_transform_before_frozen_scaler',
            'truthMeaning': 'generator_distribution_membership_not_ESB_fault'}


def score(labels, predictions):
    if len(labels) != len(predictions) or any(type(value) is not bool for value in predictions) or any(value not in (0, 1) for value in labels):
        raise ValueError('synthetic_adbench_invalid_predictions')
    return {str(label): {'rows': sum(value == label for value in labels),
                        'predictedAnomalyCount': sum(truth == label and predicted for truth, predicted in zip(labels, predictions)),
                        'predictedAnomalyRate': (sum(truth == label and predicted for truth, predicted in zip(labels, predictions)) /
                                                 sum(value == label for value in labels)) if label in labels else None}
            for label in (0, 1)}
