"""기간 지정 학습·추천·평가 작업을 큐에서 가져와 실행한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

import model_adapters
from generation_client import GenerationUnavailable
from feature_runtime import calculate_features, validate_definitions
from feature_fit import requires_fit, fit_features, transform, definitions_hash
from detector.artifacts import verified_artifact_path
from detector.rules import evaluate_rules
from methods.registry import resolve_training_method
from pg_queue import CLEAR_PROGRESS, ClaimedMessage, acknowledge, claim, fail, _table, reconcile_dead_jobs
from trainer.lease import TrainingLeaseHeartbeat, lock_training_ownership
from trainer.model_version import (SCHEMA_VERSION, build_spec, check_training_scope, content_hash as model_version_hash,
                                   is_model_version_request, read_model_key, read_schema_version, training_period)
from trainer.operational_state import read_operational_state
from trainer.snapshot import build_snapshot
from trainer.selection import select_training
from trainer.workspace import cleanup_orphan_workspaces, training_workspace
from detector.status_rule import default_policy
from telemetry import cost_family, queue_wait_ms
from threshold_recommendation import PROVIDER_V3, all_rejected, parse_job

# 계약상 폐지된 작업 종류(llm-recommendation-job.md "폐지 유지"). API가 새 요청을 422 job_kind_abolished로 막고,
# 워커는 남은 대기·임대 작업을 실행하지 않고 같은 코드로 바로 끝낸다(재시도·LLM 호출·스냅샷 생성 없음).
# 이미 저장된 run_llm_results 행은 그대로 읽힌다.
ABOLISHED_JOB_KINDS = frozenset({"recommend_feature", "recommend_rule"})
# 판정 정책 deadlineMs 상한(detection-decision.md). v3 평가 예열 행에만 쓴다.
WARMUP_DEADLINE_MS = 30000
# 종료 오류 코드 허용 형식: 코드 또는 코드:식별자(예 method_not_executable:ml:eif — 계약의 오류 형식).
# 그 외 ValueError 문구는 노출하지 않고 invalid_job_input으로 바꾼다.
_ERROR_CODE_PATTERN = re.compile(r'[a-z][a-z0-9_]{0,99}(?::[a-z][a-z0-9_:-]{0,99})?')


def _terminal_error_code(exc: BaseException) -> str:
    text = str(exc)
    return text if _ERROR_CODE_PATTERN.fullmatch(text) else 'invalid_job_input'


def _retry_error_code(exc: BaseException) -> str:
    """재시도 실패의 저장 코드. 설명의 GPU 대기 초과만 따로 남기고 그 밖은 기존 `job_processing_failed`다."""
    if isinstance(exc, GenerationUnavailable) and str(exc) == "generation_busy":
        return "generation_busy"
    return "job_processing_failed"


# 역할별로 정리(reconcile_dead_jobs)하는 큐. llm_explanation 역할은 자기 큐만 정리한다.
_RECONCILED_QUEUES = {
    "trainer": ("training",),
    "llm": ("training", "llm_recommendation", "llm_explanation"),
    "llm_explanation": ("llm_explanation",),
}


def _claim_job(conn: Any, message: ClaimedMessage, worker_id: str) -> tuple[dict[str, Any], uuid.UUID, uuid.UUID] | None:
    payload = message.payload
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("jobId"), str):
        raise ValueError("invalid_training_message")
    job_id = uuid.UUID(payload["jobId"])
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, status, input_spec, requested_by_actor_id,
                          attempt_count, max_attempts, current_attempt_id, requester_role,contract_version,llm_spec
                     FROM run_jobs WHERE id = %s FOR UPDATE""",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("job_not_found")
            cur.execute(f"""SELECT 1 FROM {_table(message.queue)} WHERE id=%s AND status='leased'
                AND lease_token=%s AND lease_until>clock_timestamp() FOR UPDATE""", (message.id,message.lease_token))
            if cur.fetchone() is None:
                raise RuntimeError("job_queue_ownership_lost")
            if row[1] in ("recommend_thresholds", "recommend_relations", "train", "evaluate", "import_csv",
                          "evaluation_set", "compare", "explain_protocol") and row[8] != "admin":
                raise ValueError("job_role_not_authorized")
            if row[2] in ("succeeded", "failed"):
                if not acknowledge(conn, message.queue, message):
                    raise RuntimeError("training_lease_lost")
                return None
            if row[7] is not None:
                cur.execute(
                    """UPDATE run_job_attempts
                          SET status = 'expired', finished_at = clock_timestamp(),
                              error_code = 'lease_expired'
                        WHERE id = %s AND status = 'running' AND lease_until <= clock_timestamp()""",
                    (row[7],),
                )
                cur.execute("SELECT status FROM run_job_attempts WHERE id = %s", (row[7],))
                if cur.fetchone()[0] == "running":
                    raise RuntimeError("job_already_running")
            if row[5] >= row[6]:
                cur.execute("UPDATE run_jobs SET status = 'failed', error_code = 'max_attempts', finished_at = clock_timestamp(), "
                            f"{CLEAR_PROGRESS} WHERE id = %s", (job_id,))
                if not acknowledge(conn, message.queue, message):
                    raise RuntimeError("training_lease_lost")
                return None
            attempt_id = uuid.uuid4()
            attempt_token = uuid.uuid4()
            cur.execute(
                """INSERT INTO run_job_attempts
                     (id, job_id, attempt_no, status, worker_id, lease_token,
                      lease_until, started_at)
                   VALUES (%s, %s, %s, 'running', %s, %s,
                           clock_timestamp() + interval '60 seconds', clock_timestamp())""",
                (attempt_id, job_id, row[5] + 1, worker_id, attempt_token),
            )
            # 새 임대는 이전 시도(크래시·재임대)가 남긴 임계값 추천 진행률을 비우고 시작한다.
            cur.execute(
                f"""UPDATE run_jobs SET status = 'running', attempt_count = attempt_count + 1,
                          current_attempt_id = %s, {CLEAR_PROGRESS} WHERE id = %s""",
                (attempt_id, job_id),
            )
    return ({"id": job_id, "kind": row[1], "input": row[3], "actor": row[4],
             "contractVersion": row[9], "llmSpec": row[10]}, attempt_id, attempt_token)


