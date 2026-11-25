"""별도 IoT 저장소와 기존 PG 임대·완료 로직을 사용하는 수동 재생 시험."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from pg_queue import acknowledge, claim, enqueue, fail
from .adapter import InputError, canonical, digest
from .pipeline import decide, labels, load_bundle, metrics, observations


def observation_id(run_id, row_number):
    return uuid.uuid5(uuid.UUID(run_id), str(row_number))


def check_run(conn, manifest):
    row = conn.execute('SELECT model_hash,manifest_hash FROM powercode_iot.runs WHERE id=%s',
                       (manifest['run_id'],)).fetchone()
    if row is None or row != (manifest['hashes']['model.joblib'], digest(manifest)):
        raise InputError('stored_bundle_mismatch')


def replay(conn, path: Path, rate=100):
    if not 0 <= rate <= 10_000:
        raise InputError('rate_out_of_range')
    manifest, _ = load_bundle(path)
    run_id = manifest['run_id']
    truth = labels(path)
    with conn.transaction():
        conn.execute('''INSERT INTO powercode_iot.runs(id,model_hash,manifest_hash,metadata)
            VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (id) DO NOTHING''',
            (run_id, manifest['hashes']['model.joblib'], digest(manifest), canonical(manifest)))
        check_run(conn, manifest)
    started, stored, queued, duplicates = time.monotonic(), 0, 0, 0
    for count, row in enumerate(observations(path), 1):
        ident = observation_id(run_id, row['row_number'])
        content_hash = digest(row)
        with conn.transaction():
            inserted = conn.execute('''INSERT INTO powercode_iot.observations
                (id,run_id,row_number,source_time,split,content_hash,allowed_values,quality_flags)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT (id) DO NOTHING RETURNING id''',
                (ident, run_id, row['row_number'], datetime.fromisoformat(row['source_time']), row['split'],
                 content_hash, canonical(row['allowed']), canonical(row['quality_flags']))).fetchone()
            if inserted is None:
                previous = conn.execute('SELECT content_hash FROM powercode_iot.observations WHERE id=%s',
                                        (ident,)).fetchone()
                if previous != (content_hash,):
                    raise InputError('observation_idempotency_conflict')
            expected = truth[row['row_number']]
            conn.execute('''INSERT INTO powercode_iot.ground_truth(observation_id,attack,attack_type)
                VALUES (%s,%s,%s) ON CONFLICT (observation_id) DO NOTHING''',
                (ident, expected['attack'], expected['attack_type']))
            actual = conn.execute('SELECT attack,attack_type FROM powercode_iot.ground_truth WHERE observation_id=%s',
                                  (ident,)).fetchone()
            if actual != (expected['attack'], expected['attack_type']):
                raise InputError('label_idempotency_conflict')
            if row['split'] == 'test':
                # 저장과 큐 등록을 같은 트랜잭션에 둔다. 같은 번들의 재생은 큐도 멱등이다.
                enqueue(conn, 'iot_sfc', {'benchmarkRunId': run_id, 'observationId': str(ident)}, message_id=ident)
                queued += int(inserted is not None)
        stored += int(inserted is not None)
        duplicates += int(inserted is None)
        if rate:
            time.sleep(max(0, started + count / rate - time.monotonic()))
    return {'run_id': run_id, 'stored': stored, 'duplicate': duplicates, 'queued': queued}


def detect(conn, path: Path, max_messages=100_000):
    manifest, model = load_bundle(path)
    run_id = manifest['run_id']
    with conn.transaction():
        check_run(conn, manifest)
    completed = 0
    for _ in range(max_messages):
        with conn.transaction():
            message = claim(conn, 'iot_sfc', benchmark_run_id=run_id)
        if message is None:
            break
        try:
            # 평가 정답 테이블은 조회하지 않는다. 여기서는 허용 관측값만 읽는다.
            with conn.transaction():
                row = conn.execute('''SELECT allowed_values FROM powercode_iot.observations
                    WHERE id=%s AND run_id=%s AND split='test' ''',
                    (message.id, run_id)).fetchone()
            if (row is None or message.payload != {'benchmarkRunId': run_id, 'observationId': str(message.id)}):
                raise InputError('invalid_detection_input')
            decision = decide(model, row[0])
            with conn.transaction():
                # 유효한 임대의 ack와 결과를 함께 확정한다. 만료·교체된 소비자의 결과는 저장하지 않는다.
                if not acknowledge(conn, 'iot_sfc', message):
                    raise InputError('lease_lost')
                conn.execute('''INSERT INTO powercode_iot.decisions
                    (observation_id,run_id,status,anomaly_score,rule_anomaly,ml_anomaly,anomaly,evidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (observation_id) DO NOTHING''',
                    (message.id, run_id, decision['status'], decision['anomaly_score'], decision['rule_anomaly'],
                     decision['ml_anomaly'], decision['anomaly'], canonical(decision['evidence'])))
            completed += 1
        except Exception:
            with conn.transaction():
                fail(conn, 'iot_sfc', message, reason='iot_detection_failed')
            raise InputError('iot_detection_failed') from None
    return {'run_id': run_id, 'completed_this_call': completed}


def report(conn, path: Path):
    manifest, _ = load_bundle(path)
    run_id = manifest['run_id']
    with conn.transaction():
        check_run(conn, manifest)
        queue = dict(conn.execute('''SELECT status,count(*) FROM powercode_iot.detection_queue
            WHERE payload->>'benchmarkRunId'=%s GROUP BY status''', (run_id,)).fetchall())
        pairs = conn.execute('''SELECT d.status,d.anomaly,g.attack FROM powercode_iot.decisions d
            JOIN powercode_iot.ground_truth g ON g.observation_id=d.observation_id WHERE d.run_id=%s''',
            (run_id,)).fetchall()
        total = conn.execute('''SELECT count(*) FROM powercode_iot.observations
            WHERE run_id=%s AND split='test' ''', (run_id,)).fetchone()[0]
    result = metrics(({'status': status, 'anomaly': anomaly}, {'attack': truth}) for status, anomaly, truth in pairs)
    return {'run_id': run_id, 'queue': queue, 'stored_test_rows': total, 'decided': len(pairs),
            'undecided': total - len(pairs), 'metrics_for_decided_rows': result,
            'complete': total > 0 and total == len(pairs) and not any(queue.get(key, 0) for key in ('pending', 'leased', 'dead'))}
