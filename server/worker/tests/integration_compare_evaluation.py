"""평가 세트 생성·모델 버전 비교 v1을 실제 DB·실제 CPU IF 학습으로 확인한다(config-comparison-evaluation.md v1 구현 범위).

API(WebUI 세션 구현)를 거치지 않고 API가 만들 행·input_spec을 그대로 큐에 넣는다. 운영 모델 버전은 화면 학습과 같은
v1(정책 포함 train)이다. 고객 입력 없음, 통제 fixture만. 새 QA DB(`qa_*`, 예 `qa_compare_evaluation`)에서 단독 실행한다(training 큐를 다른 시험과 공유하지 않음).
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

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration_research_pipeline import observe, source
from pg_queue import enqueue
from trainer import runner
from trainer.catalog import register_feature_set
from trainer.operational_state import DEFAULT_POLICY, read_operational_state

FIELD = {"op": "field", "field": "P.total_count"}
DEFINITIONS = [{"name": "count_raw", "expression": FIELD}]
V1_POLICY = {"mode": "ml_only", "deadlineMs": 30000, "combination": "selected_path"}
# IF 학습 최소 fit 500 + 임계 보정 125행. 후반 절반 학습도 train 700행이 되도록 2000행.
ROWS = 2000
FOREIGN_ROWS = 1000


def stamp(moment):
    return moment.isoformat().replace("+00:00", "Z")


def seed(conn, name, rows):
    sid = source(conn, name)
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    for index in range(rows):
        anomalous = index % 25 == 0
        raw = {"adapter_type": "A" if index % 2 else "B", "total_count": str(900 if anomalous else 50 + index % 13),
               "success_count": str(50 + index % 13), "error_count": "0", "status": "S"}
        # 라벨은 일부 행에만(이상 4%·정상 일부, 나머지는 라벨 없음) — 정상 라벨 유무 null 규칙을 실제 값으로 본다.
        label = "anomaly" if anomalous else "normal" if index % 5 == 1 else None
        observe(conn, sid, f"compare-{sid}-{index}", raw, label, persisted=start + timedelta(seconds=index))
    features = register_feature_set(conn, DEFINITIONS, "fixture-admin", definition_kind="operator_defined")
    conn.commit()
    return {"sourceId": str(sid), "featureSetId": str(features), "start": start}


def submit(conn, spec, *, snapshot=None, config=None):
    job_id = uuid.uuid4()
    document = json.dumps(spec)
    conn.execute("""INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,idempotency_key,request_hash,
                        input_config_version_id,input_snapshot_id,input_spec,max_attempts,attempt_count,next_run_at,created_at,
                        contract_version,queue,producer_kind,producer_key)
                    VALUES(%s,%s,'queued','fixture-admin','admin',%s,%s,%s,%s,%s::jsonb,3,0,clock_timestamp(),clock_timestamp(),
                           'v03','training','user','fixture-admin')""",
                 (job_id, spec["kind"], str(job_id), hashlib.sha256(document.encode()).hexdigest(), config, snapshot, document))
    return job_id


def queue(conn, job_id):
    enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(job_id), "enqueuedAt": datetime.now(timezone.utc).isoformat()})
    conn.commit()


def finish(conn, job_id):
    for _ in range(5):
        with patch("trainer.runner.load_dotenv"):
            runner.run(once=True)
        conn.rollback()
        status, code, result = conn.execute("SELECT status,error_code,result_summary FROM run_jobs WHERE id=%s", (job_id,)).fetchone()
        conn.commit()
        if status in ("succeeded", "failed"):
            return status, code, result
    raise AssertionError(f"job {job_id} did not finish: {status} {code}")


def train(conn, fixture, from_offset, until_offset, schema=1):
    """schema 1 = 화면 학습과 같은 v1 구성(정책 포함 train), 3 = 모델 버전 v3. 기간은 fixture 시작 기준 초."""
    spec = {"kind": "train", "sourceId": fixture["sourceId"], "featureSetId": fixture["featureSetId"],
            "from": stamp(fixture["start"] + timedelta(seconds=from_offset)),
            "until": stamp(fixture["start"] + timedelta(seconds=until_offset)), "modelKey": "iforest"}
    if schema == 1:
        spec.update(ruleVersionIds=[], detectionPolicy=V1_POLICY)
    job_id = submit(conn, spec)
    queue(conn, job_id)
    status, code, result = finish(conn, job_id)
    assert (status, code) == ("succeeded", None), (status, code)
    spec_row = conn.execute("SELECT configuration_spec FROM ml_config_versions WHERE id=%s", (result["configVersionId"],)).fetchone()[0]
    conn.commit()
    assert spec_row["schemaVersion"] == schema, spec_row
    return result


def create_set(conn, fixture, snapshot_id, purpose="validation"):
    """API `POST /api/evaluation-sets`와 같은 한 트랜잭션: 작업 행 + queued 세트 행."""
    set_id = uuid.uuid4()
    spec = {"kind": "evaluation_set", "evaluationSetId": str(set_id), "sourceId": fixture["sourceId"],
            "snapshotId": snapshot_id, "purpose": purpose}
    job_id = submit(conn, spec, snapshot=snapshot_id)
    conn.execute("""INSERT INTO data_evaluation_sets(id,job_id,source_id,snapshot_id,purpose,display_name,created_by_actor_id)
                    VALUES(%s,%s,%s,%s,%s,%s,'fixture-admin')""",
                 (set_id, job_id, fixture["sourceId"], snapshot_id, purpose, "비교 시험 세트"))
    queue(conn, job_id)
    return set_id, job_id


def compare_spec(conn, fixture, set_id, config_ids, baseline):
    """API가 접수 시 고정하는 input_spec. 운영 상태는 같은 뷰에서 읽는다."""
    state = read_operational_state(conn)
    rule = conn.execute("SELECT rule_set_revision,rule_set_content_hash FROM ops_operational_state").fetchone()
    conn.commit()
    return {"kind": "compare", "sourceId": fixture["sourceId"], "evaluationSetId": str(set_id),
            "configVersionIds": sorted(config_ids),
            "baselines": [] if baseline is None else [{"modelKey": "iforest", "configVersionId": baseline}],
            "activeModelSetRevision": state.active_model_set_revision,
            "ruleSetRevision": rule[0], "ruleSetContentHash": rule[1],
            "detectionPolicyRevision": state.detection_policy_revision,
            "detectionPolicy": None if state.detection_policy_revision == 0 else dict(state.detection_policy)}


def run_compare(conn, spec):
    job_id = submit(conn, spec)
    queue(conn, job_id)
    return job_id, finish(conn, job_id)


def verify_set(conn, fixture, snapshot_id):
    set_id, job_id = create_set(conn, fixture, snapshot_id)
    status, code, result = finish(conn, job_id)
    assert (status, code) == ("succeeded", None), (status, code)
    row = conn.execute("""SELECT status,generation,content_hash,artifact_relative_path,ready_at IS NOT NULL
                            FROM data_evaluation_sets WHERE id=%s""", (set_id,)).fetchone()
    members = conn.execute("""SELECT count(*), count(*) FILTER (WHERE label_evidence->>'label'='anomaly'),
                                     count(*) FILTER (WHERE label_evidence->>'label'='normal')
                                FROM data_snapshot_members WHERE snapshot_id=%s AND split='validation'""", (snapshot_id,)).fetchone()
    conn.commit()
    assert row[0] == "ready" and row[4] and row[3] is None, row
    generation = row[1]
    assert (generation["rowCount"], generation["originalRowCount"], generation["syntheticRowCount"]) == (members[0], members[0], 0)
    assert (generation["truthCount"], generation["normalLabelCount"]) == (members[1], members[2]), (generation, members)
    assert result["contentHash"] == row[2] and result["generation"] == generation
    try:
        conn.execute("UPDATE data_evaluation_sets SET display_name='바뀜' WHERE id=%s", (set_id,))
        raise AssertionError("ready evaluation set must be immutable")
    except psycopg.errors.RaiseException:
        conn.rollback()
    return set_id, generation


def verify_synthetic_rejected(conn, fixture, snapshot_id):
    # 합성 요청은 v1에서 거부(API도 400이지만 Worker가 다시 막고, 작업 실패가 세트를 failed로 만든다).
    other_id = uuid.uuid4()
    spec = {"kind": "evaluation_set", "evaluationSetId": str(other_id), "sourceId": fixture["sourceId"],
            "snapshotId": snapshot_id, "purpose": "test"}
    job_id = submit(conn, spec, snapshot=snapshot_id)
    conn.execute("""INSERT INTO data_evaluation_sets(id,job_id,source_id,snapshot_id,purpose,display_name,synthetic_request,created_by_actor_id)
                    VALUES(%s,%s,%s,%s,'test','합성 세트','{"profileId":"esb-process-research-v1"}'::jsonb,'fixture-admin')""",
                 (other_id, job_id, fixture["sourceId"], snapshot_id))
    queue(conn, job_id)
    status, code, _ = finish(conn, job_id)
    row = conn.execute("SELECT status,error_code FROM data_evaluation_sets WHERE id=%s", (other_id,)).fetchone()
    conn.commit()
    assert (status, code) == ("failed", "synthetic_set_unsupported"), (status, code)
    assert row == ("failed", "synthetic_set_unsupported"), row


def comparison_rows(conn, job_id):
    comparison = conn.execute("""SELECT id,status,baseline_config_version_id,active_revision,summary
                                   FROM run_config_comparisons WHERE job_id=%s""", (job_id,)).fetchall()
    evaluations = conn.execute("""SELECT id,config_version_id,comparison_id,evaluation_set_id,purpose::text,applicability,population_count
                                    FROM run_evaluations WHERE job_id=%s ORDER BY config_version_id""", (job_id,)).fetchall()
    items = conn.execute("""SELECT config_version_id,role,status,error_code,evaluation_id FROM run_config_comparison_items
                             WHERE comparison_id=%s ORDER BY config_version_id""",
                         (comparison[0][0] if comparison else uuid.uuid4(),)).fetchall()
    conn.commit()
    return comparison, evaluations, items


def verify_compare(conn, fixture, set_id, generation, active, candidate, version3, foreign, overlapping):
    # 1. 운영(v1) + 후보(v1) + 후보(v3) → 성공, 비교 1행·평가 3행·항목 3행. v3는 접수 고정 정책, v1은 구성 정책으로 평가.
    spec = compare_spec(conn, fixture, set_id, [active, candidate, version3], active)
    job_id, (status, code, result) = run_compare(conn, spec)
    assert (status, code) == ("succeeded", None), (status, code, result)
    comparison, evaluations, items = comparison_rows(conn, job_id)
    assert len(comparison) == 1 and comparison[0][1] == "succeeded" and str(comparison[0][2]) == active, comparison
    assert len(evaluations) == 3 and len(items) == 3, (evaluations, items)
    for evaluation in evaluations:
        assert evaluation[2] == comparison[0][0] and evaluation[3] == set_id and evaluation[4] == "validation"
        assert evaluation[6] == generation["rowCount"], evaluation
    assert {(str(item[0]), item[1], item[2]) for item in items} == {(active, "active", "succeeded"), (candidate, "candidate", "succeeded"),
                                                                    (version3, "candidate", "succeeded")}
    summary = result
    assert summary == comparison[0][4] and summary["comparisonId"] == str(comparison[0][0])
    assert summary["comparisonContractVersion"] == "config-comparison-v1" and summary["replacementRecommendations"] is None
    assert [item["configVersionId"] for item in summary["items"]] == [active] + sorted([candidate, version3]), summary["items"]
    assert summary["evaluationSet"]["contentHash"] == conn.execute("SELECT content_hash FROM data_evaluation_sets WHERE id=%s", (set_id,)).fetchone()[0]
    conn.commit()
    for item in summary["items"]:
        ml = item["categoryResults"]["ml"]
        assert ml["measurementStatus"] == "measured" and item["counts"]["populationCount"] == generation["rowCount"], item
        # 세트에 정상·이상 라벨이 모두 있으므로 칸은 정수이고 분모는 라벨 행 수와 같다.
        assert all(type(value) is int for value in ml["confusion"].values()), ml
        assert ml["completeOnly"]["recall"]["denominator"] == generation["truthCount"], ml
        assert item["evaluationId"] in {str(row[0]) for row in evaluations}
        policy = item["evaluationPolicy"]
        if item["configVersionId"] == version3:
            pinned = summary["detectionPolicy"]["policy"]
            assert policy == {"source": "accepted", "detectionPolicy": pinned, "ruleCount": 0}, policy
            assert pinned == spec["detectionPolicy"] and pinned["deadlineMs"] == 2500, (pinned, spec)
        else:
            assert policy["source"] == "configuration" and policy["detectionPolicy"]["mode"] == "ml_only", policy
    # 2. 다른 source 후보 1개는 그 항목만 실패, 나머지는 성공.
    job_id, (status, code, result) = run_compare(conn, compare_spec(conn, fixture, set_id, [active, foreign], active))
    assert (status, code) == ("succeeded", None), (status, code)
    states = {item["configVersionId"]: (item["status"], item["errorCode"]) for item in result["items"]}
    assert states == {active: ("succeeded", None), foreign: ("failed", "config_source_mismatch")}, states
    comparison, evaluations, items = comparison_rows(conn, job_id)
    assert len(evaluations) == 1 and len(items) == 2, (evaluations, items)
    # 3. 학습 train이 세트 행과 겹치는 후보는 training_evaluation_overlap.
    job_id, (status, code, result) = run_compare(conn, compare_spec(conn, fixture, set_id, [active, overlapping], active))
    states = {item["configVersionId"]: (item["status"], item["errorCode"]) for item in result["items"]}
    assert (status, code) == ("succeeded", None) and states[overlapping] == ("failed", "training_evaluation_overlap"), states
    # 4. 기준선 없이 전부 실패 → 작업 failed, 실패 묶음과 항목 사유는 남는다.
    job_id, (status, code, result) = run_compare(conn, compare_spec(conn, fixture, set_id, [foreign, overlapping], None))
    assert (status, code) == ("failed", "comparison_all_items_failed"), (status, code)
    comparison, evaluations, items = comparison_rows(conn, job_id)
    assert len(comparison) == 1 and comparison[0][1] == "failed" and comparison[0][2] is None, comparison
    assert evaluations == [] and len(items) == 2 and all(item[2] == "failed" for item in items), items
    assert result["failedCount"] == 2


def main():
    dsn = os.environ["DATABASE_URL"]
    parsed = urlsplit(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/qa_")
    with tempfile.TemporaryDirectory(prefix="powercode-compare-") as directory:
        os.environ["ARTIFACT_ROOT"] = str(Path(directory).resolve())
        with psycopg.connect(dsn) as conn:
            fixture = seed(conn, "Compare evaluation fixture", ROWS)
            foreign_fixture = seed(conn, "Compare evaluation other source", FOREIGN_ROWS)
            active = train(conn, fixture, -1, ROWS + 10)
            candidate = train(conn, fixture, -1, ROWS + 10)
            # 시간순 70/15/15 분할이라 후반 구간 학습의 train은 전체 스냅샷의 validation 행을 덮는다.
            overlapping = train(conn, fixture, ROWS // 2, ROWS + 10)
            version3 = train(conn, fixture, -1, ROWS + 10, schema=3)
            foreign = train(conn, foreign_fixture, -1, FOREIGN_ROWS + 10)
            set_id, generation = verify_set(conn, fixture, active["snapshotId"])
            verify_synthetic_rejected(conn, fixture, active["snapshotId"])
            # 저장된 운영 정책 리비전(1)을 접수 시 고정하는 경로. 리비전 0(NULL→내장 기본값)은 단위 시험이 본다.
            conn.execute("""INSERT INTO ops_detection_policy_revisions(revision,policy,saved_by_actor_id)
                            VALUES(1,%s::jsonb,'fixture-admin')""", (json.dumps({**DEFAULT_POLICY, "deadlineMs": 2500}),))
            conn.commit()
            verify_compare(conn, fixture, set_id, generation, active["configVersionId"], candidate["configVersionId"],
                           version3["configVersionId"], foreign["configVersionId"], overlapping["configVersionId"])
    print("compare evaluation integration passed: evaluation set, v1 active + v1/v3 candidates compare, item failures, all-failed")


if __name__ == "__main__":
    main()