def _snapshot_rows(conn: Any, snapshot_id: uuid.UUID, split: str | None = None,
                   limit: int = 1000000, *, allow_sample: bool = False) -> list[tuple[uuid.UUID, dict[str, Any], str, dict[str, Any] | None]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.observation_id, o.allowed_values, m.split, m.label_evidence
                 FROM data_snapshot_members AS m
                 JOIN data_observations AS o ON o.id = m.observation_id
                WHERE m.snapshot_id = %s AND (%s::text IS NULL OR m.split::text = %s)
                ORDER BY m.observation_id LIMIT %s""",
            (snapshot_id, split, split, limit + 1),
        )
        rows = cur.fetchall()
    if len(rows) > limit:
        if allow_sample:
            return rows[:limit]
        raise ValueError("snapshot_row_limit_exceeded")
    return rows


def _definitions(conn: Any, feature_set_id: uuid.UUID) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.output_name, m.output_position, v.expression, v.requires_fit
                 FROM ml_feature_set_members AS m
                 JOIN ml_feature_versions AS v ON v.id = m.feature_version_id
                WHERE m.feature_set_id = %s AND m.model_input ORDER BY m.output_position""",
            (feature_set_id,),
        )
        rows = cur.fetchall()
    if not rows or [row[1] for row in rows] != list(range(len(rows))) or \
            any(bool(row[3]) != requires_fit(row[2]) for row in rows):
        raise ValueError("invalid_feature_set")
    return validate_definitions([{"name": row[0], "expression": row[2]} for row in rows])


def _training_inputs(conn, snapshot_id, feature_set_id):
    definitions = _definitions(conn, feature_set_id)
    observations, selection = select_training(_snapshot_rows(conn, snapshot_id, 'train'))
    conn.commit()
    references = [{'raw': raw, 'label': labels.get('label') if isinstance(labels, dict) else None}
                  for _, raw, _, labels in observations]
    state = fit_features(definitions, references, snapshot_id=snapshot_id) if any(
        requires_fit(item['expression']) for item in definitions) else None
    if state is not None:
        state['reference']['trainingSelection'] = selection
    prepared = []
    for row in references:
        values, missing = transform(definitions, row['raw'], state) if state is not None else calculate_features(definitions, row['raw'])
        prepared.append({'features': values, 'missing': missing, 'label': row['label']})
    selection['featureCompleteCount'] = sum(not row['missing'] for row in prepared)
    selection['featureMissingCount'] = len(prepared) - selection['featureCompleteCount']
    return prepared, [item['name'] for item in definitions], state, definitions_hash(definitions), selection


