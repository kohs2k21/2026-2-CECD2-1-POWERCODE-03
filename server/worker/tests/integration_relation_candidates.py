"""실험 `recommend_relations`(층 B 관계·처리 시간 후보)를 격리 시험 DB의 training 큐로 한 번 돌린다.

실험 기능. 작은 합성 fixture(거래 50개)만 쓰고 고객 데이터·네트워크·GPU 없음.
전제: 시험 DB에 migration 001~021이 적용돼 있어야 한다(021 = `job_kind` 값과 `run_jobs_v03_contract` training 분기).
작업 행은 API `enqueueCandidateJob`(backend/src/ruleset/ruleCandidates.ts)과 같은 v03 training 모양으로 넣는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector.storage import ingest_observation  # noqa: E402
from pg_queue import enqueue  # noqa: E402
from trainer.runner import run  # noqa: E402
from trainer.snapshot import build_snapshot  # noqa: E402
import relation_candidates_oracle as oracle  # noqa: E402  (시험 전용: 메모리 일괄 구현)
from test_relation_candidates_stream import oracle_with_current_limitation, without_protocol_drafts  # noqa: E402

TRANSACTIONS = 50  # 블록 = 거래 → train 35, validation 7, test 8 (snapshot.assign_splits 70/15/15)


def _execution(conn, source_id, layer, key):
    execution_id = uuid.uuid4()
    conn.execute("""INSERT INTO data_executions (id, source_id, layer, execution_key, key_parts, key_contract_version,
                     first_observed_at) VALUES (%s, %s, %s, %s, '{}'::jsonb, 'test-v1', clock_timestamp())""",
                 (execution_id, source_id, layer, key))
    return execution_id


def _ingest(conn, source_id, layer, key, raw, persisted_at):
    observation = ingest_observation(conn, source_id=source_id, layer=layer, dedup_key=f"rel:{source_id}:{key}",
                                     raw=raw, execution_id=_execution(conn, source_id, layer, key), revision_no=1)
    # 블록이 거래 단위가 되도록 한 거래의 행을 같은 저장 시각으로 맞춘다(시험 fixture 제어).
    conn.execute("UPDATE data_observations SET persisted_at=%s WHERE id=%s", (persisted_at, observation.observation_id))


ACTOR = "test-admin"


def _job(conn, kind_spec, role):
    """API enqueueCandidateJob과 같은 행: v03 training(user producer, producer_key = 요청자, 멱등키 필수) + 큐 메시지."""
    job_id = uuid.uuid4()
    conn.execute("""INSERT INTO run_jobs (id, kind, status, requested_by_actor_id, requester_role, idempotency_key, request_hash,
                     input_snapshot_id, input_spec, max_attempts, attempt_count, next_run_at, created_at, contract_version,
                     queue, producer_kind, producer_key)
                   VALUES (%s, 'recommend_relations', 'queued', %s, %s, %s, %s, %s, %s::jsonb, 3, 0,
                           clock_timestamp(), clock_timestamp(), 'v03', 'training', 'user', %s)""",
                 (job_id, ACTOR, role, str(uuid.uuid4()), hashlib.sha256(json.dumps(kind_spec).encode()).hexdigest(),
                  kind_spec["snapshotId"], json.dumps(kind_spec), ACTOR))
    enqueue(conn, "training", {"schemaVersion": 1, "jobId": str(job_id),
                               "enqueuedAt": datetime.now(timezone.utc).isoformat()})
    return job_id


def seed(conn, source_id):
    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    for index in range(TRANSACTIONS):
        tx = f"TX-{index:04d}"
        at = base + timedelta(seconds=index)
        if index != 8:  # TX-0008: TRANSACTION 행 없음 → interface 미정(undetermined로 셈, 추정 안 함)
            _ingest(conn, source_id, "T", f"T:{tx}", {"transaction_id": tx, "interface_id": "IF_A",
                                                      "status": "F" if index == 5 else "S",
                                                      "start_channel_id": "CH_A", "end_channel_id": "CH_B"}, at)
        steps = [("IF_A_PR001", "NONE", 0), ("IF_A_PR002", "IF_A_PR001", 0)]
        if index == 5:  # 필수 단계 누락
            steps = steps[:1]
        if index == 6:  # 재시도: 거래당 1회
            steps.append(("IF_A_PR002", "IF_A_PR001", 1))
        if index == 9:  # 제외 대상 비표준 단계
            steps.append(("IF_A_STUB", "IF_A_PR002", 0))
        for pid, depend, retry in steps:
            end = "\\N" if (index == 10 and pid == "IF_A_PR002") else f"2026-01-01T00:00:00.{100 + index:03d}+09:00"
            _ingest(conn, source_id, "P", f"P:{tx}:{pid}:{retry}",
                    {"transaction_id": tx, "process_id": pid, "depend_process_id": depend, "adapter_type": "DB",
                     "channel_id": "CH_A" if pid == "IF_A_PR001" else "CH_B", "process_hub_id": "hub", "status": "S", "retry_count": retry, "total_count": 1,
                     "success_count": 1, "error_count": 0, "start_time_raw": "2026-01-01T00:00:00+09:00",
                     "end_time_raw": end}, at)
        in_id = f"OTHER-{index}" if index == 7 else f"MSG-{index}"  # TX-0007: 인계 ID 불일치
        _ingest(conn, source_id, "M", f"M:{tx}:OUT", {"transaction_id": tx, "process_id": "IF_A_PR001",
                                                      "message_id": f"MSG-{index}", "direction": "OUT"}, at)
        if index != 5:
            _ingest(conn, source_id, "M", f"M:{tx}:IN", {"transaction_id": tx, "process_id": "IF_A_PR002",
                                                         "message_id": in_id, "direction": "IN"}, at)
    # transaction_id가 없는(NULL) PROCESS 1행: 서버 커서 정렬(NULLS FIRST)과 unassigned 집계 경로를 지나게 한다.
    _ingest(conn, source_id, "P", "P:no-transaction",
            {"process_id": "IF_A_PR009", "depend_process_id": "NONE", "adapter_type": "DB", "status": "S",
             "retry_count": 0, "start_time_raw": "2026-01-01T00:00:00+09:00",
             "end_time_raw": "2026-01-01T00:00:00.100+09:00"}, base)
    conn.commit()
    return base


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    parsed = urlsplit(dsn or "")
    # 공용 로컬 DB(pwc-local 등)를 막는다: 로컬 호스트 + 전용 시험 DB 이름만 허용.
    # 질의 문자열(?dbname=·?host=·?service= 등)은 경로·호스트 검사를 우회할 수 있으므로 통째로 거부한다.
    if parsed.scheme not in ("postgresql", "postgres") or parsed.query or parsed.fragment or \
            parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path != "/qa_relation_candidates":
        raise SystemExit("격리된 로컬 시험 DB(경로 /qa_relation_candidates, 질의 문자열 없음)의 DATABASE_URL이 필요하다")
    with psycopg.connect(dsn, autocommit=True) as admin:
        # 연결 뒤에도 실제 DB 이름을 확인한다(PG* 환경 변수 등 대비).
        if admin.execute("SELECT current_database()").fetchone()[0] != "qa_relation_candidates":
            raise SystemExit("연결된 DB가 qa_relation_candidates가 아니다")
        # 021 적용 여부: enum 값과 CHECK의 training 분기 둘 다(이 시험은 DB를 고치지 않는다).
        ready = admin.execute("""SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                                               WHERE t.typname = 'job_kind' AND e.enumlabel = 'recommend_relations'),
                                        EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'run_jobs_v03_contract'
                                                AND pg_get_constraintdef(oid) LIKE '%%recommend_relations%%')""").fetchone()
        if ready != (True, True):
            raise SystemExit(f"migration 021이 적용된 시험 DB가 필요하다(enum, CHECK) = {ready}")
    source_id = uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("""INSERT INTO ops_sources (id, dataset_key, display_name, ingestion_method, schema_version,
                         clock_basis, field_catalog, created_at)
                       VALUES (%s, %s, 'Relation candidate test', 'synthetic-test', 'v1', '{}'::jsonb, '{}'::jsonb,
                               clock_timestamp())""", (source_id, f"relation-{source_id}"))
        conn.commit()
        base = seed(conn, source_id)
        snapshot = build_snapshot(conn, source_id=source_id, from_time=(base - timedelta(minutes=1)).isoformat(),
                                  until_time=(base + timedelta(hours=1)).isoformat(), actor_id="test-admin")
        conn.commit()
        spec = {"kind": "recommend_relations", "snapshotId": str(snapshot.id), "sourceId": str(source_id),
                "excludeProcessIds": ["IF_A_STUB"]}
        job_id = _job(conn, spec, "admin")
        conn.commit()
        # 관리자 전용: 021 CHECK의 training 분기는 requester_role = 'admin'만 받으므로 사용자 작업 행은 생기지 않는다
        # (Trainer claim의 관리자 확인은 그 뒤 2차 방어). 큐 메시지도 같은 트랜잭션이라 남지 않는다.
        try:
            _job(conn, {**spec, "excludeProcessIds": []}, "user")
        except psycopg.errors.CheckViolation as exc:
            assert exc.diag.constraint_name == "run_jobs_v03_contract", exc.diag.constraint_name
            conn.rollback()
        else:
            raise AssertionError("user-role recommend_relations job was accepted")
    run(once=True)
    with psycopg.connect(dsn) as conn:
        status, result, error = conn.execute("SELECT status::text, result_summary, error_code FROM run_jobs WHERE id=%s",
                                             (job_id,)).fetchone()
        assert status == "succeeded", (status, error, result)
        # 관리자 전용: 이 원천의 관계 추천 작업은 관리자 작업 1건뿐이다(사용자 작업은 CHECK에서 거부됨).
        jobs = conn.execute("""SELECT count(*) FROM run_jobs WHERE kind = 'recommend_relations'
                                AND input_spec->>'sourceId' = %s""", (str(source_id),)).fetchone()[0]
        assert jobs == 1, jobs
    assert snapshot.counts == {"train": 72, "validation": 14, "test": 16}, snapshot.counts
    header = result["graph"]["graph"]
    assert header["layer"] == "pattern" and header["snapshotId"] == str(snapshot.id), header
    assert set(header) == {"layer", "id", "version", "sourceId", "snapshotId", "period", "ruleSetRevision", "generatedAt"}
    assert result["excluded"]["processIds"] == {"IF_A_STUB": 1}, result["excluded"]
    assert result["unassigned"] == {"transactions": {"no_transaction_row": 1},
                                    "rows": {"processTransactionIdAbsent": 1, "unkeyedProcessRows": 0,
                                             "messageKeyAbsent": 0, "elapsedProcessIdAbsent": 0}}, result["unassigned"]
    assert result["data"]["interfaceTransactions"] == {"IF_A": 34}, result["data"]
    # 규칙 id 없음. interfaceId는 required_step scope에만(판정 키), 나머지는 adapterType 또는 null.
    assert all("id" not in item and ("interfaceId" in item["scope"]) == (item["kind"] == "required_step")
               for item in result["candidates"] if item["scope"] is not None), result["candidates"]
    by_kind = {(item["kind"], item["from"]["processId"], (item["to"] or {}).get("processId")): item
               for item in result["candidates"]}
    assert all("evaluable" not in item for item in result["candidates"])
    required = by_kind[("required_step", "IF_A_PR001", None)]
    assert required["scope"] == {"interfaceId": "IF_A"}, required
    assert (required["evaluatedAt"], required["status"], required["counts"]["satisfied"]) == \
        ("transaction", "candidate", 34), required
    last = by_kind[("required_step", "IF_A_PR002", None)]
    assert (last["evaluatedAt"], last["status"]) == ("transaction", "review_needed") and last["counts"] == \
        {"applicable": 34, "satisfied": 33, "violated": 1, "undetermined": 0}, last
    assert last["counterexampleRefs"] == [{"refs": ["transaction:TX-0005"], "reason": "step_absent",
                                           "transactionStatus": "F"}], last
    assert last["violationsByTransactionStatus"] == {"F": 1}, last
    precedes = by_kind[("precedes", "IF_A_PR001", "IF_A_PR002")]
    assert precedes["status"] == "candidate" and precedes["counts"]["applicable"] == 33, precedes
    handoff = by_kind[("message_handoff", "IF_A_PR001", "IF_A_PR002")]
    assert handoff["status"] == "review_needed" and handoff["counts"]["violated"] == 1, handoff
    assert handoff["source"]["jobId"] == str(job_id) and handoff["source"]["snapshotId"] == str(snapshot.id)
    assert handoff["pairedPrecedesEdgeId"] == precedes["patternEdgeId"] and handoff["scope"] == {"adapterType": "DB"}
    assert result["parameters"]["handoffMismatchSuffixPatterns"] == [r"-0[0-9]\Z"], result["parameters"]
    thresholds = {item["scope"]["processId"]: item for item in result["thresholdCandidates"]}
    assert thresholds["IF_A_PR001"]["status"] == "candidate" and thresholds["IF_A_PR001"]["evidence"]["n"] == 35
    assert thresholds["IF_A_PR002"]["evidence"]["endTimeMissingReferenceRows"] == 1, thresholds["IF_A_PR002"]
    assert thresholds["IF_A_PR002"]["display"]["interfaceIds"] == ["IF_A"]
    assert "IF_A_STUB" not in thresholds
    assert result["counts"]["thresholdCandidateCount"] == 2, result["counts"]
    # 승인 가능 = required(PR001)·required(PR002)·precedes·message_handoff(검토 필요 포함)
    assert result["candidateCounts"]["approvable"] == 4 and result["candidateCounts"]["listedTotal"] == 4, \
        result["candidateCounts"]
    # 회귀: 같은 DB에서 메모리 일괄 구현(오라클)이 낸 결과와 스트리밍 작업 결과가 같아야 한다.
    with psycopg.connect(dsn) as conn:
        loaded = oracle.load_inputs(conn, snapshot.id)
    generated = datetime.fromisoformat(result["graph"]["graph"]["generatedAt"].replace("Z", "+00:00"))
    # 오라클 고정 뒤 바뀐 thresholdCandidates의 limitation·limitationRefs 두 필드만 현재 값으로 맞추고 나머지는 엄격 비교.
    expected = oracle_with_current_limitation(
        oracle.build_result(loaded, oracle.parse_input(spec), job_id=job_id, generated_at=generated))
    # 문자열로 비교한다(dict 비교는 1 == 1.0을 같게 본다). jsonb는 키 순서를 보존하지 않으므로 이 비교만 sort_keys=True,
    # 오라클 쪽은 jsonb 왕복과 같은 숫자 표기가 되도록 한 번 직렬화했다가 읽는다.
    assert json.dumps(json.loads(json.dumps(expected)), sort_keys=True, ensure_ascii=False) == \
        json.dumps(without_protocol_drafts(result, last=False), sort_keys=True, ensure_ascii=False), \
        "streaming job result differs from oracle"
    # protocolPathDrafts(오라클 뒤 추가): DB 경로가 channel_id·start/end_channel_id를 읽어 경로를 센다.
    # 34거래 중 TX-0005(단계 1개, 끝 채널 CH_A ≠ CH_B)·TX-0009(STUB도 경로에 포함 — excludeProcessIds 미적용)는 다른 경로.
    # 제외는 거래 단위(TX-0008 거래 행 없음), TRANSACTION_ID 없는 PROCESS 1행은 행 수로 따로.
    assert (result["protocolPathDrafts"]["excludedReasons"], result["protocolPathDrafts"]["transactionIdMissingRows"]) \
        == ({"no_transaction_row": 1}, 1), result["protocolPathDrafts"]
    drafts = result["protocolPathDrafts"]["interfaces"]
    assert [(d["interfaceId"], d["status"], d["pathTransactions"], d["endpoints"]["mismatch"],
             [(p["pathKey"], p["transactions"]) for p in d["paths"]], d["omittedPaths"]) for d in drafts] == \
        [("IF_A", "draft", 34, 1, [("CH_A:CH_B", 32), ("CH_A", 1), ("CH_A:CH_B:CH_B", 1)],
          {"paths": 0, "transactions": 0})], drafts
    print("relation candidates integration passed:", json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
