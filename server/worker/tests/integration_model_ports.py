"""시험 또는 실제 IF로 학습 산출물→평가→승인→탐지 연결을 검증한다."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_adapters
from collector.storage import ingest_observation
from detector.applications import process_application_once
from detector.runner import process_detection_message
from feature_worker import process_feature_message
from pg_queue import claim, enqueue
from trainer.catalog import register_feature_set
from trainer.runner import run


def fake_train(rows, names, directory):
    assert names == ["error_count"] and len(rows) >= 100
    model = Path(directory) / "model.joblib"
    fit = Path(directory) / "fit.joblib"
    model.write_bytes(b"test-only-model-artifact")
    fit.write_bytes(b"test-only-fit-artifact")
    return {"modelPath": str(model), "fitPath": str(fit),
            "formatVersion": "test-v1", "runtimeVersion": "test-only",
            "thresholdSpec": {"direction": "higher_is_anomaly", "value": "0.5"}}


def fake_score(_reference, features):
    return float(features["error_count"])


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    real_iforest = os.environ.get("REAL_IFOREST") == "1"
    reference_count = 1000 if real_iforest else 200
    source_id, train_id, evaluation_job_id, rule_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "tmp").mkdir()
        os.environ["ARTIFACT_ROOT"] = str(root)
        if not real_iforest:
            model_adapters.train_iforest = fake_train
            model_adapters.score_iforest = fake_score
            model_adapters.prepare_iforest = lambda _reference: None
        model_adapters.recommend_features = lambda _rows: [
            {"name": "error_count", "expression": {"op": "field", "field": "P.error_count"}}
        ]
        model_adapters.recommend_rules = lambda _rows, _feature_set_id, _definitions=None: [
            {"ruleKey": f"process_error_{source_id.hex[:8]}", "displayName": "Process error",
             "condition": {"field": "F.error_count", "operator": "gt", "value": "0"},
             "outcome": {"decisionCode": "TEST_RULE_HIT", "severity": "warning"}}
        ]
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ops_sources
                         (id, dataset_key, display_name, ingestion_method, schema_version,
                          clock_basis, field_catalog, created_at)
                       VALUES (%s, %s, 'Model port test', 'synthetic-test', 'v1',
                               '{}'::jsonb, '{}'::jsonb, clock_timestamp())""",
                    (source_id, f"model-port-{source_id}"),
                )
            conn.commit()
            feature_set_id = register_feature_set(conn, [
                {"name": "error_count", "expression": {"op": "field", "field": "P.error_count"}}
            ], "test-admin")
            conn.commit()
            for index in range(reference_count):
                execution_id = uuid.uuid4()
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO data_executions
                             (id, source_id, layer, execution_key, key_parts,
                              key_contract_version, first_observed_at)
                           VALUES (%s, %s, 'P', %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                        (execution_id, source_id, f"train-{index}"),
                    )
                observation = ingest_observation(
                    conn, source_id=source_id, layer="P",
                    dedup_key=f"model-port:{source_id}:{index}",
                    raw={"process_id": f"p{index}",
                         "error_count": str(index % 100 if real_iforest else index % 2)},
                    execution_id=execution_id, revision_no=1,
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO data_label_evidence
                             (id, observation_id, revision_no, label, reason_code, created_by_actor_id)
                           VALUES (%s, %s, 1, %s, 'TEST_SCENARIO', 'test-admin')""",
                        (uuid.uuid4(), observation.observation_id,
                         ("normal" if index < 700 else ("anomaly" if index % 2 else "normal"))
                         if real_iforest else ("anomaly" if index % 2 else "normal")),
                    )
                conn.commit()
            recommendation_id = uuid.uuid4()
            recommendation_spec = {"kind": "recommend_feature", "sourceId": str(source_id),
                                   "from": (now - timedelta(minutes=10)).isoformat(),
                                   "until": (now + timedelta(minutes=10)).isoformat()}
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO run_jobs
                         (id, kind, status, requested_by_actor_id, requester_role,
                          request_hash, input_spec, max_attempts, attempt_count,
                          next_run_at, created_at,contract_version,queue,producer_kind,producer_key,idempotency_key,llm_spec)
                       VALUES (%s, 'recommend_feature', 'queued', 'test-admin', 'admin',
                               %s, %s::jsonb, 3, 0, clock_timestamp(), clock_timestamp(),
                               'v03','llm_recommendation','user','test-admin',%s,%s::jsonb)""",
                    (recommendation_id, hashlib.sha256(json.dumps(recommendation_spec).encode()).hexdigest(),
                     json.dumps(recommendation_spec), str(recommendation_id), json.dumps({
                         "evidence_version": "test-period-v1",
                         "input_evidence_hash": hashlib.sha256(json.dumps(recommendation_spec).encode()).hexdigest(),
                         "model_version": "fixture-test", "prompt_version": "test",
                         "output_schema_version": "test", "reuse_key": str(recommendation_id)})),
                )
                enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(recommendation_id)})
            conn.commit()
        run(once=True,role="llm")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, result_summary FROM run_jobs WHERE id = %s", (recommendation_id,))
                recommendation_status, recommendation_result = cur.fetchone()
                assert recommendation_status == "succeeded", (recommendation_status, recommendation_result)
                assert recommendation_result["featureSetId"] == str(feature_set_id)
                assert recommendation_result["materializedCount"] == reference_count
            rule_job_id = uuid.uuid4()
            rule_spec = {"kind": "recommend_rule", "sourceId": str(source_id),
                         "from": (now - timedelta(minutes=10)).isoformat(),
                         "until": (now + timedelta(minutes=10)).isoformat(),
                         "featureSetId": str(feature_set_id)}
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO run_jobs
                         (id, kind, status, requested_by_actor_id, requester_role,
                          request_hash, input_spec, max_attempts, attempt_count,
                          next_run_at, created_at)
                       VALUES (%s, 'recommend_rule', 'queued', 'test-admin', 'admin',
                               %s, %s::jsonb, 3, 0, clock_timestamp(), clock_timestamp())""",
                    (rule_job_id, hashlib.sha256(json.dumps(rule_spec).encode()).hexdigest(),
                     json.dumps(rule_spec)),
                )
                enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(rule_job_id)})
            conn.commit()
        run(once=True,role="llm")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, result_summary FROM run_jobs WHERE id = %s", (rule_job_id,))
                rule_status, rule_result = cur.fetchone()
                assert rule_status == "succeeded" and len(rule_result["suggestions"]) == 1
                selected = rule_result["suggestions"][0]
                selected_spec = {"schemaVersion": 1, "state": "draft",
                                 "condition": selected["condition"], "outcome": selected["outcome"]}
                cur.execute(
                    """INSERT INTO ml_rule_versions
                         (id, rule_key, version, rule_spec, input_spec, evidence_spec,
                          content_hash, created_by_actor_id, created_at)
                       VALUES (%s, %s, 1, %s::jsonb, %s::jsonb, '{}'::jsonb,
                               %s, 'test-admin', clock_timestamp())""",
                    (rule_id, selected["ruleKey"], json.dumps(selected_spec),
                     json.dumps({"featureSetId": str(feature_set_id), "fields": ["F.error_count"]}),
                     hashlib.sha256(json.dumps(selected_spec).encode()).hexdigest()),
                )
            conn.commit()
        with psycopg.connect(dsn) as conn:
            train_spec = {"kind": "train", "sourceId": str(source_id),
                          "from": (now - timedelta(minutes=10)).isoformat(),
                          "until": (now + timedelta(minutes=10)).isoformat(),
                          "featureSetId": str(feature_set_id), "ruleVersionIds": [str(rule_id)]}
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO run_jobs
                         (id, kind, status, requested_by_actor_id, requester_role,
                          request_hash, input_spec, max_attempts, attempt_count,
                          next_run_at, created_at)
                       VALUES (%s, 'train', 'queued', 'test-admin', 'admin',
                               %s, %s::jsonb, 3, 0, clock_timestamp(), clock_timestamp())""",
                    (train_id, hashlib.sha256(json.dumps(train_spec).encode()).hexdigest(),
                     json.dumps(train_spec)),
                )
                enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(train_id)})
            conn.commit()
        run(once=True)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, result_summary FROM run_jobs WHERE id = %s", (train_id,))
                status, result = cur.fetchone()
                assert status == "succeeded", (status, result)
                config_id = uuid.UUID(result["configVersionId"])
                snapshot_id = uuid.UUID(result["snapshotId"])
                cur.execute("SELECT count(*) FROM ml_artifacts WHERE created_job_id = %s", (train_id,))
                assert cur.fetchone()[0] == 2
            evaluation_spec = {"kind": "evaluate", "sourceId": str(source_id),
                               "configVersionId": str(config_id), "snapshotId": str(snapshot_id),
                               "purpose": "validation"}
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO run_jobs
                         (id, kind, status, requested_by_actor_id, requester_role,
                          request_hash, input_spec, input_config_version_id, input_snapshot_id,
                          max_attempts, attempt_count, next_run_at, created_at)
                       VALUES (%s, 'evaluate', 'queued', 'test-admin', 'admin',
                               %s, %s::jsonb, %s, %s, 3, 0, clock_timestamp(), clock_timestamp())""",
                    (evaluation_job_id, hashlib.sha256(json.dumps(evaluation_spec).encode()).hexdigest(),
                     json.dumps(evaluation_spec), config_id, snapshot_id),
                )
                enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(evaluation_job_id)})
            conn.commit()
        run(once=True)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, result_summary FROM run_jobs WHERE id = %s", (evaluation_job_id,))
                evaluation_status, result = cur.fetchone()
                assert evaluation_status == "succeeded", (evaluation_status, result)
                assert result["applicability"] == "valid", result
                assert result["metrics"]["combination"] == "rule_or_iforest"
                assert result["metrics"]["ruleCount"] == 1
                assert result["metrics"]["rule"] is not None
                evaluation_id = uuid.UUID(result["evaluationId"])
                cur.execute(
                    """INSERT INTO ops_system_state
                         (id, revision, detector_state, changed_at)
                       VALUES (1, 0, '{}'::jsonb, clock_timestamp()) ON CONFLICT DO NOTHING""",
                )
                cur.execute("SELECT active_config_version_id, revision FROM ops_system_state WHERE id = 1")
                previous, revision = cur.fetchone()
                application_id = uuid.uuid4()
                cur.execute(
                    """INSERT INTO run_applications
                         (id, operation, target_config_version_id, previous_config_version_id,
                          expected_active_version_id, expected_system_revision, evaluation_id,
                          requested_by_actor_id, requester_role, approved_at, request_hash,
                          status, gate_evidence, transition_audit, created_at)
                       VALUES (%s, 'apply', %s, %s, %s, %s, %s, 'test-admin', 'admin',
                               clock_timestamp(), 'test-application', 'queued', '{}'::jsonb,
                               '{}'::jsonb, clock_timestamp())""",
                    (application_id, config_id, previous, previous, revision, evaluation_id),
                )
            conn.commit()
            assert process_application_once(conn, root, "test-detector")
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT active_config_version_id FROM ops_system_state WHERE id = 1")
                assert cur.fetchone()[0] == config_id
            execution_id = uuid.uuid4()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO data_executions
                         (id, source_id, layer, execution_key, key_parts,
                          key_contract_version, first_observed_at)
                       VALUES (%s, %s, 'P', %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                    (execution_id, source_id, f"live-{execution_id}"),
                )
            live = ingest_observation(
                conn, source_id=source_id, layer="P", dedup_key=f"live:{source_id}",
                raw={"process_id": "live", "error_count": "1"},
                execution_id=execution_id, revision_no=1, detection_eligible=True,
            )
            conn.commit()
            detection_message = claim(conn, "detection", source_id=str(source_id))
            assert detection_message is not None
            conn.commit()
            process_detection_message(conn, detection_message, root)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT status, ml_status, decision_code FROM run_process_decisions
                        WHERE process_observation_id = %s""",
                    (live.observation_id,),
                )
                assert cur.fetchone() == ("complete", "succeeded", "TEST_RULE_HIT")
        print(f"model ports integration passed ({'real IF' if real_iforest else 'test adapter'}): "
              "feature recommendation, rule recommendation, train, evaluate, apply, decision")


if __name__ == "__main__":
    main()