def _register_artifact(conn: Any, *, root: Path, path_value: Any, kind: str,
                       snapshot_id: uuid.UUID, feature_set_id: uuid.UUID,
                       job_id: uuid.UUID, attempt_id: uuid.UUID,
                       runtime_version: str, format_version: str,
                       fit_artifact_id: uuid.UUID | None = None) -> uuid.UUID:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("invalid_artifact_path")
    root = root.resolve(strict=True)
    path = Path(path_value).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.suffix != ".joblib":
        raise ValueError("artifact_path_not_allowed")
    size = path.stat().st_size
    if size < 1 or size > 2 * 1024 * 1024 * 1024:
        raise ValueError("artifact_size_not_allowed")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    artifact_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ml_artifacts
                 (id, kind, relative_path, content_hash, byte_size, snapshot_id,
                  feature_set_id, fit_artifact_id, created_job_id, created_attempt_id,
                  format_version, runtime_version, metadata, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s::jsonb, clock_timestamp())""",
            (artifact_id, kind, str(path.relative_to(root)), digest.hexdigest(), size,
             snapshot_id, feature_set_id, fit_artifact_id, job_id, attempt_id,
             format_version, runtime_version, json.dumps({"trainSplit": "train", "loader": "trusted-joblib"})),
        )
    return artifact_id


def _promote_output(root: Path, output: Path, path_value: Any, kind: str) -> str:
    """검증된 임시 파일을 영속 모델·fit 디렉터리에 원자적으로 옮긴다."""
    if not isinstance(path_value, str):
        raise ValueError("invalid_artifact_path")
    source = Path(path_value)
    source_resolved = source.resolve(strict=True)
    if not source_resolved.is_relative_to(output.resolve(strict=True)) or not source.is_file() or \
            source.is_symlink() or source.suffix != ".joblib":
        raise ValueError("artifact_path_not_allowed")
    destination_dir = (root / ("models" if kind == "model" else "fit")).resolve()
    if not destination_dir.is_relative_to(root.resolve(strict=True)):
        raise ValueError("artifact_path_not_allowed")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4()}.joblib"
    os.replace(source, destination)
    return str(destination)


def _execute(conn: Any, job: dict[str, Any], attempt_id: uuid.UUID,
             artifact_root: Path | None, message: ClaimedMessage,
             attempt_token: uuid.UUID) -> dict[str, Any]:
    if job["kind"] in ABOLISHED_JOB_KINDS:
        # ValueError라 run()이 종료 실패(_finish terminal)로 기록한다. DB를 읽거나 쓰기 전에 멈춘다.
        raise ValueError("job_kind_abolished")
    started = time.monotonic()
    queue_wait = queue_wait_ms(conn, message, attempt_id)
    spec = job["input"]
    kind = job["kind"]
    if kind == 'import_csv':
        from trainer.prepared_import import execute as execute_import
        return execute_import(conn,job,artifact_root,message,attempt_id,attempt_token)
    if kind in ('evaluation_set', 'compare'):
        # 평가 세트 생성·세트 위 모델 버전 비교(config-comparison-evaluation.md v1 구현 범위).
        from trainer import comparison
        if kind == 'evaluation_set':
            return comparison.execute_evaluation_set(conn, job, message, attempt_id, attempt_token)
        return comparison.execute_compare(conn, job, artifact_root, message, attempt_id, attempt_token)
    if kind in ('train','evaluate') and spec.get('snapshotId'):
        from trainer.prepared_execution import maybe_execute
        prepared_result = maybe_execute(conn,job,artifact_root,message,attempt_id,attempt_token)
        if prepared_result is not None: return prepared_result
    training_method = training_parameters = operational = None
    model_version_request = kind == 'train' and is_model_version_request(spec)
    if kind == 'train':
        # 카탈로그 기준 검증: 미등록 키 unsupported_model / 실행 불가 method_not_executable:<id> /
        # parameters 위반 method_parameters_invalid:<id>. modelKey·parameters 부재(v1·v2 요청)는 IF 기본값.
        training_method, training_parameters = resolve_training_method(spec)
        if spec.get('detectionPolicy', {}).get('mode') == 'rule_only' and 'modelKey' in spec:
            raise ValueError('model_not_selected')
        check_training_scope(spec)
        if model_version_request:
            # 시도 시작에 고정한다. 읽을 수 없으면 긴 학습을 돌리기 전에 멈춘다.
            operational = read_operational_state(conn)
            conn.commit()
    if kind == "recommend_relations":
        # 실험 기능: 층 B 관계·처리 시간 후보. 스냅샷을 읽기만 하고 결과 JSON만 남긴다.
        from trainer.relation_candidates import execute as execute_relations
        return execute_relations(conn, job)
    if kind == "explain":
        from explanation import explain
        return explain(job)
    if kind == "explain_protocol":
        # 프로토콜 경로 설명(protocol-explanation-job.md). 점검 실패는 재시도 없이 사유 코드만 남긴다(문장 없음).
        from protocol_explanation_job import CheckFailed, explain_protocol
        try:
            return explain_protocol(conn, job)
        except CheckFailed as failed:
            _finish(conn, message, job, attempt_id, attempt_token, None, failed.code, True,
                    failure_summary=failed.summary)
            return {"_finalized": True}
    if kind == "evaluate":
        return _evaluate(conn, job, artifact_root, message, attempt_id, attempt_token)
    # 임계값 추천은 스냅샷을 만들기 전에 모드·대상을 확정하고, 기간 키는 `data` 아래에 있다.
    threshold_plan = parse_job(spec, job.get("llmSpec")) if kind == "recommend_thresholds" else None
    period = spec["data"] if threshold_plan else spec
    source_id = uuid.UUID(spec["sourceId"])
    with conn.cursor() as cur:
        cur.execute("SELECT snapshot_id FROM run_job_snapshots WHERE job_id=%s", (job["id"],))
        fixed_snapshot = cur.fetchone()
    if fixed_snapshot is None and period.get("snapshotId"):
        with conn.cursor() as cur:
            if threshold_plan:
                # 계약은 snapshotId만 보내는 요청을 허용한다. 기간을 함께 보내면 일치해야 한다.
                cur.execute("""SELECT id FROM data_snapshots WHERE id=%s AND source_id=%s AND status='ready' AND schema_version='process-v1'
                    AND (%s::timestamptz IS NULL OR (selection_spec->>'from')::timestamptz=%s::timestamptz)
                    AND (%s::timestamptz IS NULL OR (selection_spec->>'until')::timestamptz=%s::timestamptz)""",
                    (period["snapshotId"],source_id,period.get("from"),period.get("from"),period.get("until"),period.get("until")))
            else:
                cur.execute("""SELECT id FROM data_snapshots WHERE id=%s AND source_id=%s AND status='ready'
                    AND (selection_spec->>'from')::timestamptz=%s::timestamptz AND (selection_spec->>'until')::timestamptz=%s::timestamptz""", (spec["snapshotId"],source_id,spec["from"],spec["until"]))
            fixed_snapshot = cur.fetchone()
        if fixed_snapshot is None:
            raise ValueError("snapshot_conditions_mismatch")
        conn.commit()
        with conn.transaction():
            if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                raise RuntimeError("snapshot_ownership_lost")
            with conn.cursor() as cur:
                cur.execute("INSERT INTO run_job_snapshots(job_id,snapshot_id) VALUES(%s,%s)", (job["id"],fixed_snapshot[0]))
    if fixed_snapshot:
        from trainer.snapshot import SnapshotResult
        with conn.cursor() as cur:
            cur.execute("SELECT split::text,count(*) FROM data_snapshot_members WHERE snapshot_id=%s GROUP BY split", (fixed_snapshot[0],))
            counts = dict(cur.fetchall())
            cur.execute("SELECT manifest_hash FROM data_snapshots WHERE id=%s", (fixed_snapshot[0],))
            manifest = cur.fetchone()[0]
        snapshot = SnapshotResult(fixed_snapshot[0], manifest, counts)
    else:
        try:
            snapshot = build_snapshot(conn, source_id=source_id, from_time=period["from"],
                                      until_time=period["until"], actor_id=job["actor"])
        except ValueError as exc:
            if threshold_plan and str(exc) == "snapshot_insufficient_process_rows":
                raise ValueError("recommendation_input_empty") from exc
            raise
        conn.commit()
        with conn.transaction():
            if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                raise RuntimeError("snapshot_ownership_lost")
            with conn.cursor() as cur:
                cur.execute("INSERT INTO run_job_snapshots(job_id,snapshot_id) VALUES(%s,%s)", (job["id"],snapshot.id))
                cur.execute("UPDATE run_jobs SET input_snapshot_id=%s WHERE id=%s AND contract_version IS NULL", (snapshot.id,job["id"]))
    if threshold_plan:
        rows = _snapshot_rows(conn, snapshot.id, "train")
        source_tz = None
        with conn.cursor() as cur:
            cur.execute("SELECT selection_spec->>'timeBasis' FROM data_snapshots WHERE id=%s", (snapshot.id,))
            time_basis = cur.fetchone()[0]
            if threshold_plan.provider == PROVIDER_V3:
                # rule_v3는 원천 시각으로 참조/현재를 나눈다. 시간대 규칙은 Detector 사건 묶음과 같다.
                from rule_v3_capture import source_timezone
                cur.execute("SELECT clock_basis FROM ops_sources WHERE id=%s", (source_id,))
                clock = cur.fetchone()
                source_tz = source_timezone(clock[0] if clock else None)
        conn.commit()
        if not rows:
            raise ValueError("recommendation_input_empty")
        completed = model_adapters.recommend_thresholds(
            threshold_plan, rows, snapshot.id, time_basis, source_tz=source_tz,
            progress=_threshold_progress(conn, message, job, attempt_id, attempt_token))
        conn.commit()
        if all_rejected(completed):
            _finish(conn, message, job, attempt_id, attempt_token, None, "recommendation_all_rejected", True,
                    failure_summary=completed)
            return {"_finalized": True}
        return completed
    feature_set_id = uuid.UUID(spec["featureSetId"])
    if kind == "train" and spec.get("ruleVersionIds"):
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM ml_rule_versions WHERE id = ANY(%s::uuid[])
                     AND rule_spec->>'state' <> 'retired'
                     AND (input_spec->>'featureSetId' IS NULL OR input_spec->>'featureSetId' = %s)""",
                (spec["ruleVersionIds"], str(feature_set_id)),
            )
            if cur.fetchone()[0] != len(spec["ruleVersionIds"]):
                raise ValueError("rule_feature_set_mismatch")
    if kind == "train":
        from detector.policy import parse_policy
        policy = parse_policy(spec, required=spec.get("detectionPolicy") is not None)
        if policy.mode == "rule_only":
            if any(requires_fit(item['expression']) for item in _definitions(conn, feature_set_id)):
                raise ValueError('rule_only_fitted_features_unsupported')
            config_id = uuid.uuid4()
            _, selection = select_training(_snapshot_rows(conn, snapshot.id, 'train'))
            selection['modelFitted'] = False
            conn.commit()
            config_spec = {"schemaVersion": 2, "sourceId": str(source_id), "featureSetId": str(feature_set_id),
                "trainingSnapshotId": str(snapshot.id), "ruleVersionIds": spec["ruleVersionIds"],
                "detectionPolicy": spec["detectionPolicy"], "statusRulePolicy": default_policy(source_id),
                'trainingSelection': selection}
            with conn.transaction():
                if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                    raise RuntimeError("training_ownership_lost")
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,
                        content_hash,created_by_actor_id,created_at) VALUES(%s,%s,%s::jsonb,'{}'::jsonb,%s,%s,clock_timestamp())""",
                        (config_id,source_id,json.dumps(config_spec),hashlib.sha256(json.dumps(config_spec,sort_keys=True).encode()).hexdigest(),job["actor"]))
                    for order, rule_id in enumerate(spec["ruleVersionIds"]):
                        cur.execute("INSERT INTO ml_config_rules(config_version_id,rule_version_id,evaluation_order) VALUES(%s,%s,%s)", (config_id,rule_id,order))
                completed = {"snapshotId": str(snapshot.id), "featureSetId": str(feature_set_id),
                             "configVersionId": str(config_id),"splitCounts": snapshot.counts,
                             'trainingSelection': selection,
                             "executionCost": cost_family({'totalMs': (time.monotonic()-started)*1000},
                                                          batch_size=0, queue_wait=queue_wait)}
                completed['executionCost']['details']['scope'] = 'offline_training'
                _finish(conn,message,job,attempt_id,attempt_token,completed,None,True)
            return {**completed,"_finalized": True}
        if artifact_root is None:
            raise model_adapters.ModelNotConfigured("artifact_root_not_configured")
        preprocessing_started = time.monotonic()
        prepared, names, preprocessing, definition_digest, selection = _training_inputs(conn, snapshot.id, feature_set_id)
        preprocessing_ms = (time.monotonic()-preprocessing_started)*1000
        period = None
        if model_version_request:
            # selection은 fit 참조와 같은 객체다. fit 필요 피처가 있으면 fit 참조에도 함께 남는다.
            period = selection['trainingPeriod'] = training_period(spec)
        # tmp/<attempt>는 성공·실패 모두 블록 종료 시 제거된다. 승격(models/·fit/)된 파일은 등록 TX가
        # 정상 확정되면 남고, 블록이 예외로 끝나 TX가 롤백되면 미등록 파일로서 함께 제거된다.
        with training_workspace(artifact_root, attempt_id) as (output, promoted):
            training_started = time.monotonic()
            # 어댑터는 레지스트리가 model_adapters 함수에 늦게 바인딩한다(현재 ml:iforest → train_iforest).
            fit_kwargs = {"preprocessing": preprocessing} if preprocessing is not None else {}
            result = training_method.adapter.fit(prepared, names, str(output), parameters=training_parameters, **fit_kwargs)
            training_ms = (time.monotonic()-training_started)*1000
            required = {"modelPath", "fitPath", "formatVersion", "runtimeVersion", "thresholdSpec"}
            if not isinstance(result, dict) or not required.issubset(result) or not isinstance(result["thresholdSpec"], dict):
                raise ValueError("invalid_training_artifacts")
            fit_path = _promote_output(artifact_root, output, result["fitPath"], "fit")
            promoted.append(fit_path)
            model_path = _promote_output(artifact_root, output, result["modelPath"], "model")
            promoted.append(model_path)
            with conn.transaction():
                if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
                    raise RuntimeError("training_ownership_lost")
                fit_id = _register_artifact(conn, root=artifact_root, path_value=fit_path, kind="fit",
                                            snapshot_id=snapshot.id, feature_set_id=feature_set_id,
                                            job_id=job["id"], attempt_id=attempt_id,
                                            runtime_version=result["runtimeVersion"], format_version=result["formatVersion"])
                model_id = _register_artifact(conn, root=artifact_root, path_value=model_path, kind="model",
                                              snapshot_id=snapshot.id, feature_set_id=feature_set_id,
                                              job_id=job["id"], attempt_id=attempt_id,
                                              runtime_version=result["runtimeVersion"], format_version=result["formatVersion"],
                                              fit_artifact_id=fit_id)
                rule_ids = [uuid.UUID(value) for value in spec.get("ruleVersionIds", [])]
                config_id = uuid.uuid4()
                if model_version_request:
                    config_spec = build_spec(config_id=config_id, job_id=job["id"], source_id=source_id,
                                             feature_set_id=feature_set_id, snapshot_id=snapshot.id,
                                             method=training_method, parameters=training_parameters,
                                             parameters_requested=bool(spec.get("parameters")),
                                             model_artifact_id=model_id, fit_artifact_id=fit_id,
                                             selection=selection, period=period)
                    content_hash = model_version_hash(config_spec)
                else:
                    config_spec = {"schemaVersion": 1, "featureSetId": str(feature_set_id),
                                   "trainingSnapshotId": str(snapshot.id), "ruleVersionIds": [str(value) for value in rule_ids],
                                   "statusRulePolicy": default_policy(source_id), "trainingSelection": selection}
                    config_spec["sourceId"] = str(source_id)
                    if spec.get("detectionPolicy"):
                        config_spec["detectionPolicy"] = spec["detectionPolicy"]
                    content_hash = hashlib.sha256(json.dumps(config_spec, sort_keys=True).encode()).hexdigest()
                training_settings = {"snapshotId": str(snapshot.id), "split": "train", "featureNames": names,
                                     "definitionsHash": definition_digest, "splitVersion": "execution-block-v1",
                                     "thresholdPolicy": training_parameters["thresholdPolicy"],
                                     "trainingSelection": selection,
                                     "methodId": training_method.method_id,
                                     "methodVersion": training_method.method_version,
                                     "parameters": training_parameters}
                if model_version_request:
                    training_settings.update(timeBasis=period["timeBasis"], trainingPeriod=period)
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO ml_config_versions
                             (id, source_id, configuration_spec, explanation_spec, content_hash,
                              created_by_actor_id, created_at)
                           VALUES (%s, %s, %s::jsonb, '{}'::jsonb, %s, %s, clock_timestamp())""",
                        (config_id, source_id, json.dumps(config_spec), content_hash, job["actor"]),
                    )
                    cur.execute(
                        """INSERT INTO ml_config_models
                             (config_version_id, model_key, feature_set_id, model_artifact_id,
                              fit_artifact_id, adapter_spec, training_settings, threshold_spec)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)""",
                        (config_id, training_method.model_key, feature_set_id, model_id, fit_id,
                         json.dumps({"name": "sklearn-iforest", "formatVersion": result["formatVersion"]}),
                         json.dumps(training_settings),
                         json.dumps(result["thresholdSpec"])),
                    )
                    for order, rule_id in enumerate(rule_ids):
                        cur.execute(
                            """INSERT INTO ml_config_rules
                                 (config_version_id, rule_version_id, evaluation_order)
                               VALUES (%s, %s, %s)""",
                            (config_id, rule_id, order),
                        )
                completed = {"snapshotId": str(snapshot.id), "featureSetId": str(feature_set_id),
                             "configVersionId": str(config_id), "artifactId": str(model_id),
                             "splitCounts": snapshot.counts, "trainingSelection": selection,
                             "executionCost": cost_family({'totalMs': (time.monotonic()-started)*1000,
                                'preprocessMs': preprocessing_ms, 'trainMs': training_ms},
                                batch_size=len(prepared), queue_wait=queue_wait)}
                completed['executionCost']['details']['modelNJobs'] = 2
                completed['executionCost']['details']['scope'] = 'offline_training'
                if operational is not None:
                    completed.update(operational.result_fields())
                _finish(conn, message, job, attempt_id, attempt_token, completed, None, True)
        return {**completed, "_finalized": True}
    raise ValueError("unsupported_job_kind")


