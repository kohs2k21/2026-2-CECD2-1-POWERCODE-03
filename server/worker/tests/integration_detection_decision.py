"""판정 구조 v2 실행 경로의 실제 PostgreSQL 시험. 합성 입력만 쓰고, 격리된 localhost QA DB에서만 돌린다.

실행: PIPELINE_TEST_DATABASE_URL=postgresql://...@127.0.0.1:<port>/qa_detection_decision
      python server/worker/tests/integration_detection_decision.py
이 시험은 운영 리비전 표에 행을 더하고 지우므로 다른 시험과 DB를 함께 쓰지 않는다.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration_research_pipeline import observe, source
from detector import attempt_v04, operational_state
from detector.computation import legacy_feature_hash
from detector.hard_rules import HARD_RULES, read_hard_rule_snapshot
from detector.jobs import claim_waiting_input, enqueue_observation
from detector.operational_state import rule_set_content_hash
from detector.runner import process_detection_message
from detector.status_rule import default_policy
from feature_fit import definitions_hash, fit_features, transform
from pg_queue import claim, enqueue
from trainer.catalog import register_feature_set
import model_adapters

REPO = Path(__file__).resolve().parents[3]
R1_HASH = "de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0"
DEFINITIONS = [{"name": "count_total", "expression": {"op": "field", "field": "P.total_count"}},
               {"name": "count_error", "expression": {"op": "field", "field": "P.error_count"}}]
FIT_DEFINITIONS = [{"name": "count_raw", "expression": {"op": "field", "field": "P.total_count"}},
                   {"name": "count_centered", "expression": {"op": "group_centered", "groupBy": "P.adapter_type",
                                                             "value": {"op": "field", "field": "P.total_count"}}}]
RAW = {"status": "S", "start_time_raw": "2026-01-01 00:00:00", "end_time_raw": "2026-01-01 00:00:01",
       "total_count": "10", "success_count": "10", "error_count": "0", "retry_count": "0"}
ELAPSED_SOFT = {"id": "soft:elapsed_ms-3f9a2c1d", "name": "처리 시간", "field": "elapsed_ms", "operator": "gt",
                "threshold": 5000, "unit": "ms", "severity": "warning", "enabled": True}
RETRY_SOFT = {"id": "soft:retry_count-91ab77e0", "name": "재시도", "field": "retry_count", "operator": "gte",
              "threshold": 3, "unit": "count", "severity": "info", "enabled": True}
ROOT: Path


def hard_rules(**enabled):
    """내장 4개 전체. enabled에 false로 준 규칙만 끈다(키는 - 대신 _)."""
    return [{**rule, "enabled": enabled.get(rule["id"].split(":")[1].replace("-", "_"), True)} for rule in HARD_RULES]


@contextlib.contextmanager
def contract(value):
    with patch.dict(os.environ, {"DETECT_JOB_CONTRACT": value}):
        yield


def save_rule_set(conn, *, hard=None, soft=(), version=2, content_hash=None):
    snapshot = {"hardRules": hard if hard is not None else hard_rules(), "softRules": list(soft)}
    revision = conn.execute("SELECT max(revision)+1 FROM ops_rule_set_revisions").fetchone()[0]
    conn.execute("""INSERT INTO ops_rule_set_revisions(revision,content_hash,content_hash_version,snapshot,changes,summary,
        saved_by_actor_id) VALUES(%s,%s,%s,%s::jsonb,'[]','{}','integration')""",
        (revision, content_hash or rule_set_content_hash(snapshot, version), version, json.dumps(snapshot)))
    conn.commit()
    return revision


def save_policy(conn, policy):
    revision = conn.execute("SELECT COALESCE(max(revision),0)+1 FROM ops_detection_policy_revisions").fetchone()[0]
    conn.execute("""INSERT INTO ops_detection_policy_revisions(revision,policy,saved_by_actor_id)
        VALUES(%s,%s::jsonb,'integration')""", (revision, json.dumps(policy)))
    conn.commit()
    return revision


def policy(deadline=3000, **k):
    return {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1, **k},
            "deadlineMs": deadline}


def save_models(conn, models):
    revision = conn.execute("SELECT max(revision)+1 FROM ops_active_model_set_revisions").fetchone()[0]
    conn.execute("""INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id)
        VALUES(%s,%s::jsonb,'[]','integration')""", (revision, json.dumps({"models": models})))
    conn.commit()
    return revision


def active(config_id, key="iforest"):
    return {"configVersionId": str(config_id), "modelKey": key, "displayName": key, "activatedAtRevision": 1}


def config(conn, sid, spec):
    identifier = uuid.uuid4()
    conn.execute("""INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,
        created_by_actor_id,created_at) VALUES(%s,%s,%s::jsonb,'{}',%s,'integration',clock_timestamp())""",
        (identifier, sid, json.dumps(spec), hashlib.sha256(str(identifier).encode()).hexdigest()))
    conn.commit()
    return identifier


def artifact(conn, kind, path, feature_set, fit=None, *, runtime):
    identifier = uuid.uuid4()
    relative = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    data = Path(path).read_bytes()
    conn.execute("""INSERT INTO ml_artifacts(id,kind,relative_path,content_hash,byte_size,feature_set_id,fit_artifact_id,
        format_version,runtime_version,metadata,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}',clock_timestamp())""",
        (identifier, kind, relative, hashlib.sha256(data).hexdigest(), len(data), feature_set, fit,
         runtime["formatVersion"], runtime["runtimeVersion"]))
    return identifier


def trained_model(conn, sid):
    """실제 IF 산출물. 입력은 합성 건수다."""
    feature_set = register_feature_set(conn, DEFINITIONS, "integration", definition_kind="operator_defined")
    rows = [{"features": {"count_total": float(10 + index % 40), "count_error": float(index % 3)}} for index in range(1000)]
    trained = model_adapters.train_iforest(rows, [item["name"] for item in DEFINITIONS], str(ROOT / str(uuid.uuid4())))
    identifier = config(conn, sid, {"schemaVersion": 3, "featureSetId": str(feature_set)})
    fit = artifact(conn, "fit", trained["fitPath"], feature_set, runtime=trained)
    model = artifact(conn, "model", trained["modelPath"], feature_set, fit, runtime=trained)
    conn.execute("""INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,
        adapter_spec,training_settings,threshold_spec) VALUES(%s,'iforest',%s,%s,%s,'{}',%s::jsonb,%s::jsonb)""",
        (identifier, feature_set, model, fit,
         json.dumps({"featureNames": [item["name"] for item in DEFINITIONS], "thresholdPolicy": "train-internal-p99-v1"}),
         json.dumps(trained["thresholdSpec"])))
    conn.commit()
    return identifier, feature_set


def fitted_model(conn, sid):
    """fit 상태를 쓰는 모델. 변수 캐시 키에 모델 식별이 없어 이 값은 저장하면 안 된다."""
    feature_set = register_feature_set(conn, FIT_DEFINITIONS, "integration", definition_kind="operator_defined")
    raws = [{"total_count": str(10 + index % 40), "adapter_type": "HTTP"} for index in range(1000)]
    state = fit_features(FIT_DEFINITIONS, [{"raw": raw} for raw in raws], snapshot_id=uuid.uuid4())
    rows = [{"features": transform(FIT_DEFINITIONS, raw, state)[0]} for raw in raws]
    names = [item["name"] for item in FIT_DEFINITIONS]
    trained = model_adapters.train_iforest(rows, names, str(ROOT / str(uuid.uuid4())), preprocessing=state)
    identifier = config(conn, sid, {"schemaVersion": 3, "featureSetId": str(feature_set)})
    fit = artifact(conn, "fit", trained["fitPath"], feature_set, runtime=trained)
    model = artifact(conn, "model", trained["modelPath"], feature_set, fit, runtime=trained)
    conn.execute("""INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,
        adapter_spec,training_settings,threshold_spec) VALUES(%s,'iforest',%s,%s,%s,'{}',%s::jsonb,%s::jsonb)""",
        (identifier, feature_set, model, fit,
         json.dumps({"featureNames": names, "thresholdPolicy": "train-internal-p99-v1",
                     "definitionsHash": definitions_hash(FIT_DEFINITIONS)}),
         json.dumps(trained["thresholdSpec"])))
    conn.commit()
    return identifier, feature_set


