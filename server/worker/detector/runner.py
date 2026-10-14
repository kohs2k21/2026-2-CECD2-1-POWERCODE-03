"""승인 구성의 Rule·IF 판정을 수행하고 결과를 멱등하게 저장한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

import model_adapters
from detector.input_reader import load_process_observation
from detector.artifacts import verified_artifact_path
from detector.rules import evaluate_rules
from feature_worker import active_config, active_source, materialize
from pg_queue import ClaimedMessage, acknowledge, claim, fail


def _score(conn: Any, config_id: uuid.UUID, observation_id: uuid.UUID,
           root: Path | None) -> tuple[str, float | None, bool | None, list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT cm.model_key, cm.feature_set_id, cm.threshold_spec,
                      m.relative_path, m.content_hash, f.relative_path, f.content_hash,
                      cm.training_settings
                 FROM ml_config_models AS cm
                 LEFT JOIN ml_artifacts AS m ON m.id = cm.model_artifact_id AND m.kind = 'model'
                 LEFT JOIN ml_artifacts AS f ON f.id = cm.fit_artifact_id AND f.kind = 'fit'
                WHERE cm.config_version_id = %s ORDER BY cm.model_key""",
            (config_id,),
        )
        models = cur.fetchall()
    if len(models) != 1 or models[0][0] != "iforest":
        return "not_ready", None, None, [{"reason": "iforest_config_unavailable"}]
    key, feature_set_id, threshold, model_relative, model_hash, fit_relative, fit_hash, settings = models[0]
    model_path = verified_artifact_path(root, model_relative, model_hash)
    fit_path = verified_artifact_path(root, fit_relative, fit_hash)
    if model_path is None or fit_path is None:
        return "not_ready", None, None, [{"modelKey": key, "reason": "artifact_unavailable"}]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT values_json, missing_names FROM data_feature_materializations
                WHERE observation_id = %s AND feature_set_id = %s
                ORDER BY calculated_at DESC LIMIT 1""",
            (observation_id, feature_set_id),
        )
        feature_row = cur.fetchone()
    if feature_row is None or feature_row[1]:
        return "not_ready", None, None, [{"modelKey": key, "reason": "features_unavailable"}]
    names = settings.get("featureNames") if isinstance(settings, dict) else None
    if not isinstance(names, list) or not names or set(names) != set(feature_row[0]):
        return "not_ready", None, None, [{"modelKey": key, "reason": "feature_schema_mismatch"}]
    try:
        score = model_adapters.score_iforest(
            {"modelPath": model_path, "fitPath": fit_path, "featureNames": names}, feature_row[0])
    except model_adapters.ModelNotConfigured:
        return "not_ready", None, None, [{"modelKey": key, "reason": "iforest_adapter_not_configured"}]
    if not isinstance(score, (int, float)) or not math.isfinite(score):
        return "failed", None, None, [{"modelKey": key, "reason": "invalid_score"}]
    if not isinstance(threshold, dict) or threshold.get("direction") not in {
        "higher_is_anomaly", "lower_is_anomaly"
    }:
        return "not_ready", None, None, [{"modelKey": key, "reason": "threshold_unavailable"}]
    try:
        boundary = float(threshold["value"])
    except (KeyError, ValueError, TypeError):
        return "not_ready", None, None, [{"modelKey": key, "reason": "threshold_unavailable"}]
    if not math.isfinite(boundary):
        return "not_ready", None, None, [{"modelKey": key, "reason": "threshold_unavailable"}]
    anomalous = score >= boundary if threshold["direction"] == "higher_is_anomaly" else score <= boundary
    return "succeeded", float(score), anomalous, [{"modelKey": key, "featureSetId": str(feature_set_id),
                                                     "anomalous": anomalous}]


def process_detection_message(conn: Any, message: ClaimedMessage, root: Path | None) -> None:
    started = time.perf_counter()
    payload = message.payload
    observation = load_process_observation(conn, message)
    pinned = payload.get("configVersionId")
    config_id = uuid.UUID(pinned) if isinstance(pinned, str) else active_config(conn)
    if config_id is None:
        raise ValueError("active_config_unavailable")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT source_id FROM ml_config_versions WHERE id = %s", (config_id,))
            config = cur.fetchone()
            if config is None or config[0] != observation.source_id:
                raise ValueError("config_source_mismatch")
        feature_sets = materialize(conn, observation.observation_id, config_id)
        combined = hashlib.sha256(json.dumps(sorted((str(item[0]), item[1]) for item in feature_sets)).encode()).hexdigest()
        if payload.get("inputManifestHash") is not None and payload["inputManifestHash"] != combined:
            raise ValueError("input_manifest_mismatch")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT rv.rule_key, rv.version, rv.rule_spec, rv.input_spec
                     FROM ml_config_rules AS cr
                     JOIN ml_rule_versions AS rv ON rv.id = cr.rule_version_id
                    WHERE cr.config_version_id = %s ORDER BY cr.evaluation_order""",
                (config_id,),
            )
            rules = cur.fetchall()
            # Rule의 F.* 참조는 현재 구성의 고정 변수셋에서만 읽는다.
            if len(feature_sets) != 1:
                raise ValueError("ambiguous_rule_feature_set")
            feature_set_id, feature_manifest = feature_sets[0]
            cur.execute(
                """SELECT values_json FROM data_feature_materializations
                    WHERE observation_id = %s AND feature_set_id = %s AND input_manifest_hash = %s""",
                (observation.observation_id, feature_set_id, feature_manifest),
            )
            feature_row = cur.fetchone()
            if feature_row is None:
                raise ValueError("rule_features_unavailable")
            feature_values = feature_row[0]
            if any(item[3].get("featureSetId") not in (None, str(feature_set_id)) for item in rules):
                raise ValueError("rule_feature_set_mismatch")
        if rules:
            rule_status, rule_code, rule_evidence = evaluate_rules(
                [(item[0], item[1], item[2]) for item in rules],
                observation.allowed_values, feature_values)
        else:
            rule_status, rule_code, rule_evidence = "skipped", None, []
        ml_status, score, ml_anomaly, ml_evidence = _score(conn, config_id, observation.observation_id, root)
        ready_rule = rule_status in {"succeeded", "skipped"}
        ready_ml = ml_status == "succeeded"
        status = "complete" if ready_rule and ready_ml else "partial" if rule_status == "succeeded" or ready_ml else "unavailable"
        code = rule_code or "IF_ANOMALY" if ml_anomaly else rule_code
        evidence = {"schemaVersion": 1, "rules": rule_evidence, "models": ml_evidence,
                    "inputManifestHash": combined}
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO run_process_decisions
                     (id, execution_id, process_observation_id, config_version_id,
                      input_manifest_hash, status, rule_status, ml_status,
                      decision_code, anomaly_score, evidence, decided_at, latency_status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s::jsonb, clock_timestamp(), 'unavailable')
                   ON CONFLICT (execution_id, process_observation_id, config_version_id, input_manifest_hash)
                   DO NOTHING""",
                (uuid.uuid4(), observation.execution_id, observation.observation_id, config_id,
                 combined, status, rule_status, ml_status, code, score, json.dumps(evidence)),
            )
            cur.execute(
                """INSERT INTO ops_pipeline_measurements
                     (id, observation_id, stage, config_version_id, duration_ms)
                   VALUES (%s, %s, 'detector', %s, %s)""",
                (uuid.uuid4(), observation.observation_id, config_id,
                 (time.perf_counter() - started) * 1000),
            )
        if not acknowledge(conn, "detection", message):
            raise RuntimeError("detection_lease_lost")


def run(*, once: bool = False, poll_seconds: float = 1.0) -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    root = Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    from detector.applications import prepare_active, process_application_once
    worker_id = f"detector-{uuid.uuid4()}"
    with psycopg.connect(database_url) as conn:
        try:
            prepare_active(conn, root)
            conn.commit()
        except model_adapters.ModelNotConfigured:
            conn.rollback()
            raise SystemExit("활성 모델 로더가 구성되지 않아 Detector를 시작할 수 없다")
        while True:
            process_application_once(conn, root, worker_id)
            conn.commit()
            if active_config(conn) is None:
                conn.commit()
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            conn.commit()
            config_id = active_config(conn)
            source_id = active_source(conn, config_id)
            conn.commit()
            message = claim(conn, "detection", source_id=str(source_id))
            conn.commit()
            if message is None:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            try:
                process_detection_message(conn, message, root)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                reason = str(exc) if isinstance(exc, ValueError) else "detection_failed"
                fail(conn, "detection", message, reason=reason[:100])
                conn.commit()
            if once:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