def _prepare_model(context: dict[str, Any], root: Path | None) -> None:
    """행 기한 밖에서 산출물을 검증·적재한다. 실패는 예외로 올라가 작업을 끝낸다(`applications.prepare_config`와 같은 순서)."""
    from detector.computation import model_reference
    reference = model_reference(context, root)
    model_adapters.prepare_iforest(reference)
    if any(requires_fit(item['expression']) for item in context['definitions']):
        model_adapters.transform_iforest_features(reference, context['definitions'], {})


def _version_context(conn: Any, config_id: uuid.UUID, source_id: uuid.UUID,
                     pinned: Any = None) -> tuple[dict[str, Any], Any]:
    """평가할 모델 버전 1개의 실행 문맥. v3는 운영 정책(시도 시작 판독 또는 비교 접수 고정값 `pinned`)으로
    그 모델 하나만, v1·v2는 구성에 저장된 정책·룰 그대로 평가한다. evaluate와 비교 작업(`trainer/comparison.py`)이 공유한다."""
    from detector.computation import load_context
    from detector.policy import Policy
    with conn.cursor() as cur:
        cur.execute("SELECT configuration_spec FROM ml_config_versions WHERE id=%s", (config_id,))
        stored = cur.fetchone()
    # 없는 구성은 아래 load_context가 config_source_mismatch로 끝낸다.
    version_spec = stored[0] if stored is not None and read_schema_version(stored[0]) == SCHEMA_VERSION else None
    model_key = read_model_key(version_spec) if version_spec is not None else None
    operational = (pinned if pinned is not None else read_operational_state(conn)) if version_spec is not None else None
    context = load_context(conn, config_id, source_id)
    if operational is not None:
        if [model[7] for model in context["models"]] != [model_key]:
            raise ValueError("model_unavailable")
        # v3 구성에는 정책·룰이 없다. load_context의 기본값(병렬)이 아니라 그 모델 하나만 평가한다.
        context = {**context, "policy": Policy("ml_only", operational.deadline_ms, "selected_path")}
    return context, operational