def v04_job(conn, sid, key, raw=None):
    seen = observe(conn, sid, f"{key}-{uuid.uuid4()}", {**RAW, **(raw or {})})
    with contract("v04"):
        payload = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    return seen, payload


def attempt(conn, payload):
    """Detector 실행 고리와 같이 임대·처리한다. 실패는 예외 값으로 돌려준다."""
    identifier = uuid.UUID(payload["jobId"])
    conn.execute("UPDATE powercode_queue.detection_logs SET available_at=clock_timestamp() WHERE id=%s", (identifier,))
    conn.commit()
    message = claim(conn, "detection", message_id=identifier)
    conn.commit()
    assert message is not None, "message_not_claimable"
    try:
        process_detection_message(conn, message, ROOT)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return exc
    return None


def judged(conn, payload):
    failure = attempt(conn, payload)
    assert failure is None, repr(failure)
    return decision(conn, payload)


def decision(conn, payload):
    row = conn.execute("""SELECT status::text,rule_status::text,ml_status::text,decision_code,anomaly_score,evidence,
        config_version_id,latency_status::text FROM run_process_decisions WHERE job_id=%s""", (payload["jobId"],)).fetchone()
    conn.commit()
    return row


def job_state(conn, payload):
    row = conn.execute("""SELECT contract_version,status::text,input_config_version_id,attempt_count,error_code
        FROM run_jobs WHERE id=%s""", (payload["jobId"],)).fetchone()
    conn.commit()
    return row


def interims(conn, payload):
    rows = conn.execute("""SELECT attempt_id,kind,evidence_version,payload,superseded_by_attempt_id,reason_code
        FROM run_decision_interims WHERE job_id=%s ORDER BY created_at,seq""", (payload["jobId"],)).fetchall()
    conn.commit()
    return rows


def wait_until(predicate, message, seconds=10):
    limit = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < limit, message
        time.sleep(.01)


def other_connection():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def interim_count(payload):
    with other_connection() as other:
        return other.execute("SELECT count(*) FROM run_decision_interims WHERE job_id=%s",
                             (payload["jobId"],)).fetchone()[0]


def after_interims(payload, count, run):
    """고정 대기 대신 앞선 기록이 DB에 남은 뒤에 방법을 끝내 도착 순서를 강제한다."""
    def wrapped(*args):
        wait_until(lambda: interim_count(payload) >= count, f"interim {count} not written")
        return run(*args)
    return wrapped


def materialized(conn, payload):
    count = conn.execute("""SELECT count(*) FROM data_feature_materializations m
        JOIN run_process_decisions d ON d.process_observation_id=m.observation_id WHERE d.job_id=%s""",
        (payload["jobId"],)).fetchone()[0]
    conn.commit()
    return count


def method(evidence, method_id):
    return next(item for item in evidence["methodResults"] if item["methodId"] == method_id)


def assert_fail_closed(conn, sid, reason, key):
    """판정 행 없이 시도가 실패하고, 빈 판정이나 정상으로 대신하지 않는다."""
    _, payload = v04_job(conn, sid, key)
    failure = attempt(conn, payload)
    assert isinstance(failure, ValueError) and str(failure).startswith(reason), repr(failure)
    assert decision(conn, payload) is None, key
    state = job_state(conn, payload)
    assert state[1] == "queued" and state[3] == 1 and state[4].startswith(reason), state
    # 원인 코드는 작업·시도·큐 어디서 봐도 같다.
    stored = conn.execute("""SELECT a.error_code,q.last_error FROM run_job_attempts a
        JOIN powercode_queue.detection_logs q ON q.id=a.job_id WHERE a.job_id=%s""", (payload["jobId"],)).fetchone()
    conn.commit()
    assert stored[0].startswith(reason) and stored[1].startswith(reason), stored
    return payload


