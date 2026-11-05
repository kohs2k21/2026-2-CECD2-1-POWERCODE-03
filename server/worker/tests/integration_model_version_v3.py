"""모델 버전 v3 산출·판독과 활성 집합·정책 리비전 읽기를 실제 DB·실제 IF 학습으로 확인한다.

API는 v3 생성이 열리기 전이라 v1 정책 없는 train을 받지 않으므로 큐에 직접 넣는다. 고객 입력 없음.
리비전 표는 append-only이고 다른 시험이 쓰지 않으므로 이미 쓴 QA DB에서도 다음 리비전을 이어 쓴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import joblib
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration_research_pipeline import observe, source
from methods.registry import iforest_parameters
from pg_queue import enqueue
from trainer import runner
from trainer.catalog import register_feature_set
from trainer.model_version import content_hash
from trainer.operational_state import DEFAULT_POLICY, read_operational_state

FIELD = {"op": "field", "field": "P.total_count"}
DEFINITIONS = [{"name": "count_raw", "expression": FIELD},
               {"name": "count_centered", "expression": {"op": "group_centered", "groupBy": "P.adapter_type", "value": FIELD}}]
V1_POLICY = {"mode": "ml_only", "deadlineMs": 30000, "combination": "selected_path"}
SPEC_KEYS = {"schemaVersion", "contractVersion", "contentHashVersion", "sourceId", "baseVersionId", "displayName",
             "catalogVersion", "featureSetId", "trainingSnapshotId", "methods", "trainingSelection", "createdFrom"}


def seed(conn):
    sid = source(conn, "Model version v3 fixture")
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    for index in range(1000):
        raw = {"adapter_type": "A" if index % 2 else "B", "total_count": str(50 + index % 13),
               "success_count": str(50 + index % 13), "error_count": "0", "status": "S"}
        observe(conn, sid, f"model-version-{sid}-{index}", raw, "normal", persisted=start + timedelta(seconds=index))
    features = register_feature_set(conn, DEFINITIONS, "fixture-admin", definition_kind="operator_defined")
    conn.commit()
    return {"kind": "train", "sourceId": str(sid), "featureSetId": str(features),
            "from": (start - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "until": (start + timedelta(seconds=1100)).isoformat().replace("+00:00", "Z")}


def submit(conn, spec, *, config=None, snapshot=None):
    job_id = uuid.uuid4()
    document = json.dumps(spec)
    conn.execute("""INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,idempotency_key,request_hash,
                        input_config_version_id,input_snapshot_id,input_spec,max_attempts,attempt_count,next_run_at,created_at,
                        contract_version,queue,producer_kind,producer_key)
                    VALUES(%s,%s,'queued','fixture-admin','admin',%s,%s,%s,%s,%s::jsonb,3,0,clock_timestamp(),clock_timestamp(),
                           'v03','training','user','fixture-admin')""",
                 (job_id, spec["kind"], str(job_id), hashlib.sha256(document.encode()).hexdigest(), config, snapshot, document))
    enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(job_id), "enqueuedAt": datetime.now(timezone.utc).isoformat()})
    conn.commit()
    return job_id


def finish(conn, job_id):
    """앞선 시험이 남긴 메시지가 있어도 이 작업이 끝날 때까지 돌린다."""
    for _ in range(5):
        with patch("trainer.runner.load_dotenv"):
            runner.run(once=True)
        conn.rollback()
        status, code, result = conn.execute("SELECT status,error_code,result_summary FROM run_jobs WHERE id=%s", (job_id,)).fetchone()
        conn.commit()
        if status in ("succeeded", "failed"):
            return status, code, result
    raise AssertionError(f"job {job_id} did not finish: {status} {code}")


def succeeded(conn, spec, **references):
    status, code, result = finish(conn, submit(conn, spec, **references))
    assert (status, code) == ("succeeded", None), (status, code)
    return result


def stored_config(conn, config_id):
    row = conn.execute("SELECT configuration_spec,content_hash,base_version_id FROM ml_config_versions WHERE id=%s", (config_id,)).fetchone()
    model = conn.execute("""SELECT model_key,feature_set_id,model_artifact_id,fit_artifact_id,training_settings
                              FROM ml_config_models WHERE config_version_id=%s""", (config_id,)).fetchall()
    rules = conn.execute("SELECT count(*) FROM ml_config_rules WHERE config_version_id=%s", (config_id,)).fetchone()[0]
    conn.commit()
    return row, model, rules


def append_active_set(conn, models, change, *, restored_from=None):
    """API 저장과 같은 표에 같은 형태로 쓴다. 조작 규칙 검사는 API 몫이라 여기서 다루지 않는다."""
    revision = conn.execute("SELECT max(revision)+1 FROM ops_active_model_set_revisions").fetchone()[0]
    snapshot = {"models": [{**model, "activatedAtRevision": model.get("activatedAtRevision", revision)} for model in models]}
    conn.execute("""INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,restored_from_revision,changed_by_actor_id)
                    VALUES(%s,%s::jsonb,%s::jsonb,%s,'fixture-admin')""",
                 (revision, json.dumps(snapshot), json.dumps([change]), restored_from))
    conn.commit()
    return revision, snapshot["models"]


def save_policy(conn, deadline_ms):
    revision = conn.execute("SELECT COALESCE(max(revision),0)+1 FROM ops_detection_policy_revisions").fetchone()[0]
    policy = {**DEFAULT_POLICY, "deadlineMs": deadline_ms}
    conn.execute("INSERT INTO ops_detection_policy_revisions(revision,policy,saved_by_actor_id) VALUES(%s,%s::jsonb,'fixture-admin')",
                 (revision, json.dumps(policy)))
    conn.commit()
    return revision, policy


def check_read(conn, revision, models):
    state = read_operational_state(conn)
    conn.commit()
    assert state.active_model_set_revision == revision, (state, revision)
    assert [(str(item.config_version_id), item.model_key) for item in state.active_models] == \
        [(item["configVersionId"], item["modelKey"]) for item in models], (state, models)


def verify_v3_train(conn, spec, artifact_root):
    before = read_operational_state(conn)
    conn.commit()
    result = succeeded(conn, {**spec, "modelKey": "iforest", "parameters": {"n_estimators": 64}})
    assert result["activeModelSetRevision"] == before.active_model_set_revision
    assert result["detectionPolicy"] == {"revision": before.detection_policy_revision, "policy": before.detection_policy,
                                         "fixedAt": "attempt_start"}
    (config, stored_hash, base_version), models, rules = stored_config(conn, result["configVersionId"])
    assert set(config) == SPEC_KEYS, set(config) ^ SPEC_KEYS
    assert (config["schemaVersion"], config["contractVersion"], config["contentHashVersion"]) == (3, "model-version-v1", 2)
    assert config["baseVersionId"] is None and base_version is None and rules == 0
    assert len(models) == 1, models
    key, feature_set, model_id, fit_id, settings = models[0]
    method = config["methods"][0]
    assert len(config["methods"]) == 1 and key == "iforest" and method["methodId"] == "ml:iforest"
    assert method["methodVersion"] == "process-iforest-v1" and method["parameterSource"] == "user"
    assert method["parameters"] == iforest_parameters({"n_estimators": 64}) == settings["parameters"]
    assert method["artifacts"]["modelArtifactId"] == str(model_id) and method["artifacts"]["fitArtifactId"] == str(fit_id)
    assert method["artifacts"]["candidateConfigVersionId"] == result["configVersionId"]
    assert method["featureSetId"] == config["featureSetId"] == str(feature_set) == spec["featureSetId"]
    assert config["createdFrom"]["trainJobId"] == method["artifacts"]["trainJobId"]
    assert stored_hash == content_hash(config) and len(stored_hash) == 64
    period = {"from": spec["from"], "until": spec["until"], "timeBasis": "persisted_at"}
    assert config["trainingSelection"]["trainingPeriod"] == period == settings["trainingPeriod"]
    assert settings["timeBasis"] == "persisted_at" and settings["methodId"] == "ml:iforest"
    assert settings["methodVersion"] == "process-iforest-v1" and settings["thresholdPolicy"] == method["parameters"]["thresholdPolicy"]
    assert result["trainingSelection"]["trainingPeriod"] == period
    assert result["modelManifest"]["modelKey"] == "iforest" and result["dataContract"]["ruleVersionIds"] == []
    assert result["dataContract"]["statusRulePolicy"] is None
    path = conn.execute("SELECT relative_path FROM ml_artifacts WHERE id=%s", (fit_id,)).fetchone()[0]
    conn.commit()
    fit = joblib.load(artifact_root / path)
    assert fit["preprocessing"]["reference"]["trainingSelection"]["trainingPeriod"] == period
    return result


def verify_active_set_operations(conn, spec, first):
    """apply·replace·retire·restore가 남긴 리비전을 Trainer가 뷰로 그대로 읽고 다음 결과에 싣는다."""
    entry = {"configVersionId": first["configVersionId"], "modelKey": "iforest", "displayName": "첫 버전"}
    applied, models = append_active_set(conn, [entry], {"type": "activated", "configVersionId": entry["configVersionId"], "modelKey": "iforest"})
    check_read(conn, applied, models)
    second = succeeded(conn, {**spec, "modelKey": "iforest"})
    assert second["activeModelSetRevision"] == applied
    (config, _, _), _, _ = stored_config(conn, second["configVersionId"])
    assert config["methods"][0]["parameterSource"] == "catalog_default"
    replaced, models = append_active_set(conn, [{**entry, "configVersionId": second["configVersionId"], "activatedAtRevision": applied}],
                                         {"type": "replaced", "configVersionId": second["configVersionId"], "modelKey": "iforest",
                                          "previousConfigVersionId": entry["configVersionId"]})
    check_read(conn, replaced, models)
    retired, models = append_active_set(conn, [], {"type": "retired", "configVersionId": second["configVersionId"], "modelKey": "iforest"})
    check_read(conn, retired, models)
    restored, models = append_active_set(conn, [{**entry, "activatedAtRevision": applied}],
                                         {"type": "restored", "configVersionId": entry["configVersionId"], "modelKey": "iforest"},
                                         restored_from=applied)
    check_read(conn, restored, models)
    assert applied < replaced < retired < restored
    return second, restored


def verify_v3_evaluate(conn, spec, version, revision):
    policy_revision, policy = save_policy(conn, 2500)
    request = {"kind": "evaluate", "sourceId": spec["sourceId"], "snapshotId": version["snapshotId"],
               "configVersionId": version["configVersionId"], "purpose": "validation"}
    result = succeeded(conn, request, config=version["configVersionId"], snapshot=version["snapshotId"])
    assert result["applicability"] == "valid" and result["scoredCount"] > 0 and result["unavailableCount"] == 0, result
    assert result["activeModelSetRevision"] == revision
    assert result["detectionPolicy"] == {"revision": policy_revision, "policy": policy, "fixedAt": "attempt_start"}
    evaluation = conn.execute("SELECT evaluation_spec,metrics FROM run_evaluations WHERE id=%s", (result["evaluationId"],)).fetchone()
    conn.commit()
    expected = {"activeModelSetRevision": revision, "detectionPolicyRevision": policy_revision, "detectionPolicy": policy}
    for document in (evaluation[0], evaluation[1], result["ruleMlComparison"]["details"]):
        assert {key: document.get(key) for key in expected} == expected, document
    assert evaluation[1]["combination"] == "selected_path" and evaluation[1]["ruleCount"] == 0


def verify_old_path(conn, spec):
    """v1 정책이 있는 요청은 #128 임시 예외 그대로 schemaVersion 1이고 리비전을 읽지 않는다."""
    result = succeeded(conn, {**spec, "modelKey": "iforest", "ruleVersionIds": [], "detectionPolicy": V1_POLICY})
    (config, stored_hash, _), models, _ = stored_config(conn, result["configVersionId"])
    assert config["schemaVersion"] == 1 and config["detectionPolicy"] == V1_POLICY and "methods" not in config
    assert stored_hash == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    assert "trainingPeriod" not in config["trainingSelection"] and "trainingPeriod" not in models[0][4]
    assert "activeModelSetRevision" not in result and "detectionPolicy" not in result
    request = {"kind": "evaluate", "sourceId": spec["sourceId"], "snapshotId": result["snapshotId"],
               "configVersionId": result["configVersionId"], "purpose": "validation"}
    evaluated = succeeded(conn, request, config=result["configVersionId"], snapshot=result["snapshotId"])
    assert "activeModelSetRevision" not in evaluated
    evaluation = conn.execute("SELECT evaluation_spec FROM run_evaluations WHERE id=%s", (evaluated["evaluationId"],)).fetchone()[0]
    conn.commit()
    assert evaluation["detectionPolicy"]["mode"] == "ml_only" and "detectionPolicyRevision" not in evaluation
    return result