# 평가 제외 사유(config-comparison-evaluation.md "평가 제외 행"). v1은 이 하나뿐이다.
MODEL_INPUTS_UNDEFINED = "model_inputs_undefined"
ALL_ROWS_EXCLUDED = "all_rows_excluded"


def _inputs_undefined_check(context: dict[str, Any]) -> Any:
    """평가 제외 판정 함수(raw → bool) 또는 None(제외하지 않음).

    ML 경로가 선택된 모델 1개 구성에서, 모델 입력 변수를 **그 행 자신의 값만으로** 계산할 수 없는 행 = 입력 미정의 행
    (0으로 나누기·열 없음·숫자/시각 해석 불가)이다. 판정은 `calculate_features`(Detector가 쓰는 같은 계산)를
    행 수준 식에 적용한다: fit 없는 정의는 식 그대로, `group_centered`는 안쪽 `value` 식, fit 정의(`group_median`·
    `group_centered`)는 그룹 열(`P.adapter_type`)이 있는지(`feature_fit._group`). fit 상태(학습 그룹 목록)는 보지 않는다 —
    학습에 없던 그룹은 데이터 결손이 아니라 모델 적용 범위 밖이므로 제외하지 않고, compute에서 미판정
    (`model_features_unavailable`)으로 남아 `unmeasured`가 된다. `group_median`은 행 값을 쓰지 않으므로 안쪽 식은 보지 않는다.
    계산 자체가 예외를 내면 제외하지 않고 실행 경로(미판정)에 맡긴다."""
    from feature_fit import _group as fit_group
    if len(context["models"]) != 1 or context["policy"].mode == "rule_only":
        return None
    row_level: list[dict[str, Any]] = []
    grouped = False
    for item in context["definitions"]:
        expression = item["expression"]
        if requires_fit(expression):
            grouped = True
            if expression["op"] == "group_centered":
                row_level.append({"name": item["name"], "expression": expression["value"]})
        else:
            row_level.append(item)

    def undefined(raw: Any) -> bool:
        if grouped and fit_group(raw) is None:
            return True
        if not row_level:
            return False
        try:
            _, missing = calculate_features(row_level, raw)
        except Exception:
            # compute의 guarded와 같은 범위: decimal ArithmeticError 등 계산 예외는 제외하지 않고
            # 실행 경로에 맡긴다(그 행은 component_failed 미판정). 여기서 새면 작업 전체가 실패한다.
            return False
        return bool(missing)
    return undefined