def pinning_and_fail_closed(conn, sid):
    _, payload = v04_job(conn, sid, "pinned-default")
    assert "configVersionId" not in payload, payload
    assert job_state(conn, payload)[:3] == ("v04", "queued", None)
    row = judged(conn, payload)
    evidence = row[5]
    assert evidence["ruleSetRevision"] == 1 and evidence["ruleSetHash"] == R1_HASH, evidence
    assert evidence["detectionPolicyRevision"] == 0 and evidence["activeModelSetRevision"] == 0
    assert evidence["detectionPolicy"] == policy(), evidence["detectionPolicy"]
    print("PASS (a) 시도 시작 고정: r1·기본 정책(리비전 0)·활성 집합 r0, 모델 없이 룰만 판정")

    # 시도 도중 저장은 그 시도에 영향이 없고, 재시도는 새로 읽는다.
    real_execute = attempt_v04.execute_methods
    saved = []

    def save_during_attempt(*args, **kwargs):
        if not saved:
            with psycopg.connect(os.environ["DATABASE_URL"]) as other:
                saved.append(save_rule_set(other, hard=hard_rules(process_status=False)))
        return real_execute(*args, **kwargs)

    _, payload = v04_job(conn, sid, "pinned-during", {"status": "F", "total_count": "10", "success_count": "9",
                                                      "error_count": "1"})
    with patch.object(attempt_v04, "execute_methods", save_during_attempt):
        row = judged(conn, payload)
    assert row[5]["ruleSetRevision"] == 1 and method(row[5], "hard:process-status")["anomalous"] is True, row[5]
    _, payload = v04_job(conn, sid, "pinned-next", {"status": "F", "total_count": "10", "success_count": "9",
                                                    "error_count": "1"})
    row = judged(conn, payload)
    assert row[5]["ruleSetRevision"] == saved[0] and row[5]["checkers"]["hr"]["status"] == "skipped", row[5]["checkers"]
    assert row[5]["statusRulePolicy"] is None and row[0] == "complete" and row[5]["anomalous"] is False, row[5]
    good = save_rule_set(conn)

    real_build = attempt_v04.build_evidence

    def fail_once(*args, **kwargs):
        patch_state["calls"] += 1
        if patch_state["calls"] == 1:
            with psycopg.connect(os.environ["DATABASE_URL"]) as other:
                patch_state["revision"] = save_rule_set(other, hard=hard_rules(negative_duration=False))
            raise ValueError("simulated_attempt_failure")
        return real_build(*args, **kwargs)

    patch_state = {"calls": 0}
    _, payload = v04_job(conn, sid, "pinned-retry")
    with patch.object(attempt_v04, "build_evidence", fail_once):
        first = attempt(conn, payload)
        assert str(first) == "simulated_attempt_failure" and decision(conn, payload) is None
        row = judged(conn, payload)
    assert row[5]["ruleSetRevision"] == patch_state["revision"] > good, row[5]["ruleSetRevision"]
    assert "hard:negative-duration" not in [item["methodId"] for item in row[5]["methodResults"]]
    save_rule_set(conn)
    print("PASS (a) 시도 도중 저장은 그 시도에 영향 없음, 다음 작업·재시도는 새 리비전을 읽음")

    # 같은 객체: 판정과 evidence 기록이 읽기 1회의 결과를 함께 쓴다.
    seen = {"reads": [], "evaluated": [], "recorded": []}
    real_read, real_runners = operational_state.read_hard_rule_snapshot, attempt_v04.hard_rule_runners

    def read(rules):
        seen["reads"].append(real_read(rules))
        return seen["reads"][-1]

    def runners(selection, raw):
        seen["evaluated"].append(selection)
        return real_runners(selection, raw)

    def build(policy_value, methods, revisions, selection, **kwargs):
        seen["recorded"].append(selection)
        return real_build(policy_value, methods, revisions, selection, **kwargs)

    _, payload = v04_job(conn, sid, "same-selection", {"status": "H"})
    with patch.object(operational_state, "read_hard_rule_snapshot", read), \
            patch.object(attempt_v04, "hard_rule_runners", runners), patch.object(attempt_v04, "build_evidence", build):
        judged(conn, payload)
    assert len(seen["reads"]) == len(seen["evaluated"]) == len(seen["recorded"]) == 1, seen
    assert seen["reads"][0] is seen["evaluated"][0] is seen["recorded"][0]
    print("PASS (a) HardRuleSelection은 시도당 1회 읽고 판정·build_evidence가 같은 객체를 씀")

    # 저장 검증을 거치지 않은 비정상 상태. 뒤에 정상 리비전을 저장해 복구한다.
    save_rule_set(conn, content_hash="0" * 64)
    assert_fail_closed(conn, sid, "rule_set_hash_mismatch", "hash-mismatch")
    save_rule_set(conn, version=1, content_hash=rule_set_content_hash({"hardRules": hard_rules(), "softRules": []}, 2))
    assert_fail_closed(conn, sid, "rule_set_hash_mismatch", "hash-version")
    save_rule_set(conn, hard=[*hard_rules(), {"id": "hard:unknown-rule", "enabled": True, "severity": "critical"}])
    assert_fail_closed(conn, sid, "hard_rule_unknown", "unknown-hard")
    save_rule_set(conn, soft=[{**ELAPSED_SOFT, "id": "sr:r00-3f9a2c1d"}])
    assert_fail_closed(conn, sid, "method_kind_not_allowed", "sr-method")
    save_rule_set(conn, hard=hard_rules(process_status=False, negative_duration=False, success_with_error=False,
                                        count_conservation=False))
    assert_fail_closed(conn, sid, "detection_methods_empty", "no-methods")
    save_rule_set(conn)
    save_policy(conn, policy(hard=9))
    assert_fail_closed(conn, sid, "combination_k_out_of_range:hard", "k-above-n")
    save_policy(conn, policy(hard=0))
    assert_fail_closed(conn, sid, "combination_k_out_of_range:hard", "k-below-one")
    save_policy(conn, {"mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly"})
    assert_fail_closed(conn, sid, "invalid_detection_policy", "v1-policy")
    save_policy(conn, {**policy(), "policyKey": "unknown-policy"})
    assert_fail_closed(conn, sid, "unsupported_detection_policy", "unknown-policy")
    save_policy(conn, policy())

    # 뷰 0행: 불변 트리거를 이 연결에서만 건너뛰고 활성 집합을 비웠다가 복구한다.
    stored = conn.execute("SELECT revision,snapshot,changes,changed_by_actor_id FROM ops_active_model_set_revisions").fetchall()
    conn.execute("SET session_replication_role=replica")
    conn.execute("DELETE FROM ops_active_model_set_revisions")
    conn.execute("SET session_replication_role=DEFAULT")
    conn.commit()
    payload = assert_fail_closed(conn, sid, "operational_state_unavailable", "view-empty")
    conn.execute("SET session_replication_role=replica")
    for revision, snapshot, changes, actor in stored:
        conn.execute("""INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id)
            VALUES(%s,%s::jsonb,%s::jsonb,%s)""", (revision, json.dumps(snapshot), json.dumps(changes), actor))
    conn.execute("SET session_replication_role=DEFAULT")
    conn.commit()
    row = judged(conn, payload)
    assert row[0] == "complete" and job_state(conn, payload)[1:4:2] == ("succeeded", 2), job_state(conn, payload)
    print("PASS (a) fail-closed 10종(hash·hash 버전·미지 규칙·sr·방법 0개·k 2종·정책 2종·뷰 0행): 판정 행 없음, 원인 코드 기록, 복구 뒤 재시도 판정")

    _, payload = v04_job(conn, sid, "all-attempts-fail")
    save_rule_set(conn, content_hash="1" * 64)
    for number in (1, 2, 3):
        failure = attempt(conn, payload)
        assert str(failure) == "rule_set_hash_mismatch", failure
    state = job_state(conn, payload)
    assert state[1] == "failed" and state[3] == 3 and decision(conn, payload) is None, state
    assert state[4] == "rule_set_hash_mismatch", state
    save_rule_set(conn)
    print("PASS (a) 세 시도 모두 실패하면 작업이 판정 없이 failed")