def verify_fail_closed(conn, spec, version):
    copy = uuid.uuid4()
    conn.execute("""INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
                    SELECT %s,source_id,configuration_spec||'{"schemaVersion":4}'::jsonb,explanation_spec,content_hash,'fixture-admin',clock_timestamp()
                      FROM ml_config_versions WHERE id=%s""", (copy, version["configVersionId"]))
    conn.execute("""INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,
                        adapter_spec,training_settings,threshold_spec)
                    SELECT %s,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,training_settings,threshold_spec
                      FROM ml_config_models WHERE config_version_id=%s""", (copy, version["configVersionId"]))
    conn.commit()
    request = {"kind": "evaluate", "sourceId": spec["sourceId"], "snapshotId": version["snapshotId"],
               "configVersionId": str(copy), "purpose": "validation"}
    status, code, _ = finish(conn, submit(conn, request, config=copy, snapshot=version["snapshotId"]))
    assert (status, code) == ("failed", "unsupported_config_schema"), (status, code)
    status, code, _ = finish(conn, submit(conn, {**spec, "modelKey": "iforest", "excludedIntervals": []}))
    assert (status, code) == ("failed", "config_schema_not_supported_yet"), (status, code)


def main():
    dsn = os.environ["PIPELINE_TEST_DATABASE_URL"]
    parsed = urlsplit(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/qa_")
    with tempfile.TemporaryDirectory(prefix="powercode-model-version-") as directory:
        artifact_root = Path(directory).resolve()
        os.environ.update(DATABASE_URL=dsn, ARTIFACT_ROOT=str(artifact_root))
        with psycopg.connect(dsn) as conn:
            spec = seed(conn)
            first = verify_v3_train(conn, spec, artifact_root)
            second, revision = verify_active_set_operations(conn, spec, first)
            verify_v3_evaluate(conn, spec, second, revision)
            verify_old_path(conn, spec)
            verify_fail_closed(conn, spec, second)
    print("model version v3 integration passed: spec, hash, active set and policy revisions, v1 path, fail-closed")


if __name__ == "__main__":
    main()
