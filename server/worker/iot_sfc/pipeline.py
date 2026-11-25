"""수동 기준선 학습·독립 시험. ESB 모델과 운영 구성을 변경하지 않는다."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from pathlib import Path

from .adapter import FEATURES, PROFILE, InputError, canonical, features, iter_rows, summary

FILES = ('observations.jsonl', 'labels.jsonl', 'model.joblib')


def file_hash(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def split_rows(rows):
    """서로 다른 시각의 60/20/20 경계. 경계를 가로지르는 같은 uid는 모두 제외한다."""
    # 원문 ts는 십진수이므로 ISO 문자열의 가변 소수점 자릿수로 정렬하지 않는다.
    from decimal import Decimal
    times = sorted({Decimal(row['allowed']['ts']) for row in rows})
    if len(times) < 5:
        raise InputError('too_few_distinct_timestamps')
    calibration_at, test_at = times[int(len(times) * .6)], times[int(len(times) * .8)]
    assigned = []
    uid_roles = defaultdict(set)
    for row in rows:
        instant = Decimal(row['allowed']['ts'])
        role = 'train' if instant < calibration_at else 'calibration' if instant < test_at else 'test'
        uid = row['allowed']['uid']
        if uid:
            uid_roles[uid].add(role)
        assigned.append({**row, 'split': role})
    for row in assigned:
        uid = row['allowed']['uid']
        if not uid or len(uid_roles[uid]) > 1:
            row['split'] = 'excluded'
    return assigned, {'calibration_from_unix': str(calibration_at), 'test_from_unix': str(test_at),
                      'policy': 'distinct-time-60-20-20-drop-crossing-uids-v1'}


def prepare(csv_path: Path, output: Path, limit=20_000):
    import joblib
    import numpy as np
    import sklearn
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import RobustScaler

    if output.exists():
        raise InputError('output_already_exists')
    rows, split = split_rows(list(iter_rows(csv_path, limit)))
    train, calibration = [], []
    for row in rows:
        vector = features(row['allowed'])
        # 정상 기준선을 고르는 정답은 학습 표본 선별에만 사용한다. 모델 입력에는 넣지 않는다.
        if row['truth']['attack'] is False and vector is not None:
            if row['split'] == 'train':
                train.append(vector)
            elif row['split'] == 'calibration':
                calibration.append(vector)
    if len(train) < 500 or len(calibration) < 100:
        raise InputError('need_500_train_and_100_calibration_normal_complete_rows')
    if not any(row['split'] == 'test' for row in rows):
        raise InputError('test_split_empty')
    scaler = RobustScaler().fit(train)
    model = IsolationForest(n_estimators=100, max_samples=min(256, len(train)),
                            random_state=42, contamination='auto', n_jobs=1).fit(scaler.transform(train))
    # 시험 정답으로 임계값을 조정하지 않는다. 별도 정상 calibration의 고정 분위수를 사용한다.
    threshold = float(np.quantile(-model.score_samples(scaler.transform(calibration)), .99))
    rules = [{'feature': FEATURES[index], 'index': index,
              'upper': float(np.quantile(np.asarray(calibration)[:, index], .995))}
             for index in (0, 5, 6)]
    output.mkdir(parents=True)
    joblib.dump({'scaler': scaler, 'model': model, 'threshold': threshold, 'rules': rules}, output / 'model.joblib')
    with (output / 'observations.jsonl').open('x', encoding='utf-8') as values, \
            (output / 'labels.jsonl').open('x', encoding='utf-8') as labels:
        for row in rows:
            labels.write(canonical({'row_number': row['row_number'], **row['truth']}) + '\n')
            values.write(canonical({key: value for key, value in row.items() if key != 'truth'}) + '\n')
    manifest = {'version': 1, 'profile': PROFILE, 'run_id': str(uuid.uuid4()), 'features': list(FEATURES),
                'sklearn_version': sklearn.__version__, 'split': split,
                'training_normal_rows': len(train), 'calibration_normal_rows': len(calibration),
                'model_threshold_policy': 'normal-calibration-p99',
                'rule_policy': 'prototype-normal-calibration-upper-p995',
                'rules_are_generated_by_llm': False, 'threshold': threshold, 'summary': summary(rows),
                'hashes': {name: file_hash(output / name) for name in FILES}}
    (output / 'manifest.json').write_text(canonical(manifest) + '\n', encoding='utf-8')
    return manifest


def load_bundle(path: Path):
    """직접 생성한 로컬 번들만 사용한다. 다운로드한 외부 pickle을 읽는 기능은 제공하지 않는다."""
    import joblib
    import sklearn
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('version') != 1 or manifest.get('profile') != PROFILE
            or manifest.get('features') != list(FEATURES)
            or manifest.get('sklearn_version') != sklearn.__version__):
        raise InputError('bundle_runtime_or_profile_mismatch')
    uuid.UUID(manifest['run_id'])
    for name in FILES:
        if file_hash(path / name) != manifest['hashes'][name]:
            raise InputError('bundle_hash_mismatch')
    return manifest, joblib.load(path / 'model.joblib')


def observations(path):
    with (path / 'observations.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def labels(path):
    with (path / 'labels.jsonl').open(encoding='utf-8') as stream:
        return {row['row_number']: row for row in map(json.loads, stream)}


def decide(model, allowed):
    vector = features(allowed)
    if vector is None:
        return {'status': 'unavailable', 'anomaly_score': None, 'rule_anomaly': None,
                'ml_anomaly': None, 'anomaly': None, 'evidence': {'reason': 'numeric_input_unavailable'}}
    score = float(-model['model'].score_samples(model['scaler'].transform([vector]))[0])
    hits = [{'feature': rule['feature'], 'value': vector[rule['index']], 'upper': rule['upper']}
            for rule in model['rules'] if vector[rule['index']] > rule['upper']]
    ml_hit = score > model['threshold']
    return {'status': 'complete', 'anomaly_score': score, 'rule_anomaly': bool(hits),
            'ml_anomaly': ml_hit, 'anomaly': bool(hits) or ml_hit,
            'evidence': {'features': dict(zip(FEATURES, vector)), 'rule_hits': hits,
                         'ml_threshold': model['threshold'], 'combination': 'rule-or-if',
                         'rule_policy': 'prototype-normal-calibration-upper-p995'}}


def metrics(pairs):
    result = {'test_rows': 0, 'complete': 0, 'unavailable': 0, 'unknown_label': 0,
              'labeled_attack_rows': 0, 'unavailable_attack_rows': 0, 'unavailable_normal_rows': 0,
              'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}
    for decision, truth in pairs:
        result['test_rows'] += 1
        result[decision['status']] += 1
        if truth['attack'] is None:
            result['unknown_label'] += 1
        else:
            result['labeled_attack_rows'] += int(truth['attack'])
            if decision['status'] == 'complete':
                result[('tp' if decision['anomaly'] else 'fn') if truth['attack'] else
                       ('fp' if decision['anomaly'] else 'tn')] += 1
            else:
                result['unavailable_attack_rows' if truth['attack'] else 'unavailable_normal_rows'] += 1
    tp, fp, tn, fn = (result[key] for key in ('tp', 'fp', 'tn', 'fn'))
    result.update(precision_on_complete=tp / (tp + fp) if tp + fp else None,
                  recall_on_complete=tp / (tp + fn) if tp + fn else None,
                  false_positive_rate_on_complete=fp / (fp + tn) if fp + tn else None,
                  end_to_end_recall=tp / result['labeled_attack_rows'] if result['labeled_attack_rows'] else None,
                  coverage=result['complete'] / result['test_rows'] if result['test_rows'] else None)
    return result


def test_bundle(path: Path):
    _, model = load_bundle(path)
    truth = labels(path)
    return metrics((decide(model, row['allowed']), truth[row['row_number']])
                   for row in observations(path) if row['split'] == 'test')