def _measure_version(context: dict[str, Any], operational: Any, rows: list[Any], root: Path | None, *,
                     provenance: dict[str, Any], started: float, queue_wait: float | None,
                     synthetic_baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """고정 행 위에서 모델 버전 1개를 행마다 계산해 evaluate와 같은 지표·요약을 만든다(DB 쓰기 없음).

    `ml`은 비교 항목용 추가 집계(ML 경로 선택 여부·성공 행 수·ML 혼동행렬·라벨 수·행별 ML 판정·점수)이며
    evaluate 결과·평가 행에는 넣지 않는다.

    모델 입력이 정의되지 않는 행(`_inputs_undefined_check`)은 계산 전에 평가에서 뺀다. 뺀 행은 모집단·완료·미판정·
    라벨·혼동행렬·점수·행 처리 시간 어디에도 세지 않고 `excludedCount`·`excludedReasons`로만 남긴다
    (populationCount + excludedCount = 입력 행 수)."""
    from detector.computation import compute
    from detector.policy import Policy
    undefined = _inputs_undefined_check(context)
    excluded_flags = [undefined is not None and undefined(row[1]) for row in rows]
    evaluated = [row for row, flag in zip(rows, excluded_flags) if not flag]
    excluded = len(rows) - len(evaluated)
    excluded_reasons = {MODEL_INPUTS_UNDEFINED: excluded} if excluded else {}
    policy_evidence = operational.evidence_fields() if operational is not None else {"detectionPolicy": context["policy"].document()}
    rules = context["rules"]
    threshold = context["models"][0][6] if context["models"] else None
    scores: list[float] = []
    unavailable = 0
    unlabeled = 0
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    model_confusion = dict.fromkeys(confusion, 0)
    rule_confusion = dict.fromkeys(confusion, 0)
    rule_unavailable = 0
    ml_succeeded = 0
    label_counts = {"anomaly": 0, "normal": 0}

    def record(target: dict[str, int], predicted: bool, label: str) -> None:
        key = "tp" if predicted and label == "anomaly" else "fp" if predicted else \
            "fn" if label == "anomaly" else "tn"
        target[key] += 1

    def classification_metrics(target: dict[str, int]) -> dict[str, int | float]:
        return {**target,
                "precision": target["tp"] / (target["tp"] + target["fp"]) if target["tp"] + target["fp"] else None,
                "recall": target["tp"] / (target["tp"] + target["fn"]) if target["tp"] + target["fn"] else None,
                "falsePositiveRate": target["fp"] / (target["fp"] + target["tn"]) if target["fp"] + target["tn"] else None,
                "reason": None if sum(target.values()) else 'labels_unavailable'}

    complete_count = 0
    checker_results, samples = [], []
    preprocess_samples, inference_samples = [], []
    if operational is not None:
        # 새 버전은 적재 캐시가 없어 첫 행이 기한을 넘기면 평가 전체가 unmeasured가 된다. 측정 행의 기한은 운영과 같다.
        # compute는 오류를 행 미판정으로 바꾸므로 적재는 직접 하고, 첫 행은 최대 기한으로 한 번 돌려 결과를 버린다.
        _prepare_model(context, root)
        # 전 행 제외면 rows[0](제외 행)으로 예열한다. 결과는 버리고 ml()은 변수 결측에서 바로 not_ready로 끝나므로
        # 해가 없다(모델 적재는 위 _prepare_model이 이미 했다).
        warmup = evaluated[0] if evaluated else rows[0]
        compute({**context, "policy": Policy("ml_only", WARMUP_DEADLINE_MS, "selected_path")}, warmup[1], root)
    ml_predictions: dict[str, bool] = {}
    ml_scores: list[float] = []
    for (observation_id, raw, _, label_evidence), excluded_row in zip(rows, excluded_flags):
        if excluded_row:
            if synthetic_baseline is not None:
                # 합성 평가는 원본 전 행의 기준 판정을 쓴다(제외 행 포함, 아래 집계에는 넣지 않음).
                from trainer.synthetic_metrics import prediction
                synthetic_baseline[str(observation_id)] = prediction(compute(context, raw, root))
            continue
        item_started = time.monotonic()
        result = compute(context, raw, root)
        if synthetic_baseline is not None:
            from trainer.synthetic_metrics import prediction
            synthetic_baseline[str(observation_id)] = prediction(result)
        samples.append((time.monotonic()-item_started)*1000)
        checker_results.append({checker: {key: result[checker].get(key) for key in ('status', 'anomalous')}
                                for checker in ('hr', 'sr', 'ml')})
        if 'preprocessMs' in result['ml']:
            preprocess_samples.append(result['ml']['preprocessMs'])
        if 'inferenceMs' in result['ml']:
            inference_samples.append(result['ml']['inferenceMs'])
        if result['ml'].get('status') == 'succeeded':
            ml_succeeded += 1
            if type(result['ml'].get('anomalous')) is bool:
                ml_predictions[str(observation_id)] = result['ml']['anomalous']
                if isinstance(result['ml'].get('score'), (int, float)):
                    ml_scores.append(float(result['ml']['score']))
        rule_status = result["rule"]["status"]
        rule_prediction = result["rule"].get("anomalous")
        model_prediction = result["ml"].get("anomalous")
        score = result["ml"].get("score")
        if score is not None:
            scores.append(score)
        if result["status"] != "complete":
            unavailable += 1
        else:
            complete_count += 1
        if rule_status not in {"succeeded","skipped"}:
            rule_unavailable += 1
        label = label_evidence.get("label") if isinstance(label_evidence, dict) else None
        if label not in {"normal", "anomaly"}:
            unlabeled += 1
            continue
        label_counts[label] += 1
        if model_prediction is not None:
            record(model_confusion, model_prediction, label)
        if rule_prediction is not None:
            record(rule_confusion, rule_prediction, label)
        if result["status"] == "complete":
            record(confusion, result["anomalous"], label)
    reasons = []
    if len(evaluated) < 20 or confusion["tp"] + confusion["fn"] == 0 or confusion["fp"] + confusion["tn"] == 0:
        reasons.append("insufficient_labeled_population")
    if unlabeled:
        reasons.append("labels_unavailable")
    if complete_count != len(evaluated):
        reasons.append("selected_paths_unavailable")
    if rule_unavailable:
        reasons.append("rule_inputs_unavailable")
    if not evaluated:
        # 전 행 제외: 평가한 행이 없으므로 valid(완료 0 = 모집단 0)로 두지 않는다.
        reasons.append(ALL_ROWS_EXCLUDED)
    applicability = "valid" if evaluated and complete_count == len(evaluated) else "unmeasured"
    metrics = {**classification_metrics(confusion),
               "model": classification_metrics(model_confusion),
               "rule": classification_metrics(rule_confusion) if rules or context.get('statusRulePolicy') else None,
               "combination": context["policy"].combination, **policy_evidence, "ruleCount": len(rules),
               "scoreMin": min(scores) if scores else None,
               "scoreMax": max(scores) if scores else None,
               "excludedCount": excluded, "excludedReasons": dict(excluded_reasons)}
    summary = {"populationCount": len(evaluated), "scoredCount": complete_count,
               "unavailableCount": unavailable, "unlabeledCount": unlabeled,
               "excludedCount": excluded, "excludedReasons": dict(excluded_reasons),
               "applicability": applicability, "metrics": metrics, "reasonCodes": reasons}
    from trainer.metrics import comparison_family
    comparison = comparison_family(checker_results, {**provenance,
        'population': 'immutable_snapshot_process_revisions', 'scope': 'offline_snapshot',
        'statusRulePolicy': context.get('statusRulePolicy'), **policy_evidence})
    execution_cost = cost_family({'totalMs': (time.monotonic()-started)*1000,
        'evaluationMs': sum(samples), 'preprocessMs': sum(preprocess_samples) if preprocess_samples else None,
        'inferenceMs': sum(inference_samples) if inference_samples else None},
        batch_size=len(evaluated), samples=samples, queue_wait=queue_wait)
    execution_cost['details'].update(scope='offline_snapshot', modelNJobs=2 if context['models'] else None)
    metrics['ruleMlComparison'] = comparison
    return {"summary": summary, "metrics": metrics, "reasons": reasons, "comparison": comparison,
            "executionCost": execution_cost, "policyEvidence": policy_evidence, "threshold": threshold,
            "ml": {"selected": context["policy"].mode != "rule_only", "succeededCount": ml_succeeded,
                   "confusion": dict(model_confusion), "labelCounts": dict(label_counts),
                   # ML succeeded이고 anomalous가 bool인 행만: observation id → 판정, 그 행들의 점수.
                   "predictions": ml_predictions, "scores": ml_scores}}


def _evaluation_spec(measured: dict[str, Any], metric_version: str, purpose: str) -> dict[str, Any]:
    return {"schemaVersion": 2, "metricVersion": metric_version, **measured["policyEvidence"],
            "threshold": measured["threshold"], "labelSource": "manual_snapshot_evidence",
            "purpose": purpose}


def _insert_evaluation(cur: Any, *, evaluation_id: uuid.UUID, job_id: uuid.UUID, config_id: uuid.UUID,
                       snapshot_id: Any, purpose: str, metric_version: str, evaluation_spec: dict[str, Any],
                       measured: dict[str, Any], comparison_id: uuid.UUID | None = None,
                       evaluation_set_id: uuid.UUID | None = None) -> None:
    """평가 행 1개. evaluate는 계보 열 2개가 NULL이고, 비교 작업은 비교·세트 id를 채운다(011 계보 트리거)."""
    summary = measured["summary"]
    cur.execute(
        """INSERT INTO run_evaluations
             (id, job_id, config_version_id, snapshot_id, purpose,
              evaluation_spec_version, evaluation_spec_hash, evaluation_spec,
              applicability, population_count, scored_count, unavailable_count,
              metrics, reason_codes, comparison_id, evaluation_set_id, completed_at)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                   %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, clock_timestamp())""",
        (evaluation_id, job_id, config_id, snapshot_id, purpose, metric_version,
         hashlib.sha256(json.dumps(evaluation_spec, sort_keys=True).encode()).hexdigest(),
         json.dumps(evaluation_spec), summary["applicability"],
         summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
         json.dumps(measured["metrics"]), json.dumps(measured["reasons"]), comparison_id, evaluation_set_id),
    )


def _evaluate(conn: Any, job: dict[str, Any], root: Path | None,
              message: ClaimedMessage, attempt_id: uuid.UUID,
              attempt_token: uuid.UUID) -> dict[str, Any]:
    started = time.monotonic()
    queue_wait = queue_wait_ms(conn, message, attempt_id)
    spec = job["input"]
    config_id = uuid.UUID(spec["configVersionId"])
    snapshot_id = uuid.UUID(spec["snapshotId"])
    context, operational = _version_context(conn, config_id, uuid.UUID(spec["sourceId"]))
    rows = _snapshot_rows(conn, snapshot_id, spec["purpose"])
    conn.commit()
    if not rows:
        raise ValueError("evaluation_empty_population")
    synthetic_baseline = None
    if spec.get('syntheticEvaluation') is not None:
        from trainer.synthetic_profiles import validate_request
        from trainer.synthetic_snapshot import MAX_COHORT_ROWS
        validate_request(spec['syntheticEvaluation'])
        if len(rows) > MAX_COHORT_ROWS:
            raise ValueError('synthetic_cohort_limit_exceeded')
        synthetic_baseline = {}
    measured = _measure_version(context, operational, rows, root, started=started, queue_wait=queue_wait,
                                provenance={'sourceId': spec['sourceId'], 'snapshotId': str(snapshot_id),
                                            'configVersionId': str(config_id), 'purpose': spec['purpose']},
                                synthetic_baseline=synthetic_baseline)
    summary, metrics, reasons = measured["summary"], measured["metrics"], measured["reasons"]
    comparison, execution_cost = measured["comparison"], measured["executionCost"]
    synthetic = None
    metric_version = 'selected-paths-v3'
    if synthetic_baseline is not None:
        from trainer.synthetic_execution import evaluate as evaluate_synthetic
        synthetic = evaluate_synthetic(conn, spec, context, rows, root, job['id'], baseline=synthetic_baseline)
        if synthetic['status'] != 'measured':
            raise ValueError(synthetic['reason'])
        execution_cost['details'].update(scope='offline_original_snapshot_only', syntheticProcessingIncluded=False)
        metrics['syntheticEvaluation'] = synthetic
        # Synthetic method success cannot authorize an operational model switch.
        summary['applicability'] = 'unmeasured'
        reasons.append('synthetic_not_operational_validation')
        metric_version = 'synthetic-method-v1'
    evaluation_id = uuid.uuid4()
    evaluation_spec = _evaluation_spec(measured, metric_version, spec["purpose"])
    if synthetic is not None:
        evaluation_spec['syntheticEvaluation'] = spec['syntheticEvaluation']
        evaluation_spec['syntheticProfileBinding'] = spec['syntheticProfileBinding']
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        with conn.cursor() as cur:
            _insert_evaluation(cur, evaluation_id=evaluation_id, job_id=job["id"], config_id=config_id,
                               snapshot_id=snapshot_id, purpose=spec["purpose"], metric_version=metric_version,
                               evaluation_spec=evaluation_spec, measured=measured)
        completed = {**summary, "evaluationId": str(evaluation_id),
                     "ruleMlComparison": comparison, "executionCost": execution_cost}
        if synthetic is not None:
            completed['syntheticEvaluation'] = synthetic
        if operational is not None:
            completed.update(operational.result_fields())
        _finish(conn, message, job, attempt_id, attempt_token, completed, None, True)
    return {**completed, "_finalized": True}


def _threshold_progress(conn: Any, message: ClaimedMessage, job: dict[str, Any],
                        attempt_id: uuid.UUID, attempt_token: uuid.UUID):
    """임계값 추천 진행률: 실행 중 `result_summary` = {"progress": {"processed", "total"}}(필드 단위).

    소유권을 확인한 짧은 트랜잭션으로 쓰고 바로 끝낸다(생성 중 열린 트랜잭션 없음). 성공하면 `_finish`가 최종 결과로,
    재시도 대기면 NULL로, 종료 실패면 실패 요약 또는 NULL로 바꾼다.
    """
    def write(processed: int, total: int) -> None:
        with conn.transaction():
            if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
                raise RuntimeError("training_ownership_lost")
            with conn.cursor() as cur:
                cur.execute("UPDATE run_jobs SET result_summary = %s::jsonb WHERE id = %s AND status = 'running'",
                            (json.dumps({"progress": {"processed": processed, "total": total}}), job["id"]))
        conn.commit()
    return write


# 완료 때 run_llm_results 행을 함께 쓰는 v03 작업 종류(migration의 LLM 완료 결과 필수 목록과 같다 — 026 explain_protocol).
LLM_RESULT_KINDS = frozenset({"recommend_thresholds", "explain", "explain_protocol"})


def _llm_result_decision_id(job: dict[str, Any]) -> Any:
    """결과 행 decision_id = 작업 input_decision_id(erd_v03_validate_result). explain은 input_spec 최상위,
    explain_protocol은 알림 대상(target.kind = alert)만 판정이 있고 관측 경로 대상은 NULL이다."""
    spec = job["input"]
    if job["kind"] == "explain_protocol":
        target = spec.get("target") or {}
        return target.get("decisionId") if target.get("kind") == "alert" else None
    return spec.get("decisionId")


def _finish(conn: Any, message: ClaimedMessage, job: dict[str, Any],
            attempt_id: uuid.UUID, attempt_token: uuid.UUID,
            result: dict[str, Any] | None, error_code: str | None,
            terminal: bool, *, failure_summary: dict[str, Any] | None = None) -> None:
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        if result is not None and job['kind'] in {'train', 'evaluate'}:
            from trainer.service_contract import enrich_result
            enrich_result(conn, job, result)
        with conn.cursor() as cur:
            if result is not None and job.get("contractVersion") == "v03" and job["kind"] in LLM_RESULT_KINDS:
                spec = job["llmSpec"]
                cur.execute("""INSERT INTO run_llm_results
                    (id,job_id,kind,decision_id,config_version_id,evidence_version,input_evidence_hash,model_version,prompt_version,
                     output_schema_version,reuse_key,output,created_attempt_id,lease_token,completed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,clock_timestamp())""",
                    (uuid.uuid4(), job["id"], job["kind"], _llm_result_decision_id(job), job["input"].get("configVersionId"), spec["evidence_version"], spec["input_evidence_hash"],
                     spec["model_version"], spec["prompt_version"], spec["output_schema_version"],
                     spec["reuse_key"], json.dumps(result), attempt_id, attempt_token))
            cur.execute(
                """UPDATE run_job_attempts SET status = %s, finished_at = clock_timestamp(),
                          error_code = %s WHERE id = %s""",
                ("succeeded" if result is not None else "failed", error_code, attempt_id),
            )
            if result is not None:
                cur.execute(
                    """UPDATE run_jobs SET status = 'succeeded', result_summary = %s::jsonb,
                              result_config_version_id = %s, result_artifact_id = %s,
                              finished_at = clock_timestamp(), error_code = NULL WHERE id = %s""",
                    (json.dumps(result), result.get("configVersionId"), result.get("artifactId"), job["id"]),
                )
            elif terminal or message.attempts >= 3:
                # 전 항목 거절처럼 실패여도 사유 표를 보여 줘야 하는 작업만 요약을 남긴다.
                cur.execute(
                    """UPDATE run_jobs SET status = 'failed', error_code = %s, result_summary = %s::jsonb,
                              finished_at = clock_timestamp() WHERE id = %s""",
                    (error_code, None if failure_summary is None else json.dumps(failure_summary), job["id"]),
                )
            else:
                # 임계값 추천은 실행 중 진행률을 result_summary에 쓰므로 재시도 대기로 갈 때 비운다.
                cur.execute(
                    f"""UPDATE run_jobs SET status = 'queued', error_code = %s,
                              next_run_at = clock_timestamp() + interval '5 seconds',
                              {CLEAR_PROGRESS}
                        WHERE id = %s""",
                    (error_code, job["id"]),
                )
        done = acknowledge(conn, message.queue, message) if result is not None or terminal else \
            fail(conn, message.queue, message, reason=error_code or "job_failed")
        if not done:
            raise RuntimeError("training_queue_lease_lost")


def run(*, once: bool = False, poll_seconds: float = 1.0, role: str = "trainer") -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    root = Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    worker_id = f"{role}-{uuid.uuid4()}"
    priority = True
    with psycopg.connect(database_url) as conn:
        if role == "trainer" and root is not None:
            # 이전 실행(크래시·중단)이 남긴 tmp/<attempt> 고아 폴더 정리. 임대가 살아 있는 attempt는 남긴다.
            try:
                cleanup_orphan_workspaces(conn, root)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logging.getLogger(__name__).warning("orphan training workspace cleanup skipped: %s", exc)
        while True:
            if role == "trainer":
                message = claim(conn, "training", lease_seconds=60, job_kinds=("train", "evaluate", "import_csv", "recommend_relations",
                                                                          "evaluation_set", "compare"))
            elif role == "llm":
                message = None
                # A bounded rotation prevents explanation starvation under recommendation load.
                rotation = ("llm_recommendation", "llm_explanation") if priority else ("llm_explanation", "llm_recommendation")
                priority = not priority
                for queue in rotation:
                    message = claim(conn, queue, lease_seconds=60)
                    if message:
                        break
                if message is None:
                    # Drain earlier releases after leases expire without rewriting their history.
                    # 폐지 종류도 남겨 둬야 옛 training 큐 메시지가 job_kind_abolished로 끝난다(_execute 첫 검사).
                    message = claim(conn, "training", lease_seconds=60,
                                    job_kinds=("recommend_feature", "recommend_rule", "explain"))
            elif role == "llm_explanation":
                # 설명 전용 소비자: 추천 생성이 GPU를 쓰는 동안에도 설명을 받아 GPU 차례에서 먼저 넣는다.
                message = claim(conn, "llm_explanation", lease_seconds=60)
                if message is None:
                    message = claim(conn, "training", lease_seconds=60, job_kinds=("explain",))
            else:
                raise ValueError("invalid_worker_role")
            for queue in _RECONCILED_QUEUES[role]:
                reconcile_dead_jobs(conn,queue)
            conn.commit()
            if message is None:
                if once:
                    return
                time.sleep(poll_seconds)
                continue
            try:
                claimed = _claim_job(conn, message, worker_id)
                conn.commit()
                if claimed is None:
                    if once:
                        return
                    continue
                job, attempt_id, attempt_token = claimed
                heartbeat = TrainingLeaseHeartbeat(lambda: psycopg.connect(database_url),
                                                   message, job["id"], attempt_id, attempt_token)
                heartbeat.start()
                try:
                    result = _execute(conn, job, attempt_id, root, message, attempt_token)
                    conn.commit()
                finally:
                    heartbeat.stop()
                if result.get("_finalized"):
                    if once:
                        return
                    continue
                if heartbeat.lost:
                    raise RuntimeError("training_ownership_lost")
                _finish(conn, message, job, attempt_id, attempt_token, result, None, True)
                conn.commit()
            except model_adapters.ModelNotConfigured as exc:
                conn.rollback()
                if 'job' in locals() and 'attempt_id' in locals():
                    _finish(conn, message, job, attempt_id, attempt_token, None, str(exc), True)
                    conn.commit()
            except ValueError as exc:
                conn.rollback()
                # Validation failures are terminal; never expose arbitrary exception text.
                code = _terminal_error_code(exc)
                if 'job' in locals() and 'attempt_id' in locals():
                    _finish(conn, message, job, attempt_id, attempt_token, None, code, True)
                    conn.commit()
                else:
                    fail(conn, message.queue, message, reason='job_claim_failed')
                    conn.commit()
            except Exception as exc:
                conn.rollback()
                if 'job' in locals() and 'attempt_id' in locals():
                    try:
                        _finish(conn, message, job, attempt_id, attempt_token, None, _retry_error_code(exc), False)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                else:
                    fail(conn, message.queue, message, reason="job_claim_failed")
                    conn.commit()
            finally:
                if 'job' in locals():
                    del job
                if 'attempt_id' in locals():
                    del attempt_id
            if once:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
