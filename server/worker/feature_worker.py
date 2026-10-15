"""원천 PROCESS 관측을 승인 변수 구성으로 계산한 뒤 탐지 큐에 전달한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

from feature_runtime import calculate_features, input_manifest_hash
from pg_queue import ClaimedMessage, acknowledge, claim, enqueue, fail


def active_config(conn: Any) -> uuid.UUID | None:
    with conn.cursor() as cur:
        cur.execute("SELECT active_config_version_id FROM ops_system_state WHERE id = 1")
        row = cur.fetchone()
    return row[0] if row else None


def active_source(conn: Any, config_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute("SELECT source_id FROM ml_config_versions WHERE id = %s", (config_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("active_config_missing")
    return row[0]


def materialize(conn: Any, observation_id: uuid.UUID, config_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """같은 입력과 변수 버전은 재계산해도 한 행으로 수렴한다."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.source_id
                 FROM data_observations AS o JOIN data_processes AS p ON p.observation_id = o.id
                WHERE o.id = %s AND o.layer = 'P' AND o.execution_id IS NOT NULL""",
            (observation_id,),
        )
        observation = cur.fetchone()
        if observation is None:
            raise ValueError("invalid_process_observation")
        cur.execute("SELECT source_id FROM ml_config_versions WHERE id = %s", (config_id,))
        config = cur.fetchone()
        if config is None or config[0] != observation[0]:
            raise ValueError("config_source_mismatch")
        cur.execute(
            """SELECT DISTINCT fs.id, fs.content_hash
                 FROM ml_config_models AS cm
                 JOIN ml_feature_sets AS fs ON fs.id = cm.feature_set_id
                WHERE cm.config_version_id = %s ORDER BY fs.id""",
            (config_id,),
        )
        feature_sets = cur.fetchall()
        if not feature_sets:
            cur.execute("""SELECT fs.id,fs.content_hash FROM ml_feature_sets fs
                JOIN ml_config_versions c ON c.configuration_spec->>'featureSetId'=fs.id::text
                WHERE c.id=%s""", (config_id,))
            feature_sets = cur.fetchall()
        if not feature_sets:
            raise ValueError("config_has_no_feature_set")
    return [(feature_set_id, materialize_set(conn, observation_id, feature_set_id))
            for feature_set_id, _ in feature_sets]


def materialize_set(conn: Any, observation_id: uuid.UUID, feature_set_id: uuid.UUID) -> str:
    """추천으로 확정된 변수 묶음을 과거·실시간 입력에 똑같이 적용한다."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.allowed_values, o.content_hash, fs.content_hash
                 FROM data_observations AS o
                 JOIN data_processes AS p ON p.observation_id = o.id
                 CROSS JOIN ml_feature_sets AS fs
                WHERE o.id = %s AND o.layer = 'P' AND fs.id = %s""",
            (observation_id, feature_set_id),
        )
        observation = cur.fetchone()
        if observation is None:
            raise ValueError("invalid_feature_input")
        cur.execute(
                """SELECT m.output_name, m.output_position, v.expression, v.requires_fit
                     FROM ml_feature_set_members AS m
                     JOIN ml_feature_versions AS v ON v.id = m.feature_version_id
                    WHERE m.feature_set_id = %s AND m.model_input
                    ORDER BY m.output_position""",
            (feature_set_id,),
        )
        members = cur.fetchall()
        if not members or [item[1] for item in members] != list(range(len(members))):
            raise ValueError("invalid_feature_positions")
        if any(item[3] for item in members):
            raise ValueError("fitted_feature_transform_not_supported")
        definitions = [{"name": item[0], "expression": item[2]} for item in members]
        values, missing = calculate_features(definitions, observation[0])
        manifest = input_manifest_hash(str(observation_id), observation[1], observation[2])
        cur.execute(
                """INSERT INTO data_feature_materializations
                     (observation_id, feature_set_id, input_manifest_hash, values_json, missing_names)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                   ON CONFLICT DO NOTHING""",
            (observation_id, feature_set_id, manifest,
             json.dumps(values), json.dumps(missing)),
        )
    return manifest


def process_feature_message(conn: Any, message: ClaimedMessage, config_id: uuid.UUID) -> bool:
    """변수 저장·탐지 요청·입력 큐 완료를 하나의 트랜잭션으로 묶는다."""
    payload = message.payload
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("observationId"), str) or \
            not isinstance(payload.get("sourceId"), str):
        raise ValueError("invalid_feature_message")
    observation_id = uuid.UUID(payload["observationId"])
    source_id = uuid.UUID(payload["sourceId"])
    started = time.perf_counter()
    with conn.transaction():
        rows = materialize(conn, observation_id, config_id)
        combined = hashlib.sha256(json.dumps(sorted((str(item[0]), item[1]) for item in rows)).encode()).hexdigest()
        detection_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{observation_id}:{config_id}:{combined}")
        enqueue(conn, "detection", {
            "schemaVersion": 1,
            "eventId": str(detection_id),
            "observationId": str(observation_id),
            "sourceId": str(source_id),
            "configVersionId": str(config_id),
            "inputManifestHash": combined,
            "enqueuedAt": payload.get("enqueuedAt"),
        }, message_id=detection_id)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops_pipeline_measurements
                     (id, observation_id, stage, config_version_id, duration_ms)
                   VALUES (%s, %s, 'feature', %s, %s)""",
                (uuid.uuid4(), observation_id, config_id, (time.perf_counter() - started) * 1000),
            )
        if not acknowledge(conn, "feature", message):
            raise RuntimeError("feature_lease_lost")
    return True


def run(*, once: bool = False, poll_seconds: float = 1.0) -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    with psycopg.connect(database_url) as conn:
        while True:
            config_id = active_config(conn)
            conn.commit()
            if config_id is None:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            source_id = active_source(conn, config_id)
            conn.commit()
            message = claim(conn, "feature", source_id=str(source_id))
            conn.commit()
            if message is None:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            try:
                process_feature_message(conn, message, config_id)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                reason = exc.args[0] if isinstance(exc, ValueError) and exc.args else "feature_processing_failed"
                fail(conn, "feature", message, reason=str(reason)[:100])
                conn.commit()
            if once:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