def evidence_and_columns(conn, sid, model_config, feature_set):
    revision = save_rule_set(conn, soft=[ELAPSED_SOFT, RETRY_SOFT])
    model_revision = save_models(conn, [active(model_config)])

    # 소프트룰만 이상. 건수가 학습 범위 안이라 IF는 정상이다.
    seen, payload = v04_job(conn, sid, "soft-only", {"end_time_raw": "2026-01-01 00:00:08", "total_count": "20",
                                                     "success_count": "20"})
    row = judged(conn, payload)
    status, rule_status, ml_status, code, score, evidence, config_id, latency = row
    assert (status, rule_status, ml_status) == ("complete", "succeeded", "succeeded"), row[:3]
    assert config_id is None and latency == "unavailable"
    assert evidence["schemaVersion"] == 4 and evidence["ruleSetRevision"] == revision
    assert evidence["activeModelSetRevision"] == model_revision
    assert [item["methodId"] for item in evidence["methodResults"]] == [
        "hard:process-status", "hard:negative-duration", "hard:success-with-error", "hard:count-conservation",
        ELAPSED_SOFT["id"], RETRY_SOFT["id"], "ml:iforest"], evidence["methodResults"]
    soft = method(evidence, ELAPSED_SOFT["id"])
    assert soft["anomalous"] is True and soft["riskLevel"] == "warning" and soft["observedValue"] == "8000", soft
    assert soft["condition"] == {"field": "elapsed_ms", "operator": "gt", "threshold": "5000", "unit": "ms"}, soft
    assert code == soft["code"] == "SOFT_ELAPSED_MS_3F9A2C1D", code
    model = method(evidence, "ml:iforest")
    assert model["status"] == "succeeded" and model["methodVersion"] == "process-iforest-v1", model
    assert model["modelVersionId"] == str(model_config) and type(model["score"]) is float, model
    assert model["scoreDetail"]["thresholdPolicy"] == "train-internal-p99-v1", model
    assert model["scoreDetail"]["raw"] == model["score"] and abs(float(score) - model["score"]) < 1e-9, (score, model)
    assert model["anomalous"] is False and model["riskLevel"] is None, model
    composite = evidence["composite"]
    assert (composite["status"], composite["anomalous"], composite["grade"]) == ("complete", True, "anomaly_sign"), composite
    assert composite["risk"] == {"policy": "risk-max-v1", "levelTable": "method-defined-v2", "status": "rated",
                                 "level": "warning", "determinedBy": [ELAPSED_SOFT["id"]], "unratedMethods": []}, composite
    assert composite["policy"] == {key: value for key, value in evidence["detectionPolicy"].items() if key != "deadlineMs"}
    assert all(type(item["elapsedMs"]) in (int, float) for item in evidence["methodResults"])
    assert composite["firstAnomalyElapsedMs"] == soft["elapsedMs"], composite
    assert evidence["categoryResults"]["hard"] == {"n": 4, "k": 1, "result": "normal", "status": "succeeded",
                                                   "anomalous": False}, evidence["categoryResults"]
    assert evidence["checkers"]["sr"] == {"status": "skipped", "anomalous": None, "evidence": []}
    assert evidence["requestedMode"] == "parallel" and evidence["actualPaths"] == ["rule", "ml"]
    assert evidence["outcomes"] == {"classificationVersion": "detector-outcomes-v1"}
    assert evidence["observation"]["id"] == str(seen.observation_id) and evidence["telemetry"]["status"] == "measured"
    assert evidence["telemetry"]["metrics"]["inferenceMs"]["value"] is not None, evidence["telemetry"]["metrics"]
    assert conn.execute("""SELECT count(*) FROM ops_pipeline_measurements WHERE observation_id=%s AND stage='detector'
        AND config_version_id IS NULL""", (seen.observation_id,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM data_feature_materializations WHERE observation_id=%s AND feature_set_id=%s",
                        (seen.observation_id, feature_set)).fetchone()[0] == 1
    assert conn.execute("""SELECT count(*) FROM run_decision_events e JOIN run_process_decisions d ON d.id=e.decision_id
        WHERE d.job_id=%s""", (payload["jobId"],)).fetchone()[0] == 1
    summary = conn.execute("SELECT result_summary FROM run_jobs WHERE id=%s", (payload["jobId"],)).fetchone()[0]
    assert summary["status"] == "complete" and summary["checkers"] == evidence["checkers"], summary
    conn.commit()
    print("PASS (b) evidence v4 전체·판정 열·이상 징후·위험도 주의·실제 IF 점수·측정 행 저장")

    # 하드룰 이상 + 소프트 이상: 등급은 이상, 위험도는 최고값, 코드는 상태 규칙이 우선이다.
    _, payload = v04_job(conn, sid, "hard-grade", {"status": "F", "end_time_raw": "2026-01-01 00:00:09",
                                                   "total_count": "20", "success_count": "19", "error_count": "1"})
    row = judged(conn, payload)
    composite = row[5]["composite"]
    assert composite["grade"] == "anomaly" and composite["risk"]["level"] == "critical", composite
    assert row[3] == "PROCESS_STATUS_F", row[3]
    assert composite["basisMethods"] == ["hard:process-status", ELAPSED_SOFT["id"]], composite

    # 상태 규칙의 코드별 단계는 고정한 룰 세트 값이 evidence·위험도에 그대로 쓰인다.
    custom = {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_Z": "warning",
              "PROCESS_STATUS_H": "critical", "PROCESS_STATUS_N": "info"}
    rules = hard_rules()
    rules[0] = {**rules[0], "severity": {"byCode": custom}}
    save_rule_set(conn, hard=rules)
    _, payload = v04_job(conn, sid, "custom-level", {"status": "H"})
    row = judged(conn, payload)
    assert row[5]["composite"]["risk"]["level"] == "critical" and row[3] == "PROCESS_STATUS_H", row[5]["composite"]
    assert row[5]["statusRulePolicy"]["mapping"]["H"] == "critical" and row[5]["statusRulePolicy"]["version"] == "process-status-hr-v2"
    save_rule_set(conn, soft=[ELAPSED_SOFT, RETRY_SOFT])

    # 입력 없음·정의 밖 값: 정상으로 바꾸지 않는다.
    _, payload = v04_job(conn, sid, "undetermined", {"status": "Q"})
    row = judged(conn, payload)
    assert row[0] == "partial" and row[5]["anomalous"] is None and row[5]["composite"]["grade"] == "undetermined", row[5]["composite"]
    assert row[5]["composite"]["risk"]["status"] == "not_applicable" and row[3] is None
    assert method(row[5], "hard:process-status")["cause"] == "input_invalid"

    # 한 소프트룰 비정상이 다른 방법 결과를 지우지 않는다(행동 변경 2). 숫자가 아닌 값은 재시도 소프트룰만 failed다.
    _, payload = v04_job(conn, sid, "soft-isolated", {"status": "F", "retry_count": "많음", "total_count": "20",
                                                      "success_count": "19", "error_count": "1"})
    row = judged(conn, payload)
    assert row[0] == "partial" and row[1] == "failed" and row[5]["anomalous"] is True, row[:3]
    assert method(row[5], RETRY_SOFT["id"])["cause"] == "input_invalid", row[5]["methodResults"]
    assert method(row[5], "hard:process-status")["anomalous"] is True
    print("PASS (b) 이상 등급·코드 순서, 코드별 단계, 미판정 보존, 소프트룰 오류 격리")

    # 산출물 변조·다른 source 모델은 그 모델만 미판정이다.
    other_source = source(conn, "다른 원천")
    _, payload = v04_job(conn, other_source, "model-other-source")
    row = judged(conn, payload)
    model = method(row[5], "ml:iforest")
    assert (model["status"], model["cause"]) == ("not_ready", "not_configured"), model
    assert model["evidence"] == [{"reason": "config_source_mismatch"}] and row[0] == "partial" and row[2] == "not_ready"
    assert row[5]["anomalous"] is None, "모델 미판정인데 정상이 되면 안 된다"
    print("PASS (b) 다른 source 모델은 not_configured와 사유를 남기고 정상이 되지 않음")

    # 산출물 파일이 없으면 준비 안 됨, 있는데 내용이 다르면 실패다.
    path = conn.execute("""SELECT a.relative_path FROM ml_config_models c JOIN ml_artifacts a ON a.id=c.model_artifact_id
        WHERE c.config_version_id=%s""", (model_config,)).fetchone()[0]
    conn.commit()
    artifact_file = ROOT / path
    original = artifact_file.read_bytes()
    artifact_file.write_bytes(original + b"tampered")
    _, payload = v04_job(conn, sid, "artifact-tampered")
    row = judged(conn, payload)
    model = method(row[5], "ml:iforest")
    assert (model["status"], model["cause"], model["failureCode"]) == ("failed", "failure", "artifact_invalid"), model
    assert row[0] == "partial" and row[2] == "failed" and row[5]["anomalous"] is None, row[:3]
    artifact_file.unlink()
    _, payload = v04_job(conn, sid, "artifact-missing")
    row = judged(conn, payload)
    model = method(row[5], "ml:iforest")
    assert (model["status"], model["cause"], model["failureCode"]) == ("not_ready", "not_configured", None), model
    assert row[2] == "not_ready" and row[5]["anomalous"] is None, row[:3]
    artifact_file.write_bytes(original)
    _, payload = v04_job(conn, sid, "artifact-restored")
    assert method(judged(conn, payload)[5], "ml:iforest")["status"] == "succeeded"
    print("PASS (b) 산출물 변조는 failed·artifact_invalid, 파일 없음은 not_ready·not_configured, 복구 뒤 정상 실행")

    save_models(conn, [active(model_config), active(uuid.uuid4(), "minibatch_kmeans")])
    _, payload = v04_job(conn, sid, "model-not-executable")
    row = judged(conn, payload)
    assert method(row[5], "ml:minibatch_kmeans")["cause"] == "not_executable", row[5]["methodResults"]
    assert row[4] is None, "활성 ML이 2개면 anomaly_score는 null이다"
    save_models(conn, [])
    print("PASS (b) 실행 불가 모델 분리, 활성 ML 2개면 점수 열 null")

    fit_config, fit_set = fitted_model(conn, sid)
    save_models(conn, [active(fit_config)])
    seen, payload = v04_job(conn, sid, "fitted-features", {"adapter_type": "HTTP"})
    row = judged(conn, payload)
    assert method(row[5], "ml:iforest")["status"] == "succeeded", row[5]["methodResults"]
    assert conn.execute("SELECT count(*) FROM data_feature_materializations WHERE observation_id=%s AND feature_set_id=%s",
                        (seen.observation_id, fit_set)).fetchone()[0] == 0
    conn.commit()
    save_models(conn, [])
    print("PASS (b) fit을 쓰는 모델의 변수값은 저장하지 않음")
    return revision


def interim_records(conn, sid, model_config):
    save_rule_set(conn, soft=[ELAPSED_SOFT])
    save_models(conn, [active(model_config)])
    real_soft, real_score = attempt_v04.evaluate_soft_rule, model_adapters.score_iforest

    # 상태 F가 먼저, 소프트 T가 뒤에 오고 ML이 가장 늦다.
    raw = {"status": "F", "end_time_raw": "2026-01-01 00:00:09", "total_count": "20", "success_count": "19", "error_count": "1"}
    _, payload = v04_job(conn, sid, "interim-order", raw)
    with patch.object(attempt_v04, "evaluate_soft_rule", after_interims(payload, 1, real_soft)), \
            patch.object(model_adapters, "score_iforest", after_interims(payload, 2, real_score)):
        row = judged(conn, payload)
    records = interims(conn, payload)
    assert [item[1] for item in records] == ["first_anomaly", "evidence_added"], records
    first, added = records[0][3], records[1][3]
    assert records[0][2] == records[1][2] == "detector-evidence-v4"
    assert first["schemaVersion"] == 4 and first["composite"]["basisMethods"] == ["hard:process-status"], first["composite"]
    assert first["composite"]["grade"] == "anomaly" and first["composite"]["status"] == "partial", first["composite"]
    assert first["ruleSetRevision"] == row[5]["ruleSetRevision"] and first["ruleSetHash"] == row[5]["ruleSetHash"]
    assert first["detectionPolicy"] == row[5]["detectionPolicy"] and first["activeModelSetRevision"] == row[5]["activeModelSetRevision"]
    pending = [item for item in first["methodResults"] if item["cause"] == "pending"]
    assert {item["methodId"] for item in pending} == {ELAPSED_SOFT["id"], "ml:iforest"}, first["methodResults"]
    assert all(item["elapsedMs"] is None for item in pending)
    assert added["composite"]["basisMethods"] == ["hard:process-status", ELAPSED_SOFT["id"]], added["composite"]
    assert [item["methodId"] for item in added["methodResults"] if item["cause"] == "pending"] == ["ml:iforest"]
    assert first["elapsedMs"] < added["elapsedMs"], (first["elapsedMs"], added["elapsedMs"])
    assert row[5]["composite"]["basisMethods"] == added["composite"]["basisMethods"] or "ml:iforest" in row[5]["composite"]["basisMethods"]
    assert "cause" not in row[5] and all(item["cause"] != "pending" for item in row[5]["methodResults"])
    events = conn.execute("""SELECT event_kind FROM run_decision_events e JOIN run_decision_interims i ON i.id=e.interim_id
        WHERE i.job_id=%s ORDER BY e.id""", (payload["jobId"],)).fetchall()
    assert [item[0] for item in events] == ["first_anomaly", "evidence_added"], events
    conn.commit()
    print("PASS (c) 첫 이상·근거 추가 기록, 전체 스냅샷·미도착 pending·같은 리비전, 이벤트 순서")

    # 근거가 그대로면 결과가 더 도착해도 새로 쓰지 않는다. 호출 횟수를 세어 두 번째 도착을 기다린다.
    calls = []
    real_call = attempt_v04.InterimRecorder.__call__

    def counted(recorder, methods):
        real_call(recorder, methods)
        calls.append(1)

    def score_after_second_arrival(*args):
        wait_until(lambda: len(calls) >= 2, "second arrival did not reach the recorder")
        return real_score(*args)

    _, payload = v04_job(conn, sid, "interim-same-basis", {**raw, "end_time_raw": "2026-01-01 00:00:01"})
    with patch.object(attempt_v04, "evaluate_soft_rule", after_interims(payload, 1, real_soft)), \
            patch.object(attempt_v04.InterimRecorder, "__call__", counted), \
            patch.object(model_adapters, "score_iforest", score_after_second_arrival):
        row = judged(conn, payload)
    assert [item[1] for item in interims(conn, payload)] == ["first_anomaly"] and len(calls) == 2, calls
    assert method(row[5], ELAPSED_SOFT["id"])["anomalous"] is False, row[5]["methodResults"]
    print("PASS (c) 근거가 같으면 결과가 더 도착해도 기록하지 않음")

    # 이상이 없으면 최종까지 기록이 없다.
    _, payload = v04_job(conn, sid, "interim-none", {"total_count": "20", "success_count": "20"})
    row = judged(conn, payload)
    assert interims(conn, payload) == [] and row[5]["anomalous"] is False, row[5]["composite"]

    # 재시도: 이전 시도 기록은 새 시도가 닫는다.
    real_build = attempt_v04.build_evidence
    calls = []

    def fail_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("simulated_attempt_failure")
        return real_build(*args, **kwargs)

    _, payload = v04_job(conn, sid, "interim-superseded", raw)
    with patch.object(attempt_v04, "build_evidence", fail_first), \
            patch.object(model_adapters, "score_iforest", after_interims(payload, 1, real_score)):
        assert str(attempt(conn, payload)) == "simulated_attempt_failure"
        before = interims(conn, payload)
        assert before and before[0][1] == "first_anomaly"
        row = judged(conn, payload)
    kinds = [item[1] for item in interims(conn, payload)]
    assert kinds.count("superseded") == 1 and row[5]["anomalous"] is True, kinds
    closed = next(item for item in interims(conn, payload) if item[1] == "superseded")
    assert closed[0] == before[0][0] and closed[4] != closed[0], closed

    # 모든 시도 실패: 열린 기록은 최종 없음으로 닫힌다.
    def always_fail(*_args, **_kwargs):
        raise ValueError("simulated_attempt_failure")

    _, payload = v04_job(conn, sid, "interim-no-final", raw)
    with patch.object(attempt_v04, "build_evidence", always_fail), \
            patch.object(model_adapters, "score_iforest", after_interims(payload, 1, real_score)):
        for _ in range(3):
            assert str(attempt(conn, payload)) == "simulated_attempt_failure"
    records = interims(conn, payload)
    assert job_state(conn, payload)[1] == "failed" and decision(conn, payload) is None
    assert "no_final" in [item[1] for item in records], [item[1] for item in records]
    assert [item[5] for item in records if item[1] == "no_final"] == ["simulated_attempt_failure"], records
    print("PASS (c) 이상 없으면 기록 없음, 재시도 대체됨, 전 시도 실패 최종 없음")

    # 기한: 측정값 기록, 기한 뒤 결과 버림, 기한 뒤 중간 기록 없음.
    revision = save_policy(conn, policy(deadline=300))
    released, finished = threading.Event(), threading.Event()
    real_run_model, real_telemetry = attempt_v04._run_model, attempt_v04._telemetry

    def stuck_score(*args):
        released.wait(10)
        return real_score(*args)

    def run_model(*args):
        try:
            return real_run_model(*args)
        finally:
            finished.set()

    def telemetry_after_late_model(*args, **kwargs):
        # 메인 스레드는 이미 ML을 timeout으로 정했다. 그 뒤에 모델 스레드가 끝나도 그 값은 쓰이면 안 된다.
        released.set()
        assert finished.wait(10), "late model did not finish"
        time.sleep(.02)
        return real_telemetry(*args, **kwargs)

    _, payload = v04_job(conn, sid, "deadline", raw)
    with patch.object(model_adapters, "score_iforest", stuck_score), patch.object(attempt_v04, "_run_model", run_model), \
            patch.object(attempt_v04, "_telemetry", telemetry_after_late_model):
        row = judged(conn, payload)
    model = method(row[5], "ml:iforest")
    assert (model["status"], model["cause"], model["anomalous"]) == ("not_ready", "timeout", None), model
    assert 300 <= model["elapsedMs"] < 3000, model["elapsedMs"]
    assert row[0] == "partial" and row[2] == "not_ready" and row[5]["anomalous"] is True, row[:3]
    assert row[5]["detectionPolicyRevision"] == revision and row[5]["detectionPolicy"]["deadlineMs"] == 300
    assert row[5]["composite"]["risk"]["level"] == "critical"
    assert materialized(conn, payload) == 0, "기한을 넘긴 모델의 값은 저장하지 않는다"
    assert row[5]["telemetry"]["metrics"]["inferenceMs"]["value"] is None, row[5]["telemetry"]["metrics"]["inferenceMs"]

    # 결과는 기한 안에 도착했지만 메인 스레드가 늦으면 중간 기록을 쓰지 않는다.
    real_decide = attempt_v04.decide
    stuck = threading.Event()

    def late_decide(policy_value, methods):
        time.sleep(policy_value.deadline_ms / 1000 + .05)
        return real_decide(policy_value, methods)

    def blocked_score(*args):
        stuck.wait(10)
        return real_score(*args)

    _, payload = v04_job(conn, sid, "interim-after-deadline", raw)
    with patch.object(attempt_v04, "decide", late_decide), patch.object(model_adapters, "score_iforest", blocked_score):
        row = judged(conn, payload)
    stuck.set()
    assert interims(conn, payload) == [] and row[5]["anomalous"] is True, interims(conn, payload)
    save_policy(conn, policy())
    save_models(conn, [])
    save_rule_set(conn)
    print("PASS (c) 기한 초과 elapsedMs 측정값, 늦은 모델의 변수·측정값 저장 없음, 기한 뒤 중간 기록 없음, 확인된 이상 유지")


def lease_loss(conn, sid):
    """소유권을 잃은 시도는 판정도 중간 기록도 남기지 않는다."""
    save_rule_set(conn)
    raw = {"status": "F", "total_count": "10", "success_count": "9", "error_count": "1"}
    save_rule_set(conn, soft=[ELAPSED_SOFT])
    real_soft = attempt_v04.evaluate_soft_rule
    real_lock = attempt_v04.lock_training_ownership

    # 큐 임대만 잃는 경우는 DB 트리거가 보지 않으므로 Detector의 소유권 확인이 막아야 한다.
    real_decide = attempt_v04.decide
    _, payload = v04_job(conn, sid, "lease-queue", raw)

    def expire_queue_then_decide(*args):
        with other_connection() as other:
            other.execute("UPDATE powercode_queue.detection_logs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                          (payload["jobId"],))
        return real_decide(*args)

    def hold_soft(*args):
        wait_until(lambda: gate.is_set(), "attempt did not stop")
        return real_soft(*args)

    gate = threading.Event()
    with patch.object(attempt_v04, "decide", expire_queue_then_decide), patch.object(attempt_v04, "evaluate_soft_rule", hold_soft):
        failure = attempt(conn, payload)
    gate.set()
    assert isinstance(failure, RuntimeError), repr(failure)
    assert interims(conn, payload) == [] and decision(conn, payload) is None
    print("PASS 임대 상실: 큐 임대를 잃으면 중간 기록을 쓰지 않음")

    # 중간 기록에서 임대 상실을 확인하면 시도를 멈춘다. 뒤에서 소유권이 돌아와도 판정을 쓰지 않는다.
    answers = [False]

    def lost_once(*args):
        return answers.pop() if answers else real_lock(*args)

    gate = threading.Event()
    _, payload = v04_job(conn, sid, "lease-interim-once", raw)
    with patch.object(attempt_v04, "lock_training_ownership", lost_once), patch.object(attempt_v04, "evaluate_soft_rule", hold_soft):
        failure = attempt(conn, payload)
    gate.set()
    assert str(failure) == "detection_lease_lost", repr(failure)
    assert interims(conn, payload) == [] and decision(conn, payload) is None
    assert job_state(conn, payload)[1:5:3] == ("queued", "detection_processing_failed"), job_state(conn, payload)
    print("PASS 임대 상실: 중간 기록에서 잃으면 시도를 멈추고 판정을 쓰지 않음")

    # 최종 저장 직전 소유권 확인.
    save_rule_set(conn)
    _, payload = v04_job(conn, sid, "lease-final")
    with patch.object(attempt_v04, "lock_training_ownership", lambda *args: False):
        failure = attempt(conn, payload)
    assert str(failure) == "detection_lease_lost" and decision(conn, payload) is None, repr(failure)

    # 임대 갱신 스레드가 상실을 알린 경우.
    class LostHeartbeat:
        lost = True

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    _, payload = v04_job(conn, sid, "lease-heartbeat")
    with patch("trainer.lease.TrainingLeaseHeartbeat", LostHeartbeat):
        failure = attempt(conn, payload)
    assert str(failure) == "detection_lease_lost" and decision(conn, payload) is None, repr(failure)

    # 시도 임대가 실제로 만료된 경우.
    real_telemetry = attempt_v04._telemetry
    _, payload = v04_job(conn, sid, "lease-attempt")

    def expire_attempt(*args, **kwargs):
        with other_connection() as other:
            other.execute("UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                          (payload["jobId"],))
        return real_telemetry(*args, **kwargs)

    with patch.object(attempt_v04, "_telemetry", expire_attempt):
        failure = attempt(conn, payload)
    assert isinstance(failure, RuntimeError) and decision(conn, payload) is None, repr(failure)
    print("PASS 임대 상실: 최종 소유권 확인, 갱신 스레드 상실, 시도 임대 만료 모두 판정을 쓰지 않음")


def legacy_paths(conn, sid):
    status = default_policy(sid)
    rule_only = config(conn, sid, {"statusRulePolicy": status,
                                   "detectionPolicy": {"mode": "rule_only", "deadlineMs": 1000, "combination": "selected_path"}})
    legacy = config(conn, sid, {"statusRulePolicy": status})
    parallel = config(conn, sid, {"statusRulePolicy": status,
                                  "detectionPolicy": {"mode": "parallel", "deadlineMs": 1000, "combination": "any_anomaly"}})

    def v03(key, config_id, raw=None, setting="v03"):
        seen = observe(conn, sid, f"{key}-{uuid.uuid4()}", {**RAW, **(raw or {})})
        with contract(setting):
            payload = enqueue_observation(conn, seen.observation_id, config_id=config_id)
        conn.commit()
        return seen, payload

    # 기본 설정은 v03이고 판정 구조는 그대로다.
    with patch.dict(os.environ):
        os.environ.pop("DETECT_JOB_CONTRACT", None)
        seen = observe(conn, sid, f"default-{uuid.uuid4()}", {**RAW, "status": "F"})
        payload = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
        conn.commit()
    assert payload["configVersionId"] == str(rule_only) and job_state(conn, payload)[:3] == ("v03", "queued", rule_only)
    row = judged(conn, payload)
    evidence = row[5]
    assert evidence["schemaVersion"] == 3 and row[6] == rule_only and row[:3] == ("complete", "succeeded", "skipped"), row[:3]
    assert row[3] == "PROCESS_STATUS_F" and evidence["detectionPolicy"]["mode"] == "rule_only"
    assert evidence["statusRulePolicy"]["version"] == "process-status-hr-v1"
    assert "methodResults" not in evidence and "composite" not in evidence and "ruleSetRevision" not in evidence
    assert evidence["checkers"]["sr"]["evidence"] == [{"reason": "sr_experiment_pending"}]
    assert interims(conn, payload) == [] and evidence["requestedMode"] == "rule_only"
    print("PASS (d) 기본 설정 v03 작업: rule_only 구성 evidence v3·구성 열 유지, 중간 기록 없음")

    _, payload = v03("legacy-policy", legacy, {"status": "P"})
    row = judged(conn, payload)
    assert row[5]["schemaVersion"] == 3 and row[5]["detectionPolicy"] == {
        "mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly", "legacy": True}, row[5]["detectionPolicy"]
    assert row[0] == "partial" and row[1] == "succeeded" and row[2] == "not_ready" and row[5]["anomalous"] is True
    assert row[5]["ruleSeverities"] == ["warning"], "v1 상태 규칙의 P 단계는 그대로다"
    _, payload = v03("v1-status-n", rule_only, {"status": "N", "end_time_raw": ""})
    row = judged(conn, payload)
    assert row[5]["anomalous"] is True and row[5]["ruleSeverities"] == ["warning"], "v1의 N은 END_TIME과 무관하게 이상·주의다"
    _, payload = v03("v1-status-h", rule_only, {"status": "H"})
    row = judged(conn, payload)
    assert row[0] == "unavailable" and row[5]["anomalous"] is None, "v1의 H는 미판정이다"
    print("PASS (d) legacy·v1 정책 판정 값 불변: 상태 P·N·H, 새 규칙 미적용")

    # v04 설정에서도 입력 검증은 v03이고, 그 구성의 판정을 쓴다.
    conn.execute("INSERT INTO ops_input_controls(source_id,updated_by_actor_id) VALUES(%s,'integration') ON CONFLICT DO NOTHING", (sid,))
    conn.execute("UPDATE ops_input_controls SET config_version_id=%s WHERE source_id=%s", (parallel, sid))
    conn.commit()
    seen = observe(conn, sid, f"input-validation-{uuid.uuid4()}", {**RAW, "status": "F"})
    with contract("v04"):
        payload = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    assert payload["configVersionId"] == str(parallel) and job_state(conn, payload)[0] == "v03", payload
    with contract("v04"):
        row = judged(conn, payload)
    assert row[5]["schemaVersion"] == 3 and row[6] == parallel
    conn.execute("UPDATE ops_input_controls SET config_version_id=NULL WHERE source_id=%s", (sid,))
    conn.commit()

    # 승인 구성이 활성이어도 v04 설정의 승인 경로는 구성 없이 만든다.
    conn.execute("UPDATE ops_system_state SET active_config_version_id=%s WHERE id=1", (parallel,))
    conn.commit()
    seen = observe(conn, sid, f"approved-{uuid.uuid4()}", RAW)
    with contract("v04"):
        approved = enqueue_observation(conn, seen.observation_id, config_id=parallel)
        conn.commit()
    assert job_state(conn, approved)[:3] == ("v04", "queued", None) and "configVersionId" not in approved
    conn.execute("UPDATE ops_system_state SET active_config_version_id=NULL WHERE id=1")
    conn.commit()
    judged(conn, approved)
    print("PASS (d) v04 설정에서 입력 검증은 v03 유지, 활성 구성을 고른 승인 입력은 v04")

    # 같은 입력은 전환 전후로 작업 1개다.
    seen = observe(conn, sid, f"one-job-a-{uuid.uuid4()}", RAW)
    first = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
    conn.commit()
    conn.execute("UPDATE ops_input_controls SET config_version_id=NULL WHERE source_id=%s", (sid,))
    with contract("v04"):
        second = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    assert second["jobId"] == first["jobId"] and second["configVersionId"] == str(rule_only), second
    seen = observe(conn, sid, f"one-job-b-{uuid.uuid4()}", RAW)
    with contract("v04"):
        first = enqueue_observation(conn, seen.observation_id)
        repeated = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    assert repeated["jobId"] == first["jobId"]
    second = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
    conn.commit()
    assert second["jobId"] == first["jobId"] and "configVersionId" not in second, second
    count = conn.execute("""SELECT count(*) FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
        WHERE j.kind='detect' AND m.process_observation_id=%s""", (seen.observation_id,)).fetchone()[0]
    conn.commit()
    assert count == 1, count

    def job_count(observation_id):
        count = conn.execute("""SELECT count(*) FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
            WHERE j.kind='detect' AND m.process_observation_id=%s""", (observation_id,)).fetchone()[0]
        conn.commit()
        return count

    def concurrent_with_manifest_state(key, keep_manifest):
        """앞 생산자가 커밋하기 전에 뒤 생산자가 들어온다. 어느 잠금에서 기다리는지는 pg_locks로 확인한다."""
        seen = observe(conn, sid, f"{key}-{uuid.uuid4()}", RAW)
        if keep_manifest:
            # 작업 없이 manifest만 커밋된 상태. 코드는 이 상태를 만들지 않으므로 지워서 만든다.
            made = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
            conn.commit()
            conn.execute("DELETE FROM powercode_queue.detection_logs WHERE id=%s", (made["jobId"],))
            conn.execute("DELETE FROM run_jobs WHERE id=%s", (made["jobId"],))
            conn.commit()
        first = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
        answer = {}

        def concurrent():
            with psycopg.connect(os.environ["DATABASE_URL"]) as other:
                answer["payload"] = enqueue_observation(other, seen.observation_id)

        def waiting():
            with other_connection() as watcher:
                return watcher.execute("""SELECT count(*) FROM pg_stat_activity
                    WHERE wait_event_type='Lock' AND datname=current_database()""").fetchone()[0] > 0

        with contract("v04"):
            worker = threading.Thread(target=concurrent)
            worker.start()
            wait_until(lambda: waiting() or not worker.is_alive(), "second producer neither waited nor finished")
            assert worker.is_alive(), "앞 트랜잭션이 끝나기 전에 뒤 생산자가 지나갔다"
            conn.commit()
            worker.join(10)
        assert answer["payload"]["jobId"] == first["jobId"], answer
        assert job_count(seen.observation_id) == 1

    concurrent_with_manifest_state("race-new-manifest", False)
    concurrent_with_manifest_state("race-committed-manifest", True)

    # 먼저 만든 작업이 failed여도 재사용한다. 다시 판정하지 않는다.
    seen = observe(conn, sid, f"one-job-failed-{uuid.uuid4()}", RAW)
    first = enqueue_observation(conn, seen.observation_id, config_id=rule_only)
    conn.execute("UPDATE run_jobs SET status='failed',error_code='test',finished_at=clock_timestamp() WHERE id=%s", (first["jobId"],))
    conn.commit()
    with contract("v04"):
        second = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    assert second["jobId"] == first["jobId"] and job_count(seen.observation_id) == 1, second
    print("PASS (d) 같은 입력의 v03·v04 작업은 어느 순서로도, 동시에 넣어도 1개, failed 작업도 재사용")

    # 옛 메시지 복구: 기존 판정 확인과 hash 대조는 v04에서도 같다.
    def legacy_message(seen, **extra):
        identifier = uuid.uuid4()
        enqueue(conn, "detection", {"schemaVersion": 1, "eventId": str(identifier), "sourceId": str(sid),
                                    "observationId": str(seen.observation_id), **extra}, message_id=identifier)
        conn.commit()
        message = claim(conn, "detection", message_id=identifier)
        conn.commit()
        return message

    seen = observe(conn, sid, f"legacy-decided-{uuid.uuid4()}", RAW)
    execution, as_of = conn.execute("SELECT execution_id,persisted_at FROM data_observations WHERE id=%s",
                                    (seen.observation_id,)).fetchone()
    conn.execute("""INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,
        input_manifest_hash,input_as_of,status,rule_status,ml_status,evidence,decided_at,latency_status)
        VALUES(%s,%s,%s,%s,%s,%s,'complete','succeeded','skipped','{}',clock_timestamp(),'unavailable')""",
        (uuid.uuid4(), execution, seen.observation_id, rule_only, "a" * 64, as_of))
    conn.commit()
    message = legacy_message(seen)
    with contract("v04"):
        process_detection_message(conn, message, ROOT)
        conn.commit()
    assert job_count(seen.observation_id) == 0, "legacy 판정이 있는 관측에 v04 작업이 새로 생겼다"
    assert conn.execute("SELECT status::text FROM powercode_queue.detection_logs WHERE id=%s", (message.id,)).fetchone()[0] == "done"
    conn.commit()

    conn.execute("UPDATE ops_system_state SET active_config_version_id=%s WHERE id=1", (parallel,))
    conn.commit()
    for name, extra in (("wrong", {"configVersionId": str(parallel), "inputManifestHash": "0" * 64}),
                        ("no-config", {"inputManifestHash": "0" * 64})):
        seen = observe(conn, sid, f"legacy-{name}-{uuid.uuid4()}", RAW)
        message = legacy_message(seen, **extra)
        with contract("v04"):
            try:
                process_detection_message(conn, message, ROOT)
            except ValueError as exc:
                assert str(exc) == "input_manifest_mismatch", exc
            else:
                raise AssertionError(f"hash mismatch accepted: {name}")
        conn.rollback()
        assert job_count(seen.observation_id) == 0
    seen = observe(conn, sid, f"legacy-right-{uuid.uuid4()}", RAW)
    content_hash = conn.execute("SELECT content_hash FROM data_observations WHERE id=%s", (seen.observation_id,)).fetchone()[0]
    conn.commit()
    context = {"featureSetId": None, "featureHash": None}
    message = legacy_message(seen, configVersionId=str(parallel),
                             inputManifestHash=legacy_feature_hash(context, seen.observation_id, content_hash))
    with contract("v04"):
        process_detection_message(conn, message, ROOT)
        conn.commit()
    assert job_count(seen.observation_id) == 1
    conn.execute("UPDATE ops_system_state SET active_config_version_id=NULL WHERE id=1")
    conn.commit()
    print("PASS (d) 옛 메시지 복구 v04: 기존 판정이면 작업을 만들지 않고, hash가 다르면 거부")

    # 구성 없는 입력: v03 설정은 기다리고 v04 설정은 작업이 된다.
    waiting_source = source(conn, "구성 없는 입력")
    seen = observe(conn, waiting_source, f"waiting-{uuid.uuid4()}", RAW)
    waiting = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    assert "jobId" not in waiting
    assert claim_waiting_input(conn) is None
    conn.commit()
    message = claim(conn, "detection", source_id=str(waiting_source))
    conn.commit()
    try:
        process_detection_message(conn, message, ROOT)
    except ValueError as exc:
        assert str(exc) == "active_config_unavailable", exc
    else:
        raise AssertionError("v03 설정의 구성 없는 입력이 작업이 됐다")
    conn.rollback()
    conn.execute("UPDATE powercode_queue.detection_logs SET status='pending',lease_until=NULL,lease_token=NULL,attempts=0 WHERE id=%s", (message.id,))
    conn.commit()
    with contract("v04"):
        message = claim_waiting_input(conn)
        conn.commit()
        assert message is not None and message.id == uuid.UUID(waiting["eventId"]), message
        process_detection_message(conn, message, ROOT)
        conn.commit()
    row = conn.execute("""SELECT j.id,j.contract_version FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id
        WHERE j.kind='detect' AND m.process_observation_id=%s""", (seen.observation_id,)).fetchone()
    conn.commit()
    assert row[1] == "v04", row
    assert judged(conn, {"jobId": str(row[0])})[5]["schemaVersion"] == 4
    print("PASS (d) 구성 없는 입력: v03은 그대로 대기·active_config_unavailable, v04는 대기 입력을 작업으로 바꿔 판정")


def runtime_roles(conn, sid):
    """실제 권한으로 돈다. 작업 생성은 collector, 판정·중간 기록은 detector 권한이다."""
    conn.execute((REPO / "server/infra/scripts/database-roles.sql").read_text(encoding="utf-8"))
    conn.commit()
    seen = observe(conn, sid, f"roles-{uuid.uuid4()}", {**RAW, "status": "F"})
    conn.execute("SET ROLE powercode_collector")
    with contract("v04"):
        payload = enqueue_observation(conn, seen.observation_id)
    conn.commit()
    conn.execute("SET ROLE powercode_detector")
    conn.commit()
    try:
        real_soft = attempt_v04.evaluate_soft_rule

        def slow_soft(*args):
            time.sleep(.2)
            return real_soft(*args)

        save = psycopg.connect(os.environ["DATABASE_URL"])
        save_rule_set(save, soft=[ELAPSED_SOFT])
        save.close()
        with patch.object(attempt_v04, "evaluate_soft_rule", slow_soft):
            row = judged(conn, payload)
        assert row[5]["schemaVersion"] == 4 and interims(conn, payload), row[5]["composite"]
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
        conn.commit()
    print("PASS 실제 권한: collector가 v04 작업 생성, detector가 뷰 읽기·중간 기록·판정 저장")


def main():
    dsn = os.environ.get("PIPELINE_TEST_DATABASE_URL")
    parsed = urlsplit(dsn or "")
    if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.path.startswith("/qa_"):
        raise SystemExit("localhost qa_ 시험 DB만 허용한다")
    os.environ["DATABASE_URL"] = dsn
    global ROOT
    with tempfile.TemporaryDirectory(prefix="powercode-detection-") as directory, psycopg.connect(dsn) as conn:
        ROOT = Path(directory)
        conn.execute("INSERT INTO ops_system_state(id,revision,detector_state,changed_at) VALUES (1,0,'{}',clock_timestamp())")
        sid = source(conn, "판정 구조 시험")
        model_config, feature_set = trained_model(conn, sid)
        assert read_hard_rule_snapshot(conn.execute("SELECT snapshot->'hardRules' FROM ops_rule_set_revisions WHERE revision=1").fetchone()[0]).enabled_ids == \
            tuple(rule["id"] for rule in HARD_RULES)
        conn.commit()
        pinning_and_fail_closed(conn, sid)
        evidence_and_columns(conn, sid, model_config, feature_set)
        interim_records(conn, sid, model_config)
        lease_loss(conn, sid)
        legacy_paths(conn, sid)
        runtime_roles(conn, sid)
    print("PASS integration_detection_decision")


if __name__ == "__main__":
    main()
