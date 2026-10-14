"""격리 DB에서 저장→변수 큐→탐지 큐→부분 판정까지 확인한다."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.storage import ingest_observation
from detector.runner import process_detection_message
from feature_worker import process_feature_message
from pg_queue import claim, enqueue
from trainer.catalog import register_feature_set


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    source_id, execution_id, config_id, rule_id = (uuid.uuid4() for _ in range(4))
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_sources
                   (id, dataset_key, display_name, ingestion_method, schema_version,
                    clock_basis, field_catalog, created_at)
                   VALUES (%s, %s, 'Feature pipeline test', 'synthetic-test', 'v1',
                           '{}'::jsonb, '{}'::jsonb, clock_timestamp())""",
                (source_id, f"feature-pipeline-{source_id}"),
            )
            cur.execute(
                """INSERT INTO data_executions
                   (id, source_id, layer, execution_key, key_parts,
                    key_contract_version, first_observed_at)
                   VALUES (%s, %s, 'P', %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                (execution_id, source_id, f"process-{execution_id}"),
            )
        conn.commit()
        feature_set_id = register_feature_set(conn, [
            {"name": "error_count", "expression": {"op": "field", "field": "P.error_count"}}
        ], "test-actor")
        conn.commit()
        rule = {"schemaVersion": 1, "state": "draft", "condition": {
            "field": "P.error_count", "operator": "gt", "value": "0"
        }, "outcome": {"decisionCode": "PROCESS_ERROR", "severity": "warning"}}
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ml_rule_versions
                   (id, rule_key, version, rule_spec, input_spec, evidence_spec,
                    content_hash, created_by_actor_id, created_at)
                   VALUES (%s, %s, 1, %s::jsonb, '{}'::jsonb,
                           '{}'::jsonb, %s, 'test-actor', clock_timestamp())""",
                (rule_id, f"process_error_{source_id.hex[:8]}", json.dumps(rule),
                 hashlib.sha256(json.dumps(rule).encode()).hexdigest()),
            )
            cur.execute(
                """INSERT INTO ml_config_versions
                   (id, source_id, configuration_spec, explanation_spec, content_hash,
                    created_by_actor_id, created_at)
                   VALUES (%s, %s, '{}'::jsonb, '{}'::jsonb, 'test-config',
                           'test-actor', clock_timestamp())""",
                (config_id, source_id),
            )
            cur.execute(
                """INSERT INTO ml_config_models
                   (config_version_id, model_key, feature_set_id, adapter_spec,
                    training_settings, threshold_spec)
                   VALUES (%s, 'iforest', %s, '{}'::jsonb, '{}'::jsonb,
                           '{}'::jsonb)""",
                (config_id, feature_set_id),
            )
            cur.execute(
                """INSERT INTO ml_config_rules
                   (config_version_id, rule_version_id, evaluation_order)
                   VALUES (%s, %s, 0)""",
                (config_id, rule_id),
            )
            cur.execute(
                """INSERT INTO ops_system_state
                   (id, active_config_version_id, revision, detector_state, changed_at)
                   VALUES (1, %s, 1, '{}'::jsonb, clock_timestamp())
                   ON CONFLICT (id) DO UPDATE SET active_config_version_id = EXCLUDED.active_config_version_id,
                       revision = ops_system_state.revision + 1""",
                (config_id,),
            )
        conn.commit()
        observed = ingest_observation(
            conn, source_id=source_id, layer="P", dedup_key=f"feature-test:{source_id}",
            raw={"process_id": "p1", "error_count": "2", "BODY": "discard-me"},
            execution_id=execution_id, revision_no=1, detection_eligible=True,
        )
        conn.commit()
        feature_message = claim(conn, "feature", source_id=str(source_id))
        assert feature_message is not None
        conn.commit()
        process_feature_message(conn, feature_message, config_id)
        conn.commit()
        detection_message = claim(conn, "detection", source_id=str(source_id))
        assert detection_message is not None
        conn.commit()
        process_detection_message(conn, detection_message, None)
        conn.commit()
        # 이전 배포의 탐지 메시지에는 sourceId가 없으므로 관측 참조로 범위를 찾는다.
        enqueue(conn, "detection", {"schemaVersion": 1, "eventId": str(uuid.uuid4()),
                                    "observationId": str(observed.observation_id)})
        conn.commit()
        legacy = claim(conn, "detection", source_id=str(source_id))
        assert legacy is not None
        conn.commit()
        process_detection_message(conn, legacy, None)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.status, d.rule_status, d.ml_status, d.decision_code,
                          d.evidence, f.values_json
                     FROM run_process_decisions AS d
                     JOIN data_feature_materializations AS f
                       ON f.observation_id = d.process_observation_id
                    WHERE d.process_observation_id = %s""",
                (observed.observation_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0:4] == ("partial", "succeeded", "not_ready", "PROCESS_ERROR")
            assert row[5] == {"error_count": "2"}
            assert "discard-me" not in str(row)
            cur.execute("SELECT count(*) FROM run_process_decisions WHERE process_observation_id = %s",
                        (observed.observation_id,))
            assert cur.fetchone()[0] == 1
        conn.commit()
    print("feature pipeline integration passed: materialized P, rule hit, IF not_ready, partial decision")


if __name__ == "__main__":
    main()
