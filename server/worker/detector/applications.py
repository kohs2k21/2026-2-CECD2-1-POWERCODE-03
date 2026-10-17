"""Detector 프로세스가 모델 준비를 확인한 뒤 활성 포인터를 전환한다."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import model_adapters
from detector.artifacts import verified_artifact_path


def _model_reference(conn: Any, config_id: uuid.UUID, root: Path | None) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.relative_path, m.content_hash, f.relative_path, f.content_hash
                 FROM ml_config_models AS cm
                 JOIN ml_artifacts AS m ON m.id = cm.model_artifact_id AND m.kind = 'model'
                 JOIN ml_artifacts AS f ON f.id = cm.fit_artifact_id AND f.kind = 'fit'
                WHERE cm.config_version_id = %s AND cm.model_key = 'iforest'
                  AND m.fit_artifact_id = f.id AND cm.feature_set_id = m.feature_set_id
                  AND cm.feature_set_id = f.feature_set_id""",
            (config_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("model_artifacts_not_ready")
    model_path = verified_artifact_path(root, row[0], row[1])
    fit_path = verified_artifact_path(root, row[2], row[3])
    if model_path is None or fit_path is None:
        raise ValueError("artifact_hash_or_path_invalid")
    return {"modelPath": model_path, "fitPath": fit_path}


def prepare_config(conn, config_id, root):
    from detector.computation import load_context, model_reference
    from feature_fit import requires_fit
    with conn.cursor() as cur:
        cur.execute("SELECT source_id FROM ml_config_versions WHERE id=%s", (config_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("config_missing")
    context = load_context(conn, config_id, row[0])
    if context["policy"].mode != "ml_only" and not context["rules"] and not context["policy"].legacy and \
            context.get('statusRulePolicy') is None:
        raise ValueError("rules_unavailable")
    if context["policy"].mode != "rule_only":
        # Keep relational model/fit binding checks in addition to definition/order checks.
        _model_reference(conn, config_id, root)
        reference = model_reference(context, root)
        model_adapters.prepare_iforest(reference)
        if any(requires_fit(item['expression']) for item in context['definitions']):
            model_adapters.transform_iforest_features(reference, context['definitions'], {})


def prepare_active(conn: Any, root: Path | None) -> None:
    """재시작 시 DB의 활성 구성과 프로세스 메모리를 다시 맞춘다."""
    with conn.cursor() as cur:
        cur.execute("SELECT active_config_version_id FROM ops_system_state WHERE id = 1")
        row = cur.fetchone()
    if row and row[0]:
        prepare_config(conn, row[0], root)


def process_application_once(conn: Any, root: Path | None, worker_id: str) -> bool:
    """신청 하나를 임대하고 파일 검증·로드 후 CAS로 실제 전환한다."""
    token = uuid.uuid4()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """WITH picked AS (
                     SELECT id FROM run_applications
                      WHERE status = 'queued' OR (status = 'applying' AND lease_until <= clock_timestamp())
                      ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE run_applications AS a
                      SET status = 'applying', lease_token = %s, worker_id = %s,
                          lease_until = clock_timestamp() + interval '300 seconds'
                     FROM picked WHERE a.id = picked.id
                   RETURNING a.id, a.target_config_version_id, a.expected_active_version_id,
                             a.expected_system_revision, a.evaluation_id""",
                (token, worker_id),
            )
            application = cur.fetchone()
    if application is None:
        return False
    application_id, config_id, expected_active, expected_revision, evaluation_id = application
    try:
        prepare_config(conn, config_id, root)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM run_applications
                        WHERE id = %s AND status = 'applying' AND lease_token = %s
                          AND lease_until > clock_timestamp() FOR UPDATE""",
                    (application_id, token),
                )
                if cur.fetchone() is None:
                    raise RuntimeError("application_lease_lost")
                cur.execute(
                    """SELECT 1 FROM run_evaluations
                        WHERE id = %s AND config_version_id = %s AND applicability = 'valid'
                          AND purpose = 'validation' AND scored_count > 0 AND unavailable_count = 0""",
                    (evaluation_id, config_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("evaluation_gate_not_met")
                cur.execute(
                    """SELECT active_config_version_id, revision FROM ops_system_state
                        WHERE id = 1 FOR UPDATE""",
                )
                state = cur.fetchone()
                if state is None or state != (expected_active, expected_revision):
                    raise ValueError("active_config_changed")
                cur.execute(
                    """UPDATE ops_system_state SET active_config_version_id = %s,
                              active_application_id = %s, revision = revision + 1,
                              detector_state = %s::jsonb, changed_at = clock_timestamp()
                        WHERE id = 1""",
                    (config_id, application_id, '{"status":"ready"}'),
                )
                cur.execute(
                    """UPDATE run_applications SET status = 'succeeded',
                              transition_audit = %s::jsonb, activated_at = clock_timestamp(),
                              lease_token = NULL, lease_until = NULL
                        WHERE id = %s""",
                    ('{"actualTransition":"detector_loaded"}', application_id),
                )
        return True
    except (model_adapters.ModelNotConfigured, ValueError) as exc:
        conn.rollback()
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE run_applications SET status = 'failed', error_code = %s,
                              lease_token = NULL, lease_until = NULL
                        WHERE id = %s AND status = 'applying' AND lease_token = %s""",
                    (str(exc)[:100], application_id, token),
                )
        return True
