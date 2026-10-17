"""PROCESS truth and paired denominators, including every unjudged injection."""
PATHS = ('hr', 'sr', 'ml', 'combined')
VERSION = 'synthetic-process-metrics-v1'


def prediction(result):
    values = {key: result[key].get('anomalous') if result[key].get('status') == 'succeeded' else None
              for key in ('hr', 'sr', 'ml')}
    values['combined'] = result.get('anomalous') if result.get('status') == 'complete' else None
    return values


def score(truth, before, after):
    ids = [item['rowId'] for item in truth]
    if len(ids) != len(set(ids)) or any(item.get('label') != 1 for item in truth):
        raise ValueError('synthetic_invalid_truth')
    if not set(ids).issubset(before) or not set(after).issubset(before):
        raise ValueError('synthetic_identity_outside_baseline')
    for predictions in (before, after):
        if any(value is not None and type(value) is not bool for row in predictions.values() for value in row.values()):
            raise ValueError('synthetic_invalid_prediction')
    reports = {}
    for path in PATHS:
        pairs = [(before[key].get(path), after.get(key, {}).get(path)) for key in ids]
        detected = sum(post is True for _, post in pairs)
        reports[path] = {'injectedCount': len(ids), 'detectedCount': detected,
                         'unjudgedCount': sum(post is None for _, post in pairs),
                         'detectionRate': detected / len(ids) if ids else None,
                         'baselineAlertCount': sum(pre is True for pre, _ in pairs),
                         'newlyDetectedCount': sum(pre is False and post is True for pre, post in pairs),
                         'persistentAlertCount': sum(pre is True and post is True for pre, post in pairs),
                         'lostAlertCount': sum(pre is True and post is False for pre, post in pairs),
                         'unchangedNonalertCount': sum(pre is False and post is False for pre, post in pairs),
                         'pairedUnjudgedCount': sum(pre is None or post is None for pre, post in pairs),
                         'precision': None, 'falsePositiveRate': None,
                         'normalLabelReason': 'verified_normal_labels_unavailable',
                         'reason': 'sr_experiment_pending' if path == 'sr' else 'no_generated_rows' if not ids else None}
    return reports
