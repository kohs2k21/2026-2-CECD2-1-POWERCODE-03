"""Independent Issue58 real CPU/HTTP/PG QA; fresh localhost QA database only.

Run after baseline migrations and API build, with PIPELINE_TEST_DATABASE_URL
pointing to qa_metrics_service. Never reads repository dotenv or account files.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import unittest
from types import SimpleNamespace
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

import joblib
import jsonschema
import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration_research_pipeline import source, observe
from trainer.catalog import register_feature_set
from trainer import runner
import model_adapters

REPO = Path(__file__).resolve().parents[3]
FIELD = {'op': 'field', 'field': 'P.total_count'}
DEFINITIONS = [
    {'name': 'count_raw', 'expression': FIELD},
    {'name': 'count_centered', 'expression': {
        'op': 'group_centered', 'groupBy': 'P.adapter_type', 'value': FIELD}},
]


class Api:
    def __init__(self, root, dsn):
        self.root, self.dsn = Path(root), dsn
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        with socket.socket() as socket_:
            socket_.bind(('127.0.0.1', 0))
            port = socket_.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP') if k in os.environ}
        self.env.update(DATABASE_URL=dsn, NODE_ENV='test', PORT=str(port),
            PYTHONUTF8='1', PYTHON_DOTENV_DISABLED='1',
            PYTHONPATH=str(REPO / 'server/worker'), ARTIFACT_ROOT=str(self.artifacts),
            JWT_SECRET='isolated-issue58-qa-runtime-only',
            USER_DATA_PATH=str(self.root / 'temporary-accounts.json'),
            SEED_ADMIN_EMAIL='metrics-admin@example.test', SEED_ADMIN_PASSWORD='runtime-test-58',
            SEED_USER_EMAIL='metrics-user@example.test', SEED_USER_PASSWORD='runtime-test-58')
        self.log = (self.root / 'api.log').open('w', encoding='utf-8')
        self.child = subprocess.Popen(['node', str(REPO / 'server/backend/dist/index.js')],
            cwd=self.root, env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                if self.request('/health')[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(.1)
        else:
            self.close()
            raise AssertionError('isolated API startup failed')
        self.admin = self.request('/api/auth/login', {
            'email': 'metrics-admin@example.test', 'password': 'runtime-test-58'})[1]['token']
        self.user = self.request('/api/auth/login', {
            'email': 'metrics-user@example.test', 'password': 'runtime-test-58'})[1]['token']

    def request(self, path, body=None, token=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if key:
            headers['Idempotency-Key'] = key
        request = Request(self.base + path, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def worker(self):
        result = subprocess.run([sys.executable, '-m', 'trainer.runner', '--once'],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr[-1500:]

    def submit(self, spec):
        status, job = self.request('/api/jobs', spec, self.admin, str(uuid.uuid4()))
        assert status == 201, (status, job)
        self.worker()
        status, job = self.request('/api/jobs/' + job['id'], token=self.admin)
        assert status == 200 and job['status'] == 'succeeded', (status, job)
        return job

    def close(self):
        if self.child.poll() is None:
            self.child.terminate()
            self.child.wait(timeout=15)
        self.log.close()


def seed(conn):
    """2,000 independent executions; >1,000 selected S train candidates.

    Non-S and explicit-anomaly S train rows have extreme counts. Validation is
    entirely unlabeled and includes F/P/N. Test also has unknown/missing status.
    """
    sid = source(conn, 'Issue58 controlled data; no customer input')
    conn.execute("UPDATE ops_sources SET ingestion_method='customer-api' WHERE id=%s", (sid,))
    conn.commit()
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for i in range(2000):
        validation = 1400 <= i < 1700
        status = {0: 'F', 1: 'P', 2: 'N', 3: 'UNKNOWN', 4: None}.get(i % 20, 'S')
        if validation and status in ('UNKNOWN', None):
            status = 'S'
        count = 50 + i % 13
        anomaly_label = i < 1400 and status == 'S' and i % 97 == 0
        if i < 1400 and (status != 'S' or anomaly_label):
            count = 10**12 if anomaly_label else 10**9
        elif i >= 1400 and (status == 'P' or (status == 'S' and i % 17 == 0)):
            count = 10**9
        raw = {'adapter_type': 'A' if i % 2 else 'B', 'total_count': str(count),
               'success_count': str(count), 'error_count': '0'}
        if status is not None:
            raw['status'] = status
        observe(conn, sid, f'issue58-{uuid.uuid4()}-{i}', raw,
                'anomaly' if anomaly_label else None, persisted=start + timedelta(seconds=i))
    features = register_feature_set(conn, DEFINITIONS, 'independent-qa', definition_kind='operator_defined')
    conn.commit()
    return {'kind': 'train', 'sourceId': str(sid), 'featureSetId': str(features),
            'from': (start - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
            'until': (start + timedelta(seconds=2100)).isoformat().replace('+00:00', 'Z'),
            'detectionPolicy': {'mode': 'parallel', 'deadlineMs': 30000, 'combination': 'any_anomaly'}}


def snapshot_rows(conn, snapshot, split):
    rows = conn.execute('''SELECT o.allowed_values,m.label_evidence FROM data_snapshot_members m
        JOIN data_observations o ON o.id=m.observation_id WHERE m.snapshot_id=%s AND m.split=%s
        ORDER BY m.observation_id''', (snapshot, split)).fetchall()
    conn.commit()
    return rows


def artifacts(conn, config, root):
    row = conn.execute('''SELECT m.relative_path,f.relative_path,c.threshold_spec FROM ml_config_models c
        JOIN ml_artifacts m ON m.id=c.model_artifact_id JOIN ml_artifacts f ON f.id=c.fit_artifact_id
        WHERE c.config_version_id=%s''', (config,)).fetchone()
    conn.commit()
    return joblib.load(root / row[0]), joblib.load(root / row[1]), row[2]


def verify_training(conn, api, spec, result):
    rows = snapshot_rows(conn, result['snapshotId'], 'train')
    selected = [raw for raw, label in rows if raw.get('status') == 'S'
                and not (isinstance(label, dict) and label.get('label') == 'anomaly')]
    selection = result['trainingSelection']
    assert len(selected) >= 1000
    assert selection['selectedCount'] == len(selected)
    assert selection['populationCount'] == len(rows) == 1400
    assert selection['excludedCount'] == len(rows) - len(selected)
    assert selection['excludedExplicitAnomalyCount'] > 0
    assert selection['normalGroundTruth'] is False
    model, fit, _ = artifacts(conn, result['configVersionId'], api.artifacts)
    medians = {group: float(np.median([float(row['total_count']) for row in selected
               if row['adapter_type'] == group])) for group in ('A', 'B')}
    actual_medians = {key: float(value) for key, value in
                      fit['preprocessing']['features']['count_centered']['groups'].items()}
    assert actual_medians == medians, (actual_medians, medians)
    assert sum(fit['preprocessing']['features']['count_centered']['referenceCounts'].values()) == len(selected)
    reference_rows = [{'features': {'count_raw': float(row['total_count']),
        'count_centered': float(row['total_count']) - medians[row['adapter_type']]},
        'missing': [], 'label': None} for row in selected]
    reference = model_adapters.train_iforest(reference_rows, ['count_raw', 'count_centered'],
        str(api.root / 'independent-reference-model'))
    forest = joblib.load(reference['modelPath'])['forest']
    assert all(np.array_equal(a.tree_.threshold, b.tree_.threshold)
               for a, b in zip(model['forest'].estimators_, forest.estimators_))
    for split in ('validation', 'test'):
        statuses = Counter(raw.get('status') for raw, _ in snapshot_rows(conn, result['snapshotId'], split))
        assert all(statuses[s] > 0 for s in ('S', 'F', 'P', 'N')), statuses
    print('PASS actual S-only group fit/forest, explicit anomaly exclusion, full F/P/N evaluation population', flush=True)


def verify_evaluation(conn, api, spec, train):
    result = train['resultSummary']
    model, fit, threshold = artifacts(conn, result['configVersionId'], api.artifacts)
    schema = json.loads((REPO / 'server/contracts/trainer-evaluation-envelope.schema.json').read_text())
    outputs = {}
    for split in ('validation', 'test'):
        job = api.submit({'kind': 'evaluate', 'sourceId': spec['sourceId'],
            'configVersionId': result['configVersionId'], 'snapshotId': result['snapshotId'], 'purpose': split})
        summary = job['resultSummary']
        rows = snapshot_rows(conn, result['snapshotId'], split)
        groups = {key: float(value) for key, value in
                  fit['preprocessing']['features']['count_centered']['groups'].items()}
        # Independent raw-count -> saved sklearn prediction. No comparison/compute helper.
        vectors = [[float(raw['total_count']), float(raw['total_count']) - groups[raw['adapter_type']]]
                   for raw, _ in rows]
        scores = -model['forest'].score_samples(fit['scaler'].transform(vectors))
        counts = Counter()
        known = 0
        for (raw, _), score in zip(rows, scores):
            status = raw.get('status')
            if status not in ('S', 'F', 'P', 'N'):
                continue
            known += 1
            hr, ml = status in ('F', 'P', 'N'), float(score) >= float(threshold['value'])
            counts[(hr, ml)] += 1
        metrics = summary['evaluationEnvelope']['rule_ml']['metrics']
        keys = {(True, True): 'hrBoth', (True, False): 'hrRuleOnly',
                (False, True): 'hrMlOnly', (False, False): 'hrNeither'}
        for pair, key in keys.items():
            assert metrics[key]['numerator'] == counts[pair], (split, key, metrics[key], counts)
            assert metrics[key]['denominator'] == known
            assert metrics[key]['value'] == counts[pair] / known
            assert metrics[key]['coverage'] == known / len(rows)
        assert metrics['hrMissing']['value'] == len(rows) - known
        assert metrics['srBoth']['value'] is None and metrics['srBoth']['reason'] == 'sr_experiment_pending'
        assert summary['populationCount'] == len(rows) == 300
        assert summary['unlabeledCount'] == 300 and summary['metrics']['precision'] is None
        jsonschema.Draft202012Validator(schema).validate(summary['evaluationEnvelope'])
        cost = summary['evaluationEnvelope']['execution_cost']
        assert cost['metrics']['evaluationMs']['value'] > 0
        assert cost['metrics']['esbLatencyMs']['value'] is None
        assert cost['metrics']['esbLatencyMs']['reason'] == 'source_clock_unverified'
        assert cost['details']['memoryScope'] == 'process_current_rss_sample'
        assert summary['applicability'] == ('valid' if split == 'validation' else 'unmeasured')
        outputs[split] = job
        print(f'PASS actual IF vs independent HR/sklearn hand table {split}: {dict(counts)}, missing={len(rows)-known}', flush=True)
    return outputs


def verify_detector(conn, api, spec, result):
    from detector.jobs import enqueue_observation
    from detector.runner import process_detection_message
    from pg_queue import claim
    source_id, config = uuid.UUID(spec['sourceId']), uuid.UUID(result['configVersionId'])
    identifiers = []
    for status in ('S', 'F', 'P', 'N', 'UNKNOWN', None):
        raw = {'adapter_type': 'A', 'total_count': '55', 'success_count': '55', 'error_count': '0'}
        if status is not None:
            raw['status'] = status
        observation = observe(conn, source_id, f'live-{uuid.uuid4()}', raw)
        payload = enqueue_observation(conn, observation.observation_id, config_id=config)
        conn.commit()
        message = claim(conn, 'detection', message_id=uuid.UUID(payload['eventId']))
        conn.commit()
        with patch.dict(os.environ, api.env):
            process_detection_message(conn, message, api.artifacts)
            conn.commit()
        row = conn.execute('''SELECT id,evidence,latency_status,source_latency_ms FROM run_process_decisions
            WHERE process_observation_id=%s AND config_version_id=%s''', (observation.observation_id, config)).fetchone()
        conn.commit()
        assert row is not None
        evidence = row[1]
        hr = evidence['checkers']['hr']
        assert evidence['checkers']['sr']['anomalous'] is None
        assert evidence['checkers']['sr']['evidence'][0]['reason'] == 'sr_experiment_pending'
        if status in ('S', 'F', 'P', 'N'):
            assert hr['anomalous'] is (status != 'S')
            expected_severity = None if status == 'S' else 'critical' if status == 'F' else 'warning'
            assert hr['evidence'][0]['severity'] == expected_severity
        else:
            assert hr['status'] == 'not_ready' and hr['anomalous'] is None
        cost = evidence['telemetry']
        assert cost['metrics']['totalMs']['value'] > 0
        assert evidence['checkers']['ml']['status'] == 'succeeded'
        assert cost['metrics']['inferenceMs']['value'] >= 0
        assert cost['metrics']['queueWaitMs']['value'] >= 0
        assert cost['metrics']['esbLatencyMs']['value'] is None
        assert row[2:] == ('unavailable', None)
        identifiers.append(str(row[0]))
        # The same manifest/config cannot register another detection job or decision.
        repeated = enqueue_observation(conn, observation.observation_id, config_id=config)
        conn.commit()
        assert repeated['eventId'] == payload['eventId']
        assert conn.execute('SELECT count(*) FROM run_process_decisions WHERE job_id=%s',
            (payload['jobId'],)).fetchone()[0] == 1
        conn.commit()
    code, response = api.request('/api/pipeline/decisions?sourceId=' + str(source_id) + '&limit=100', token=api.user)
    assert code == 200 and all(any(row['id'] == identifier for row in response['items']) for identifier in identifiers)
    print('PASS real Detector SFPN/unknown/missing checker evidence, inference/queue cost, unknown source clock, duplicate job, user HTTP decisions', flush=True)


def fixed_decision(conn, source_id, config, *, at, status='complete', anomaly=False,
                   hr=False, ml=False, failed=False, missing_metadata=False, sr_reason=None):
    observation = observe(conn, source_id, f'report-{uuid.uuid4()}',
        {'adapter_type': 'A', 'total_count': '55', 'status': 'S'}, persisted=at - timedelta(seconds=10))
    execution = conn.execute('SELECT execution_id FROM data_observations WHERE id=%s',
                            (observation.observation_id,)).fetchone()[0]
    evidence = {'anomalous': anomaly}
    if not missing_metadata:
        evidence['checkers'] = {
            'hr': {'status': 'succeeded' if hr is not None else 'not_ready', 'anomalous': hr},
            'ml': {'status': 'failed' if failed else 'succeeded' if ml is not None else 'not_ready', 'anomalous': ml},
            'sr': {'status': 'not_ready', 'anomalous': None}}
        if sr_reason:
            evidence['checkers']['sr']['evidence'] = [{'reason': sr_reason}]
        # Malformed historical telemetry must be ignored rather than cast/SQL errors.
        evidence['telemetry'] = {'metricVersion': 'execution-cost-v1', 'status': 'measured', 'metrics': {
            'totalMs': {'value': 'not-a-number', 'unit': 'ms'},
            'inferenceMs': {'value': -3, 'unit': 'ms'},
            'ruleMs': {'value': 2, 'unit': 'bytes'}}}
    conn.execute('''INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,
        input_manifest_hash,status,rule_status,ml_status,evidence,decided_at,latency_status)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'unavailable')''',
        (uuid.uuid4(), execution, observation.observation_id, config, str(uuid.uuid4()), status,
         'succeeded' if hr is not None else 'not_ready', 'failed' if failed else 'succeeded' if ml is not None else 'not_ready',
         json.dumps(evidence), at))
    conn.commit()
    return observation.observation_id, execution


def verify_sse_and_time_boundaries(conn, api, query):
    path = '/api/pipeline/reports/operational?'
    status, before = api.request(path + urlencode(query), token=api.admin)
    assert status == 200
    events = conn.execute('SELECT count(*) FROM run_decision_events').fetchone()[0]
    conn.commit()
    for _ in range(2):
        request = Request(api.base + '/api/pipeline/events', headers={
            'Authorization': 'Bearer ' + api.user, 'Last-Event-ID': '0'})
        with urlopen(request, timeout=10) as response:
            sample = response.read(256)
            assert b'event: decision' in sample
    assert conn.execute('SELECT count(*) FROM run_decision_events').fetchone()[0] == events
    conn.commit()
    status, after = api.request(path + urlencode(query), token=api.admin)
    assert status == 200 and after['items'] == before['items']
    tiny = {**query, 'from': '2026-10-02T00:00:00.000001Z', 'until': '2026-10-02T00:00:00.000002Z'}
    assert api.request(path + urlencode(tiny), token=api.admin)[0] == 200
    excessive = {**query, 'from': '2026-10-02T00:00:00Z', 'until': '2026-11-02T00:00:00.000001Z'}
    assert api.request(path + urlencode(excessive), token=api.admin)[0] == 400
    print('PASS actual repeated SSE replay leaves report/event counts unchanged, 1us period accepted and 31days+1us rejected', flush=True)


def verify_operational_reports(conn, api, spec, result):
    from collector.storage import ingest_observation
    sid, config = uuid.UUID(spec['sourceId']), uuid.UUID(result['configVersionId'])
    utc = timezone.utc
    day = datetime(2026, 10, 2, 15, tzinfo=utc)
    first, first_exec = fixed_decision(conn, sid, config, at=day - timedelta(seconds=1), anomaly=True, hr=True)
    fixed_decision(conn, sid, config, at=day, anomaly=False)
    fixed_decision(conn, sid, config, at=day + timedelta(seconds=1), status='unavailable', anomaly=False, hr=None, ml=None)
    fixed_decision(conn, sid, config, at=day + timedelta(hours=1), status='partial', anomaly=True, hr=True, ml=None, failed=True)
    fixed_decision(conn, sid, config, at=day + timedelta(hours=2), status='partial', anomaly=False, hr=False, ml=None)
    fixed_decision(conn, sid, config, at=day + timedelta(hours=3), missing_metadata=True)
    old, execution = fixed_decision(conn, sid, config, at=day + timedelta(hours=4), anomaly=True, hr=True)
    # A later revision with no decision must hide the older alarm and appear pending.
    newer = ingest_observation(conn, source_id=sid, layer='P', dedup_key=str(uuid.uuid4()),
        raw={'adapter_type': 'A', 'total_count': '56', 'status': 'S'}, execution_id=execution, revision_no=2,
        detection_eligible=False)
    conn.execute('UPDATE data_observations SET persisted_at=%s WHERE id=%s',
        (day + timedelta(hours=5), newer.observation_id))
    # A revision beyond the report cutoff cannot hide its earlier in-period decision.
    future = ingest_observation(conn, source_id=sid, layer='P', dedup_key=str(uuid.uuid4()),
        raw={'adapter_type': 'A', 'total_count': '57', 'status': 'S'}, execution_id=first_exec, revision_no=2,
        detection_eligible=False)
    conn.execute('UPDATE data_observations SET persisted_at=%s WHERE id=%s',
        (datetime(2026, 10, 5, tzinfo=utc), future.observation_id))
    conn.commit()
    query = {'sourceId': str(sid), 'configVersionId': str(config), 'from': '2026-10-02T00:00:00Z',
             'until': '2026-10-04T00:00:00Z', 'timezone': 'Asia/Seoul', 'scope': 'live'}
    path = '/api/pipeline/reports/operational?'
    assert api.request(path + urlencode(query))[0] == 401
    assert api.request(path + urlencode(query), token=api.user)[0] == 403
    code, report = api.request(path + urlencode(query), token=api.admin)
    assert code == 200, (code, report)
    rows = {item['day']: item for item in report['items']}
    assert rows['2026-10-02']['processCount']['value'] == 1, report
    row = rows['2026-10-03']
    assert row['processCount']['value'] == 5, row
    assert row['alarmProcessCount']['value'] == 1
    assert row['alarmRatio']['value'] == 1/3 and row['alarmRatio']['denominator'] == 3
    assert row['unavailableRate']['value'] == 2/5
    assert row['failedProcessCount']['value'] == 1
    assert row['pendingArrivalCount']['value'] == 1
    assert row['comparisons']['hrMl']['comparable'] == 1
    assert row['comparisons']['srMl']['both']['value'] is None
    assert report['decisionCount'] == 6
    assert report['esbOccurrenceToDecisionLatency']['value'] is None
    assert report['externalNotificationCount']['value'] is None
    assert report['executionCost']['metrics']['totalMs']['p50']['value'] is None
    assert report['executionCost']['metrics']['inferenceMs']['p50']['value'] is None
    verify_sse_and_time_boundaries(conn, api, query)
    empty_code, empty = api.request(path + urlencode({**query, 'scope': 'replay'}), token=api.admin)
    assert empty_code == 200 and all(item['processCount']['value'] is None for item in empty['items'])
    code, page = api.request(path + urlencode({**query, 'limit': '1', 'offset': '1'}), token=api.admin)
    assert code == 200 and page['items'][0]['day'] == '2026-10-03' and page['decisionCount'] == 6
    code, outside = api.request(path + urlencode({**query, 'offset': '100'}), token=api.admin)
    assert code == 200 and not outside['items'] and outside['pagination']['total'] == 3
    for extra in ({'from': '2026-02-30T00:00:00Z'}, {'timezone': 'Mars/Unknown'},
                  {'limit': '101'}, {'offset': '10001'}, {'unexpected': 'ignored'}):
        assert api.request(path + urlencode({**query, **extra}), token=api.admin)[0] == 400
    assert api.request(path + urlencode({**query, 'configVersionId': str(uuid.uuid4())}), token=api.admin)[0] == 404
    # UTC times straddle the DST repeated hour, but both belong to the same local day.
    for hour in (5, 6):
        fixed_decision(conn, sid, config, at=datetime(2026, 11, 1, hour, 30, tzinfo=utc))
    code, dst = api.request(path + urlencode({**query, 'from': '2026-11-01T00:00:00Z',
        'until': '2026-11-03T00:00:00Z', 'timezone': 'America/New_York'}), token=api.admin)
    assert code == 200 and next(item for item in dst['items'] if item['day'] == '2026-11-01')['processCount']['value'] == 2
    print('PASS real HTTP/PG operational hand table, KST midnight/DST, no older-revision fallback, pending isolation, null/zero, malformed telemetry, auth/filters/pagination', flush=True)


def verify_queue_at_cutoff(conn, api, spec, result):
    """Past-period queue figures come from job/attempt intervals, not the query-time queue."""
    from detector.jobs import enqueue_observation
    sid, config = uuid.UUID(spec['sourceId']), uuid.UUID(result['configVersionId'])
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)
    hour = lambda value: start + timedelta(hours=value)
    # name: (created h, attempt (start h, end h | None, status, lease h) | None, job status, finished h | None)
    cases = {
        'waiting': (1, None, 'queued', None),
        'running': (2, (3, None, 'running', 48), 'running', None),
        'retry_gap': (3, (4, 5, 'failed', 5), 'queued', None),
        # Reaped after the cutoff: finished_at is the reap time, the lease lapsed earlier.
        'expired_lease': (4, (5, 30, 'expired', 6), 'queued', None),
        # Worker crashed and nothing reaped it yet: still 'running' with a lapsed lease.
        'crashed_unreaped': (4, (5, None, 'running', 6), 'running', None),
        'finished_before': (5, (6, 7, 'failed', 7), 'failed', 8),
        'finished_after': (6, (7, 30, 'failed', 30), 'failed', 31),
        'created_after': (26, None, 'queued', None),
    }
    for name, (created, attempt, status, finished) in cases.items():
        observation = observe(conn, sid, f'queue-{name}-{uuid.uuid4()}', {'adapter_type': 'A', 'status': 'S'},
                              persisted=hour(created) - timedelta(minutes=1))
        job = uuid.UUID(enqueue_observation(conn, observation.observation_id, config_id=config)['jobId'])
        conn.execute('UPDATE run_jobs SET created_at=%s,status=%s,finished_at=%s WHERE id=%s',
                     (hour(created), status, hour(finished) if finished is not None else None, job))
        if attempt is not None:
            began, ended, attempt_status, lease = attempt
            conn.execute('''INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,
                started_at,finished_at) VALUES(%s,%s,1,%s,'queue-qa',%s,%s,%s,%s)''',
                (uuid.uuid4(), job, attempt_status, uuid.uuid4(), hour(lease), hour(began),
                 hour(ended) if ended is not None else None))
    # One decided PROCESS whose SR checker carries the Detector's real pending-experiment evidence.
    fixed_decision(conn, sid, config, at=hour(10), sr_reason='sr_experiment_pending')
    conn.commit()
    query = {'sourceId': str(sid), 'configVersionId': str(config), 'from': start.isoformat().replace('+00:00', 'Z'),
             'until': hour(24).isoformat().replace('+00:00', 'Z'), 'timezone': 'Asia/Seoul', 'scope': 'live'}
    code, report = api.request('/api/pipeline/reports/operational?' + urlencode(query), token=api.admin)
    assert code == 200, (code, report)
    at_cutoff = report['queue']['atCutoff']
    assert at_cutoff['asOf'].startswith('2026-09-11T00:00:00'), at_cutoff
    # waiting: never started + retry gap + reaped expiry + unreaped crash;
    # running: live attempt + attempt ending after cutoff.
    assert at_cutoff['waiting']['value'] == 4 and at_cutoff['waiting']['status'] == 'measured', at_cutoff
    assert at_cutoff['running']['value'] == 2, at_cutoff
    assert at_cutoff['oldestWaitSeconds']['value'] == 23 * 3600, at_cutoff
    # The current snapshot keys keep their documented meaning (query-time status, cutoff-scoped inputs).
    assert report['queue']['scope'] == 'current_snapshot_for_scoped_observations_before_cutoff'
    assert report['queue']['queued']['value'] == 3 and report['queue']['running']['value'] == 2, report['queue']
    rows = {item['day']: item for item in report['items']}
    # Seven undecided arrivals persisted on 09-10 KST; the 09-11 KST part of the period has none.
    assert rows['2026-09-10']['pendingArrivalCount']['value'] == 7, rows['2026-09-10']
    sr = rows['2026-09-10']['comparisons']['srMl']
    assert sr['comparable'] == 0 and sr['both']['reason'] == 'sr_experiment_pending', sr
    assert rows['2026-09-11']['pendingArrivalCount'] == {'status': 'measured', 'value': 0, 'unit': 'process',
        'numerator': 0, 'denominator': None, 'coverage': 1, 'reason': None}, rows['2026-09-11']
    print('PASS real PG queue reconstruction at cutoff (waiting/retry gap/expired lease/running across cutoff), '
          'current snapshot unchanged, measured pending zero', flush=True)


def verify_cost_and_history(conn, api, spec, train):
    result = train['resultSummary']
    now = datetime.now(timezone.utc)
    query = {'sourceId': spec['sourceId'], 'configVersionId': result['configVersionId'],
             'from': (now - timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
             'until': (now + timedelta(hours=1)).isoformat().replace('+00:00', 'Z'),
             'timezone': 'UTC', 'scope': 'offline'}
    path = '/api/pipeline/reports/execution-cost?'
    assert api.request(path + urlencode(query))[0] == 401
    assert api.request(path + urlencode(query), token=api.user)[0] == 403
    status, report = api.request(path + urlencode(query), token=api.admin)
    assert status == 200, (status, report)
    kinds = {row['kind']: row for row in report['items']}
    assert kinds['train']['targets'] == 1 and kinds['evaluate']['targets'] == 2
    assert kinds['train']['metrics']['trainMs']['sum']['value'] > 0
    assert kinds['evaluate']['metrics']['evaluationMs']['sum']['value'] > 0
    assert kinds['evaluate']['metrics']['queueWaitMs']['p95']['value'] >= 0
    assert kinds['train']['conditions'][0]['memoryScope'] == 'process_current_rss_sample'
    # Controlled historical rows establish both UUID casing directions and bad JSON tolerance.
    for source_text, value in ((spec['sourceId'].upper(), 3), (spec['sourceId'], 7), ('bad-source-uuid', 10000), (None, 10000)):
        cost = {'status': 'measured', 'metricVersion': 'execution-cost-v1',
                'metrics': {'totalMs': {'value': value, 'unit': 'ms'}},
                'details': {'scope': 'controlled_historical_sql_fixture'}}
        conn.execute('''INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,
            input_spec,input_config_version_id,max_attempts,attempt_count,next_run_at,created_at,finished_at,result_summary)
            VALUES(%s,'evaluate','failed','independent-qa','admin',%s,%s::jsonb,%s,3,1,
            clock_timestamp(),clock_timestamp(),clock_timestamp(),%s::jsonb)''',
            (uuid.uuid4(), str(uuid.uuid4()), json.dumps({'sourceId': source_text}), result['configVersionId'],
             json.dumps({'evaluationEnvelope': {'execution_cost': cost}})))
    conn.commit()
    reports = [api.request(path + urlencode({**query, 'sourceId': text}), token=api.admin)
               for text in (spec['sourceId'], spec['sourceId'].upper())]
    assert all(status == 200 for status, _ in reports)
    for _, response in reports:
        evaluated = next(row for row in response['items'] if row['kind'] == 'evaluate')
        assert evaluated['targets'] == 4 and evaluated['failed'] == 2, evaluated
        assert abs(evaluated['metrics']['totalMs']['sum']['value'] -
                   (kinds['evaluate']['metrics']['totalMs']['sum']['value'] + 10)) < 1e-6
    # Same-source keyset must retain microseconds and avoid missing/repeated candidates.
    for micro in ('123456', '123456', '123455'):
        conn.execute('''INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,
            content_hash,created_by_actor_id,created_at) SELECT %s,source_id,configuration_spec,explanation_spec,
            %s,'independent-qa',%s::timestamptz FROM ml_config_versions WHERE id=%s''',
            (uuid.uuid4(), str(uuid.uuid4()), '2026-10-20T00:00:00.' + micro + 'Z', result['configVersionId']))
    conn.commit()
    expected = [str(row[0]) for row in conn.execute('''SELECT id FROM ml_config_versions WHERE source_id=%s
        ORDER BY created_at DESC,id DESC''', (spec['sourceId'],))]
    conn.commit()
    seen, cursor = [], None
    while True:
        args = {'sourceId': spec['sourceId'], 'limit': '1'}
        if cursor:
            args['cursor'] = cursor
        status, page = api.request('/api/pipeline/reports/candidates?' + urlencode(args), token=api.admin)
        assert status == 200 and page['manualApprovalRequired'] is True, (status, page)
        seen += [item['configVersionId'] for item in page['items']]
        if not page['pagination']['hasMore']:
            break
        cursor = page['pagination']['nextCursor']
        assert cursor
    assert seen == expected and len(seen) == len(set(seen)) == 4
    status, all_candidates = api.request('/api/pipeline/reports/candidates?' + urlencode({'sourceId': spec['sourceId']}), token=api.admin)
    candidate = next(row for row in all_candidates['items'] if row['configVersionId'] == result['configVersionId'])
    assert candidate['trainingSummary']['trainingSelection']['normalGroundTruth'] is False
    assert candidate['latestEvaluation']['metrics']['ruleMlComparison']['metricVersion'] == 'rule-ml-comparison-v1'
    assert candidate['trainingSummary']['evaluationEnvelope']['synthetic']['status'] == 'unavailable'
    print('PASS real train/eval cost API, source UUID case both directions/bad legacy source JSON, candidate provenance and microsecond keyset', flush=True)


def verify_manual_application(conn, api, result, evaluations):
    from detector.applications import process_application_once
    status, before = api.request('/api/configurations/active', token=api.admin)
    assert status == 200
    body = {'operation': 'apply', 'evaluationId': evaluations['validation']['resultSummary']['evaluationId'],
            'expectedRevision': before['revision'], 'expectedActiveVersionId': before['configVersionId']}
    path = '/api/configurations/' + result['configVersionId'] + '/applications'
    assert api.request(path, body, api.user, str(uuid.uuid4()))[0] == 403
    assert api.request(path, {**body, 'evaluationId': evaluations['test']['resultSummary']['evaluationId']},
                       api.admin, str(uuid.uuid4()))[0] == 409
    # Missing labels/quality metrics do not automatically reject this technically complete candidate.
    assert evaluations['validation']['resultSummary']['metrics']['precision'] is None
    status, application = api.request(path, body, api.admin, str(uuid.uuid4()))
    assert status == 201, (status, application)
    row = conn.execute('''SELECT a.relative_path FROM ml_config_models m JOIN ml_artifacts a
        ON a.id=m.model_artifact_id WHERE m.config_version_id=%s''', (result['configVersionId'],)).fetchone()
    conn.commit()
    model_path = api.artifacts / row[0]
    original = model_path.read_bytes()
    try:
        model_path.write_bytes(original + b'controlled-tamper')
        assert process_application_once(conn, api.artifacts, 'independent-qa')
        conn.commit()
        state = conn.execute('SELECT status FROM run_applications WHERE id=%s', (application['id'],)).fetchone()[0]
        conn.commit()
        assert state == 'failed'
        failed_active = api.request('/api/configurations/active', token=api.admin)[1]
        assert (failed_active['configVersionId'], failed_active['revision']) == (
            before['configVersionId'], before['revision']), (before, failed_active)
    finally:
        model_path.write_bytes(original)
    status, _ = api.request(path, body, api.admin, str(uuid.uuid4()))
    assert status == 201
    assert process_application_once(conn, api.artifacts, 'independent-qa')
    conn.commit()
    active = api.request('/api/configurations/active', token=api.admin)[1]
    assert active['configVersionId'] == result['configVersionId'] and active['revision'] == before['revision'] + 1
    print('PASS unlabeled manual application, user/test-evaluation rejection, actual artifact-tamper failure preserves active, verified explicit CAS apply', flush=True)


class IndependentUtilityTests(unittest.TestCase):
    def test_sr_fixture_metadata_and_whole_envelope_schema(self):
        from trainer.metrics import comparison_family
        from trainer.service_contract import evaluation_envelope
        from telemetry import cost_family
        rows = [{'hr': {'status': 'succeeded', 'anomalous': False},
                 'sr': {'status': 'succeeded', 'anomalous': sr},
                 'ml': {'status': 'succeeded', 'anomalous': ml}}
                for sr, ml in ((True, True), (True, False), (False, True), (False, False))]
        family = comparison_family(rows, {'scope': 'controlled_fixture'})
        self.assertEqual(family['details']['srStatus'], 'measured')
        self.assertIsNone(family['details']['srReason'])
        self.assertEqual([family['metrics'][k]['numerator'] for k in
                          ('srBoth', 'srRuleOnly', 'srMlOnly', 'srNeither')], [1, 1, 1, 1])
        self.assertTrue(all(family['metrics'][k]['value'] == .25 for k in
                            ('srBoth', 'srRuleOnly', 'srMlOnly', 'srNeither')))
        for row in rows:
            row['sr'] = {'status': 'not_ready', 'anomalous': None}
        unavailable = comparison_family(rows, {})
        self.assertEqual(unavailable['details']['srReason'], 'sr_experiment_pending')
        self.assertIsNone(unavailable['metrics']['srBoth']['value'])
        envelope = evaluation_envelope()
        envelope['rule_ml'] = family
        envelope['execution_cost'] = cost_family({'totalMs': 4}, batch_size=4, samples=[1, 1, 1, 1])
        schema = json.loads((REPO / 'server/contracts/trainer-evaluation-envelope.schema.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(envelope)
        self.assertIsNone(envelope['execution_cost']['metrics']['esbLatencyMs']['value'])

    def test_source_hr_unknown_and_selection_do_not_invent_ground_truth(self):
        from detector.status_rule import default_policy, evaluate_status
        from trainer.selection import select_training
        policy = default_policy(uuid.uuid4())
        expected = {'S': (False, []), 'F': (True, ['critical']),
                    'P': (True, ['warning']), 'N': (True, ['warning'])}
        for status, (hit, severity) in expected.items():
            result = evaluate_status(policy, {'status': status})
            self.assertEqual((result['anomalous'], result['severity']), (hit, severity))
            self.assertEqual(result['evidence'][0]['sourceId'], policy['sourceId'])
        for raw in ({}, {'status': '?'}, {'status': None}, {'status': 's'}):
            result = evaluate_status(policy, raw)
            self.assertEqual(result['status'], 'not_ready')
            self.assertIsNone(result['anomalous'])
        rows = [(uuid.uuid4(), {'status': status}, 'train', label) for status, label in
                [('S', None), ('S', {'label': 'anomaly'}), ('F', None), ('P', None), ('N', None), ('?', None)]]
        selected, meta = select_training(rows)
        self.assertEqual(selected, rows[:1])
        self.assertEqual((meta['populationCount'], meta['selectedCount'], meta['excludedCount']), (6, 1, 5))
        self.assertFalse(meta['normalGroundTruth'])

    def test_queue_wait_uses_persisted_attempt_start_not_later_query_clock(self):
        from telemetry import queue_wait_ms
        dsn = os.environ.get('PIPELINE_TEST_DATABASE_URL')
        if not dsn:
            self.skipTest('fresh isolated database is required')
        parsed = urlsplit(dsn)
        self.assertTrue(parsed.hostname in ('127.0.0.1', 'localhost') and parsed.path == '/qa_metrics_service')
        with psycopg.connect(dsn) as conn:
            job, attempt = uuid.uuid4(), uuid.uuid4()
            conn.execute('''INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,
                request_hash,input_spec,max_attempts,attempt_count,created_at,next_run_at)
                VALUES(%s,'train','running','independent-qa','admin','queue-probe','{}',3,1,clock_timestamp(),clock_timestamp())''', (job,))
            conn.execute('''INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,
                lease_until,started_at) VALUES(%s,%s,1,'running','independent-qa',%s,
                clock_timestamp()+interval '60 seconds','2026-10-17T00:00:02Z')''', (attempt, job, uuid.uuid4()))
            message = SimpleNamespace(created_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
            self.assertEqual(queue_wait_ms(conn, message, attempt), 2000)
            conn.execute('SELECT pg_sleep(.05)')
            self.assertEqual(queue_wait_ms(conn, message, attempt), 2000)
            conn.rollback()


def main():
    dsn = os.environ['PIPELINE_TEST_DATABASE_URL']
    parsed = urlsplit(dsn)
    assert parsed.hostname in ('127.0.0.1', 'localhost') and parsed.path == '/qa_metrics_service'
    with tempfile.TemporaryDirectory(prefix='powercode-issue58-http-') as directory:
        api = Api(directory, dsn)
        try:
            with psycopg.connect(dsn) as conn:
                spec = seed(conn)
                before = conn.execute('SELECT * FROM ops_system_state').fetchall()
                conn.commit()
                train = api.submit(spec)
                verify_training(conn, api, spec, train['resultSummary'])
                verify_detector(conn, api, spec, train['resultSummary'])
                evaluations = verify_evaluation(conn, api, spec, train)
                verify_operational_reports(conn, api, spec, train['resultSummary'])
                verify_queue_at_cutoff(conn, api, spec, train['resultSummary'])
                assert conn.execute('SELECT * FROM ops_system_state').fetchall() == before
                conn.commit()
                verify_cost_and_history(conn, api, spec, train)
                verify_manual_application(conn, api, train['resultSummary'], evaluations)
        finally:
            api.close()


if __name__ == '__main__':
    if '--utilities' in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        main()
