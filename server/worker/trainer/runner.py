"""기간 지정 학습·추천·평가 작업을 큐에서 가져와 실행한다."""

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
from feature_runtime import calculate_features, validate_definitions
from feature_fit import requires_fit, fit_features, transform, definitions_hash
from detector.artifacts import verified_artifact_path
from detector.rules import evaluate_rules
from feature_worker import materialize_set
from pg_queue import ClaimedMessage, acknowledge, claim, fail, _table, reconcile_dead_jobs
from trainer.catalog import register_feature_set
from trainer.rule_candidates import validate_suggestions
from trainer.lease import TrainingLeaseHeartbeat, lock_training_ownership
from trainer.snapshot import build_snapshot

SAFE_MODEL_FIELDS = frozenset({
    "success_count", "error_count", "total_count", "retry_count", "status",
    "response_code", "adapter_type", "channel_id", "process_hub_id", "process_id",
    "start_time_raw", "end_time_raw",
})


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
            if row[1] in ("recommend_feature", "recommend_rule", "train", "evaluate") and row[8] != "admin":
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
                cur.execute("UPDATE run_jobs SET status = 'failed', error_code = 'max_attempts', finished_at = clock_timestamp() WHERE id = %s", (job_id,))
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
            cur.execute(
                """UPDATE run_jobs SET status = 'running', attempt_count = attempt_count + 1,
                          current_attempt_id = %s WHERE id = %s""",
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
    if not any(requires_fit(item['expression']) for item in definitions):
        rows, names = _feature_rows(conn, snapshot_id, feature_set_id, 'train')
        return rows, names, None, definitions_hash(definitions)
    observations = _snapshot_rows(conn, snapshot_id, 'train')
    conn.commit()
    references = [{'raw': raw, 'label': labels.get('label') if isinstance(labels, dict) else None}
                  for _, raw, _, labels in observations]
    state = fit_features(definitions, references, snapshot_id=snapshot_id)
    prepared = []
    for row in references:
        values, missing = transform(definitions, row['raw'], state)
        prepared.append({'features': values, 'missing': missing, 'label': row['label']})
    return prepared, [item['name'] for item in definitions], state, definitions_hash(definitions)


def _feature_rows(conn: Any, snapshot_id: uuid.UUID, feature_set_id: uuid.UUID,
                  split: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    definitions = _definitions(conn, feature_set_id)
    names = [item["name"] for item in definitions]
    prepared: list[dict[str, Any]] = []
    observations = _snapshot_rows(conn, snapshot_id, split)
    conn.commit()
    for observation_id, _, _, label_evidence in observations:
        with conn.transaction():
            manifest = materialize_set(conn, observation_id, feature_set_id)
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT values_json, missing_names FROM data_feature_materializations
                        WHERE observation_id = %s AND feature_set_id = %s AND input_manifest_hash = %s""",
                    (observation_id, feature_set_id, manifest),
                )
                values, missing = cur.fetchone()
        prepared.append({"features": values, "missing": missing,
                         "label": label_evidence.get("label") if isinstance(label_evidence, dict) else None})
    return prepared, names


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
    spec = job["input"]
    kind = job["kind"]
    if kind == 'train':
        if spec.get('modelKey', 'iforest') != 'iforest':
            raise ValueError('unsupported_model')
        if spec.get('detectionPolicy', {}).get('mode') == 'rule_only' and 'modelKey' in spec:
            raise ValueError('model_not_selected')
    if kind == "explain":
        from explanation import explain
        return explain(job)
    if kind == "evaluate":
        return _evaluate(conn, job, artifact_root, message, attempt_id, attempt_token)
    source_id = uuid.UUID(spec["sourceId"])
    with conn.cursor() as cur:
        cur.execute("SELECT snapshot_id FROM run_job_snapshots WHERE job_id=%s", (job["id"],))
        fixed_snapshot = cur.fetchone()
    if fixed_snapshot is None and spec.get("snapshotId"):
        with conn.cursor() as cur:
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
        snapshot = build_snapshot(conn, source_id=source_id, from_time=spec["from"],
                                  until_time=spec["until"], actor_id=job["actor"])
        conn.commit()
        with conn.transaction():
            if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                raise RuntimeError("snapshot_ownership_lost")
            with conn.cursor() as cur:
                cur.execute("INSERT INTO run_job_snapshots(job_id,snapshot_id) VALUES(%s,%s)", (job["id"],snapshot.id))
                cur.execute("UPDATE run_jobs SET input_snapshot_id=%s WHERE id=%s AND contract_version IS NULL", (snapshot.id,job["id"]))
    if kind == "recommend_feature":
        rows = _snapshot_rows(conn, snapshot.id, "train", 10000, allow_sample=True)
        conn.commit()
        safe_rows = [{key: value for key, value in raw.items() if key in SAFE_MODEL_FIELDS}
                     for _, raw, _, _ in rows]
        recommendation = model_adapters.recommend_features(safe_rows)
        definitions = recommendation.get("definitions", []) if isinstance(recommendation, dict) else recommendation
        details = {key: value for key, value in recommendation.items() if key != "definitions"} if isinstance(recommendation, dict) else {}
        if not definitions:
            return {"snapshotId": str(snapshot.id), "featureNames": [], "materializedCount": 0, **details}
        feature_set_id = register_feature_set(conn, definitions, job["actor"])
        # 제안된 변수는 별도의 수동 선택 없이 요청 기간의 전체 스냅샷에 적용한다.
        materialized = 0
        observations = _snapshot_rows(conn, snapshot.id)
        conn.commit()
        for observation_id, _, _, _ in observations:
            with conn.transaction():
                materialize_set(conn, observation_id, feature_set_id)
            materialized += 1
        return {"snapshotId": str(snapshot.id), "featureSetId": str(feature_set_id),
                "featureNames": [item["name"] for item in definitions],
                "materializedCount": materialized, **details,
                "referenceSampleCount": len(safe_rows), "referenceTrainPopulationCount": snapshot.counts["train"],
                "referenceSampling": "observation_id_order_train_prefix_max_10000"}
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
    if kind == "recommend_rule":
        prepared, names = _feature_rows(conn, snapshot.id, feature_set_id, "train")
        definitions = _definitions(conn, feature_set_id)
        conn.commit()
        recommendation = model_adapters.recommend_rules(
            prepared[:10000], str(feature_set_id), definitions)
        conn.commit()
        suggested = validate_suggestions(recommendation.get("suggestions", [])
                                         if isinstance(recommendation, dict) else recommendation, names)
        # 추천은 초안일 뿐이며 채택·승인 전에는 탐지에 사용하지 않는다.
        return {"snapshotId": str(snapshot.id), "featureSetId": str(feature_set_id),
                "featureNames": names, "suggestions": suggested,
                **({key: value for key, value in recommendation.items() if key != "suggestions"}
                   if isinstance(recommendation, dict) else {})}
    if kind == "train":
        from detector.policy import parse_policy
        policy = parse_policy(spec, required=spec.get("detectionPolicy") is not None)
        if policy.mode == "rule_only":
            if any(requires_fit(item['expression']) for item in _definitions(conn, feature_set_id)):
                raise ValueError('rule_only_fitted_features_unsupported')
            if not spec.get("ruleVersionIds"):
                raise ValueError("rules_required")
            config_id = uuid.uuid4()
            config_spec = {"schemaVersion": 2, "sourceId": str(source_id), "featureSetId": str(feature_set_id),
                "trainingSnapshotId": str(snapshot.id), "ruleVersionIds": spec["ruleVersionIds"],
                "detectionPolicy": spec["detectionPolicy"]}
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
                             "configVersionId": str(config_id),"splitCounts": snapshot.counts}
                _finish(conn,message,job,attempt_id,attempt_token,completed,None,True)
            return {**completed,"_finalized": True}
        if artifact_root is None:
            raise model_adapters.ModelNotConfigured("artifact_root_not_configured")
        prepared, names, preprocessing, definition_digest = _training_inputs(conn, snapshot.id, feature_set_id)
        output = artifact_root / "tmp" / str(attempt_id)
        output.mkdir(parents=True, exist_ok=True)
        result = model_adapters.train_iforest(prepared, names, str(output), preprocessing=preprocessing) if preprocessing is not None else \
            model_adapters.train_iforest(prepared, names, str(output))
        required = {"modelPath", "fitPath", "formatVersion", "runtimeVersion", "thresholdSpec"}
        if not isinstance(result, dict) or not required.issubset(result) or not isinstance(result["thresholdSpec"], dict):
            raise ValueError("invalid_training_artifacts")
        fit_path = _promote_output(artifact_root, output, result["fitPath"], "fit")
        model_path = _promote_output(artifact_root, output, result["modelPath"], "model")
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
            config_spec = {"schemaVersion": 1, "featureSetId": str(feature_set_id),
                           "trainingSnapshotId": str(snapshot.id), "ruleVersionIds": [str(value) for value in rule_ids]}
            config_spec["sourceId"] = str(source_id)
            if spec.get("detectionPolicy"):
                config_spec["detectionPolicy"] = spec["detectionPolicy"]
            content_hash = hashlib.sha256(json.dumps(config_spec, sort_keys=True).encode()).hexdigest()
            config_id = uuid.uuid4()
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
                       VALUES (%s, 'iforest', %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)""",
                    (config_id, feature_set_id, model_id, fit_id,
                     json.dumps({"name": "sklearn-iforest", "formatVersion": result["formatVersion"]}),
                     json.dumps({"snapshotId": str(snapshot.id), "split": "train", "featureNames": names,
                                 "definitionsHash": definition_digest, "splitVersion": "execution-block-v1",
                                 "thresholdPolicy": "train-internal-p99-v1"}),
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
                         "splitCounts": snapshot.counts}
            _finish(conn, message, job, attempt_id, attempt_token, completed, None, True)
        return {**completed, "_finalized": True}
    raise ValueError("unsupported_job_kind")


def _evaluate(conn: Any, job: dict[str, Any], root: Path | None,
              message: ClaimedMessage, attempt_id: uuid.UUID,
              attempt_token: uuid.UUID) -> dict[str, Any]:
    spec = job["input"]
    config_id = uuid.UUID(spec["configVersionId"])
    snapshot_id = uuid.UUID(spec["snapshotId"])
    from detector.computation import load_context, compute
    context = load_context(conn, config_id, uuid.UUID(spec["sourceId"]))
    rules = context["rules"]
    rows = _snapshot_rows(conn, snapshot_id, spec["purpose"])
    conn.commit()
    if not rows:
        raise ValueError("evaluation_empty_population")
    threshold = context["models"][0][6] if context["models"] else None
    scores: list[float] = []
    unavailable = 0
    unlabeled = 0
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    model_confusion = dict.fromkeys(confusion, 0)
    rule_confusion = dict.fromkeys(confusion, 0)
    rule_unavailable = 0

    def record(target: dict[str, int], predicted: bool, label: str) -> None:
        key = "tp" if predicted and label == "anomaly" else "fp" if predicted else \
            "fn" if label == "anomaly" else "tn"
        target[key] += 1

    def classification_metrics(target: dict[str, int]) -> dict[str, int | float]:
        return {**target,
                "precision": target["tp"] / max(1, target["tp"] + target["fp"]),
                "recall": target["tp"] / max(1, target["tp"] + target["fn"]),
                "falsePositiveRate": target["fp"] / max(1, target["fp"] + target["tn"])}

    complete_count = 0
    for _, raw, _, label_evidence in rows:
        result = compute(context, raw, root)
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
        if model_prediction is not None:
            record(model_confusion, model_prediction, label)
        if rule_prediction is not None:
            record(rule_confusion, rule_prediction, label)
        if result["status"] == "complete":
            record(confusion, result["anomalous"], label)
    reasons = []
    if len(rows) < 20 or confusion["tp"] + confusion["fn"] == 0 or confusion["fp"] + confusion["tn"] == 0:
        reasons.append("insufficient_labeled_population")
    if unlabeled:
        reasons.append("labels_unavailable")
    if complete_count != len(rows):
        reasons.append("selected_paths_unavailable")
    if rule_unavailable:
        reasons.append("rule_inputs_unavailable")
    applicability = "valid" if not reasons else "unmeasured"
    metrics = {**classification_metrics(confusion),
               "model": classification_metrics(model_confusion),
               "rule": classification_metrics(rule_confusion) if rules else None,
               "combination": context["policy"].combination, "detectionPolicy": context["policy"].document(), "ruleCount": len(rules),
               "scoreMin": min(scores) if scores else None,
               "scoreMax": max(scores) if scores else None}
    summary = {"populationCount": len(rows), "scoredCount": complete_count,
               "unavailableCount": unavailable, "unlabeledCount": unlabeled,
               "applicability": applicability, "metrics": metrics, "reasonCodes": reasons}
    evaluation_id = uuid.uuid4()
    evaluation_spec = {"schemaVersion": 1, "metricVersion": "selected-paths-v2", "detectionPolicy": context["policy"].document(),
                       "threshold": threshold, "labelSource": "manual_snapshot_evidence",
                       "purpose": spec["purpose"]}
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO run_evaluations
                     (id, job_id, config_version_id, snapshot_id, purpose,
                      evaluation_spec_version, evaluation_spec_hash, evaluation_spec,
                      applicability, population_count, scored_count, unavailable_count,
                      metrics, reason_codes, completed_at)
                     VALUES (%s, %s, %s, %s, %s, 'selected-paths-v2', %s, %s::jsonb,
                           %s, %s, %s, %s, %s::jsonb, %s::jsonb, clock_timestamp())""",
                (evaluation_id, job["id"], config_id, snapshot_id, spec["purpose"],
                 hashlib.sha256(json.dumps(evaluation_spec, sort_keys=True).encode()).hexdigest(),
                 json.dumps(evaluation_spec), applicability,
                 len(rows), complete_count, unavailable,
                 json.dumps(metrics), json.dumps(reasons)),
            )
        completed = {**summary, "evaluationId": str(evaluation_id)}
        _finish(conn, message, job, attempt_id, attempt_token, completed, None, True)
    return {**completed, "_finalized": True}


def _finish(conn: Any, message: ClaimedMessage, job: dict[str, Any],
            attempt_id: uuid.UUID, attempt_token: uuid.UUID,
            result: dict[str, Any] | None, error_code: str | None,
            terminal: bool) -> None:
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        if result is not None and job['kind'] in {'train', 'evaluate'}:
            from trainer.service_contract import enrich_result
            enrich_result(conn, job, result)
        with conn.cursor() as cur:
            if result is not None and job.get("contractVersion") == "v03" and job["kind"] in {"recommend_feature", "recommend_rule", "explain"}:
                spec = job["llmSpec"]
                cur.execute("""INSERT INTO run_llm_results
                    (id,job_id,kind,decision_id,config_version_id,evidence_version,input_evidence_hash,model_version,prompt_version,
                     output_schema_version,reuse_key,output,created_attempt_id,lease_token,completed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,clock_timestamp())""",
                    (uuid.uuid4(), job["id"], job["kind"], job["input"].get("decisionId"), job["input"].get("configVersionId"), spec["evidence_version"], spec["input_evidence_hash"],
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
                cur.execute(
                    """UPDATE run_jobs SET status = 'failed', error_code = %s,
                              finished_at = clock_timestamp() WHERE id = %s""",
                    (error_code, job["id"]),
                )
            else:
                cur.execute(
                    """UPDATE run_jobs SET status = 'queued', error_code = %s,
                              next_run_at = clock_timestamp() + interval '5 seconds'
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
        while True:
            if role == "trainer":
                message = claim(conn, "training", lease_seconds=60, job_kinds=("train", "evaluate"))
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
                    message = claim(conn, "training", lease_seconds=60,
                                    job_kinds=("recommend_feature", "recommend_rule", "explain"))
            else:
                raise ValueError("invalid_worker_role")
            for queue in (("training",) if role == "trainer" else ("training","llm_recommendation","llm_explanation")):
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
                import re
                code = str(exc) if re.fullmatch(r'[a-z][a-z0-9_]{0,99}', str(exc)) else 'invalid_job_input'
                if 'job' in locals() and 'attempt_id' in locals():
                    _finish(conn, message, job, attempt_id, attempt_token, None, code, True)
                    conn.commit()
                else:
                    fail(conn, message.queue, message, reason='job_claim_failed')
                    conn.commit()
            except Exception:
                conn.rollback()
                if 'job' in locals() and 'attempt_id' in locals():
                    try:
                        _finish(conn, message, job, attempt_id, attempt_token, None, "job_processing_failed", False)
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
