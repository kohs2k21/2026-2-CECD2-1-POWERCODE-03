"""승인 구성의 Rule·IF 판정을 수행하고 결과를 멱등하게 저장한다."""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

import model_adapters
from feature_fit import requires_fit
from detector.input_reader import load_process_observation
from detector.policy import configure_capacity
from feature_worker import active_config, active_source
from pg_queue import ClaimedMessage, acknowledge, claim, fail
from telemetry import cost_family, queue_wait_ms

LOGGER = logging.getLogger(__name__)


def process_detection_message(conn: Any, message: ClaimedMessage, root: Path | None) -> None:
    from detector.computation import load_context, compute, legacy_feature_hash
    from detector.jobs import detect_contract,enqueue_observation,input_config
    from trainer.runner import _claim_job, _finish
    from trainer.lease import TrainingLeaseHeartbeat, lock_training_ownership
    started = time.monotonic()
    observation = load_process_observation(conn, message)
    payload = message.payload
    if not payload.get("jobId"):
        # Bridge old feature/detection backlog to the immutable job protocol.
        with conn.transaction():
            requested = payload.get("configVersionId")
            v04 = detect_contract(conn,observation.source_id,requested) == "v04"
            # v04는 구성 없이 판정한다. 메시지가 구성을 가리킬 때만 그 구성으로 입력을 대조한다.
            pinned = requested if v04 else requested or input_config(conn,observation.source_id)
            if pinned is None and not v04:
                raise ValueError("active_config_unavailable")
            with conn.cursor() as cur:
                cur.execute("SELECT content_hash FROM data_observations WHERE id=%s", (observation.observation_id,))
                content_hash = cur.fetchone()[0]
                previous_hash = None
                if pinned is not None:
                    context = load_context(conn, pinned, observation.source_id)
                    previous_hash = legacy_feature_hash(context, observation.observation_id, content_hash)
                if payload.get("inputManifestHash") not in (None, previous_hash):
                    raise ValueError("input_manifest_mismatch")
                # v04 판정은 구성에 묶이지 않으므로 그 관측의 판정이 하나라도 있으면 새로 만들지 않는다.
                cur.execute("""SELECT 1 FROM run_process_decisions WHERE process_observation_id=%s
                    AND (%s OR config_version_id=%s)""", (observation.observation_id,v04,pinned))
                if cur.fetchone() is None:
                    enqueue_observation(conn, observation.observation_id, config_id=pinned)
            if not acknowledge(conn, "detection", message):
                raise RuntimeError("detection_lease_lost")
        return
    claimed = _claim_job(conn, message, "detector")
    conn.commit()
    if claimed is None:
        return
    job, attempt_id, attempt_token = claimed
    queue_wait = queue_wait_ms(conn, message, attempt_id)
    heartbeat = TrainingLeaseHeartbeat(lambda: psycopg.connect(os.environ["DATABASE_URL"]),
        message,job["id"],attempt_id,attempt_token)
    heartbeat.start()
    try:
        with conn.cursor() as cur:
            # readiness(6번째)는 v04 재판정 시도만 읽는다(readiness.relation, relation-rules.md 6.3). v03 경로는 앞 5개만 쓴다.
            cur.execute("""SELECT j.input_spec,j.input_config_version_id,j.input_manifest_id,m.content_hash,m.input_as_of,
                m.readiness FROM run_jobs j JOIN run_input_manifests m ON m.id=j.input_manifest_id WHERE j.id=%s""", (job["id"],))
            fixed = cur.fetchone()
            if fixed is None or any(payload.get(key) != fixed[0].get(key) for key in
                    ("sourceId","observationId","configVersionId","inputManifestHash","inputManifestId")):
                raise ValueError("input_manifest_mismatch")
        if job["contractVersion"] == "v04":
            from detector.attempt_v04 import run_attempt
            run_attempt(conn, message, job, attempt_id, attempt_token, observation, fixed, root,
                        started=started, queue_wait=queue_wait, heartbeat=heartbeat, finish=_finish)
            return
        config_id = fixed[1]
        context = load_context(conn, config_id, observation.source_id)
        with conn.cursor() as cur:
            cur.execute("SELECT content_hash FROM data_observations WHERE id=%s",(observation.observation_id,))
            source_hash=cur.fetchone()[0]
            cur.execute('SELECT ingestion_method,dataset_key FROM ops_sources WHERE id=%s',(observation.source_id,))
            source_mode=cur.fetchone()
        conn.commit()
        result = compute(context, observation.allowed_values, root, started_at=started)
        if heartbeat.lost:
            raise RuntimeError("detection_lease_lost")
        elapsed_ms = (time.monotonic()-started)*1000
        durations = {'totalMs': elapsed_ms}
        durations.update({key: result['ml'][key] for key in ('preprocessMs', 'inferenceMs') if key in result['ml']})
        if 'ruleMs' in result['rule']:
            durations['ruleMs'] = result['rule']['ruleMs']
        telemetry = cost_family(durations, batch_size=1, samples=[elapsed_ms], queue_wait=queue_wait)
        telemetry['details'].update(scope='detector_process_revision', sourceClockStatus=observation.clock_status,
                                    concurrency=2 if result['requestedMode']=='parallel' else 1,
                                    concurrencyScope='checker_callbacks', modelNJobs=2 if context['models'] else None)
        checkers = {key: {name: result[path].get(name) for name in ('status', 'anomalous', 'evidence')}
                    for key, path in (('hr', 'hr'), ('ml', 'ml'), ('sr', 'sr'))}
        evidence = {"schemaVersion": 3,"rules":result["rule"]["evidence"],"models":result["ml"]["evidence"],
                    'checkers': checkers, 'telemetry': telemetry,
                    'statusRulePolicy': context.get('statusRulePolicy'),
                    'observation': {'id': str(observation.observation_id), 'revisionNo': observation.revision_no,
                                    'inputAsOf': fixed[4].isoformat()},
                    "inputManifestHash":fixed[3],"anomalous":result["anomalous"],
                    "requestedMode":result["requestedMode"],"actualPaths":result["actualPaths"],
                    "detectionPolicy":result["policy"],"ruleSeverities":result["rule"].get("severity",[])}
        if source_mode and source_mode[0]=='demo-replay':
            evidence['demo']={'historicalReplay':True,'runKey':source_mode[1].removeprefix('demo-replay:'),'scope':'Process replay; source occurrence latency unavailable'}
        if fixed[0].get('inputOrigin'):
            evidence['input']=fixed[0]['inputOrigin']
        code = result["rule"].get("code") or ("IF_ANOMALY" if result["ml"].get("anomalous") else None)
        with conn.transaction():
            if not lock_training_ownership(conn,message,job["id"],attempt_id,attempt_token):
                raise RuntimeError("detection_lease_lost")
            with conn.cursor() as cur:
                feature_result=next((result[path] for path in ("rule","ml") if "featureValues" in result[path]),None)
                # This cache key has no model/fit identity. Fitted values must remain
                # local to the pinned configuration rather than pollute global rows.
                fitted = any(requires_fit(item['expression']) for item in context['definitions'])
                if feature_result is not None and context["featureSetId"] and not fitted:
                    from feature_runtime import input_manifest_hash
                    cur.execute("""INSERT INTO data_feature_materializations(observation_id,feature_set_id,
                        input_manifest_hash,values_json,missing_names) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)
                        ON CONFLICT DO NOTHING""", (observation.observation_id,context["featureSetId"],
                        input_manifest_hash(str(observation.observation_id),source_hash,context["featureHash"]),
                        json.dumps(feature_result["featureValues"]),json.dumps(feature_result["missingFeatures"])))
                cur.execute("""INSERT INTO run_process_decisions
                    (id,execution_id,process_observation_id,config_version_id,input_manifest_hash,input_as_of,
                    status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,latency_status,
                    job_id,input_manifest_id,result_attempt_id,result_lease_token)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,clock_timestamp(),'unavailable',%s,%s,%s,%s)""",
                    (uuid.uuid4(),observation.execution_id,observation.observation_id,config_id,fixed[3],fixed[4],
                    result["status"],result["rule"]["status"],result["ml"]["status"],code,result["ml"].get("score"),
                    json.dumps(evidence),job["id"],fixed[2],attempt_id,attempt_token))
                cur.execute("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                    VALUES(%s,%s,'detector',%s,%s)""",(uuid.uuid4(),observation.observation_id,config_id,(time.monotonic()-started)*1000))
            _finish(conn,message,job,attempt_id,attempt_token,{"status":result["status"],
                    'executionCost': telemetry, 'checkers': checkers},None,True)
    except Exception as exc:
        conn.rollback()
        # _finish가 큐 임대를 비우므로 run()의 fail은 효력이 없다. v04 fail-closed 원인은 여기서 남긴다.
        failure = str(exc)[:100] if job["contractVersion"] == "v04" and isinstance(exc, ValueError) \
            else "detection_processing_failed"
        _finish(conn,message,job,attempt_id,attempt_token,None,failure,False)
        conn.commit()
        raise
    finally:
        heartbeat.stop()


# dead 큐 행 대조(reconcile_dead_jobs)는 색인 없는 순차 탐색이라 반복마다 부르지 않고 이 간격마다 한 번 부른다.
RECONCILE_SECONDS = 30.0
# 다중 작업자(run_workers): 직전 반복에서 메시지를 처리했으면 신청·활성 구성·원천 조회와 feature 복구를 이 간격마다만 한다.
FIXED_REFRESH_SECONDS = 1.0
# DETECTOR_WORKERS 기본값과 허용 범위.
DEFAULT_WORKERS = 4
MAX_WORKERS = 8


class _Loop:
    """판정 루프 한 개(연결 1개). run()과 run_workers()의 작업자가 같은 반복을 쓴다.

    - fixed_refresh_seconds None: 고정 단계(신청·활성 구성·원천 조회·feature 복구)를 반복마다 한다(run()).
      값이 있으면 직전 반복에서 메시지를 처리했을 때 마지막 고정 단계 뒤 그 간격이 지난 반복에서만 한다.
      직전 반복이 비었으면(큐가 빔) 반복마다 한다. 건너뛴 반복의 원천 범위 claim은 마지막으로 읽은 원천을 쓴다.
    - reconcile: 이 루프가 dead 정리(reconcile_dead_jobs)를 맡는지. 첫 반복과 그 뒤 reconcile_seconds마다.
    - expire_in_claim False: detection claim 안의 dead 처리 UPDATE를 건너뛰고, 정리를 맡은 루프가 정리 주기마다
      정리 직전에 expire_exhausted_leases를 부른다.
    시각(time.monotonic)은 반복마다 한 번 읽는다.
    """

    def __init__(self, conn: Any, root: Path | None, worker_id: str, *, reconcile: bool = True,
                 reconcile_seconds: float = RECONCILE_SECONDS, expire_in_claim: bool = True,
                 fixed_refresh_seconds: float | None = None) -> None:
        self.conn, self.root, self.worker_id = conn, root, worker_id
        self.reconcile, self.reconcile_seconds = reconcile, reconcile_seconds
        self.expire_in_claim, self.fixed_refresh_seconds = expire_in_claim, fixed_refresh_seconds
        self.last_reconcile: float | None = None
        self.last_fixed: float | None = None
        self.busy = False
        self.config_id: Any = None
        self.source_id: Any = None

    def fixed_due(self, now: float) -> bool:
        return (self.fixed_refresh_seconds is None or not self.busy or self.last_fixed is None
                or now - self.last_fixed >= self.fixed_refresh_seconds)

    def _fixed(self, now: float) -> None:
        from detector.applications import process_application_once
        conn = self.conn
        process_application_once(conn, self.root, self.worker_id)
        conn.commit()
        self.config_id = active_config(conn)
        conn.commit()
        self.source_id = active_source(conn, self.config_id) if self.config_id else None
        conn.commit()
        self.last_fixed = now
        # Keep a bounded recovery step so historical waiting inputs also make progress.
        if self.source_id:
            from feature_worker import process_feature_message
            legacy = claim(conn, "feature", source_id=str(self.source_id))
            conn.commit()
            if legacy:
                try:
                    process_feature_message(conn, legacy, self.config_id)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    fail(conn, "feature", legacy, reason="feature_bridge_failed")
                    conn.commit()

    def _claim(self) -> ClaimedMessage | None:
        conn = self.conn
        expire = {} if self.expire_in_claim else {"expire_exhausted": False}
        # Fixed jobs keep their pinned config even if the active source changes.
        message = claim(conn, "detection", job_kinds=("detect",), **expire)
        conn.commit()
        if message is None and self.source_id:
            message = claim(conn, "detection", source_id=str(self.source_id), **expire)
            conn.commit()
        if message is None:
            from detector.jobs import claim_waiting_input
            message = claim_waiting_input(conn)
            conn.commit()
        return message

    def _maintain(self, now: float) -> None:
        if not self.reconcile or (self.last_reconcile is not None and now - self.last_reconcile < self.reconcile_seconds):
            return
        from pg_queue import expire_exhausted_leases, reconcile_dead_jobs
        if not self.expire_in_claim:
            expire_exhausted_leases(self.conn, "detection")
            self.conn.commit()
        reconcile_dead_jobs(self.conn, "detection")
        self.conn.commit()
        self.last_reconcile = now

    def iterate(self) -> bool:
        """반복 1회. 메시지를 처리(성공·실패 무관)했으면 True, 가져올 메시지가 없었으면 False."""
        conn = self.conn
        now = time.monotonic()
        if self.fixed_due(now):
            self._fixed(now)
        message = self._claim()
        self._maintain(now)
        self.busy = message is not None
        if message is None:
            return False
        try:
            process_detection_message(conn, message, self.root)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            reason = str(exc) if isinstance(exc, ValueError) else "detection_failed"
            fail(conn, "detection", message, reason=reason[:100])
            conn.commit()
        return True


def _prepare(conn: Any, root: Path | None) -> None:
    from detector.applications import prepare_active
    try:
        prepare_active(conn, root)
        conn.commit()
    except (model_adapters.ModelNotConfigured, ValueError):
        conn.rollback()
        # Rule path remains available when model preparation fails.


def run(*, once: bool = False, poll_seconds: float = 1.0, reconcile_seconds: float = RECONCILE_SECONDS) -> None:
    """단일 루프(연결 1개). 고정 단계는 반복마다, claim 안 dead 처리도 그대로다(통합 시험·compose.demo.yaml이 쓴다)."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요하다")
    root = Path(os.environ["ARTIFACT_ROOT"]) if os.environ.get("ARTIFACT_ROOT") else None
    worker_id = f"detector-{uuid.uuid4()}"
    with psycopg.connect(database_url) as conn:
        _prepare(conn, root)
        loop = _Loop(conn, root, worker_id, reconcile_seconds=reconcile_seconds)
        while True:
            processed = loop.iterate()
            if once:
                return
            if not processed:
                time.sleep(poll_seconds)


def worker_count(value: str | None) -> int:
    """DETECTOR_WORKERS 값. 없으면 DEFAULT_WORKERS, 1~MAX_WORKERS 정수 문자열이 아니면 시작 실패(ValueError)."""
    if value is None:
        return DEFAULT_WORKERS
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= MAX_WORKERS:
        raise ValueError(f"invalid_detector_workers: 1~{MAX_WORKERS} 정수여야 한다")
    return int(value)


class WorkerFailed(RuntimeError):
    """작업자 스레드 하나가 예외로 끝났다. 남은 작업자를 멈춘 뒤 호출자에게 올려 프로세스를 실패로 끝낸다."""


def run_workers(database_url: str, root: Path | None, *, workers: int, poll_seconds: float = .25,
                reconcile_seconds: float = RECONCILE_SECONDS,
                fixed_refresh_seconds: float = FIXED_REFRESH_SECONDS,
                stop: threading.Event | None = None, join_seconds: float = 10.0) -> None:
    """판정 루프 workers개를 스레드로 돌린다. 작업자마다 연결이 따로다.

    - 시작 단계 prepare_active는 작업자를 띄우기 전에 한 번만 한다.
    - 방법 실행 슬롯(policy.configure_capacity)을 동시 판정 수 = workers + 1(직접 전달 몫)로 맞춘다. 슬롯이 동시 판정보다
      적으면 겹친 판정의 룰·모델이 capacity(미판정)로 끝난다(detection-decision.md D20).
    - dead 처리·정리는 0번 작업자만 reconcile_seconds마다 한다. 모든 작업자의 detection claim은 dead 처리 UPDATE를 건너뛴다.
    - 작업자 하나가 예외로 끝나면 traceback을 로그(stderr)에 남기고 stop을 세워 나머지를 멈추고(현재 반복을 끝낸 뒤,
      전체 최대 join_seconds 대기) WorkerFailed를 올린다. stop이 밖에서 세워지면 같은 방식으로 멈추고 정상 반환한다.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"invalid_detector_workers: 1~{MAX_WORKERS} 정수여야 한다")
    stop = stop or threading.Event()
    configure_capacity(workers + 1)
    with psycopg.connect(database_url) as conn:
        _prepare(conn, root)
    failures: list[tuple[int, BaseException]] = []
    base_id = f"detector-{uuid.uuid4()}"

    def work(index: int) -> None:
        try:
            with psycopg.connect(database_url) as conn:
                loop = _Loop(conn, root, f"{base_id}-{index}", reconcile=index == 0,
                             reconcile_seconds=reconcile_seconds, expire_in_claim=False,
                             fixed_refresh_seconds=fixed_refresh_seconds)
                while not stop.is_set():
                    if not loop.iterate():
                        stop.wait(poll_seconds)
        except BaseException as exc:  # noqa: BLE001 - 어떤 종료든 프로세스 실패로 올린다
            LOGGER.error("detector worker %s failed", index, exc_info=exc)
            failures.append((index, exc))
            stop.set()

    threads = [threading.Thread(target=work, args=(index,), name=f"detector-worker-{index}", daemon=True)
               for index in range(workers)]
    for thread in threads:
        thread.start()
    try:
        stop.wait()
    finally:
        stop.set()
        limit = time.monotonic() + join_seconds
        for thread in threads:
            thread.join(max(0.0, limit - time.monotonic()))
    if failures:
        index, exc = failures[0]
        raise WorkerFailed(f"detector_worker_failed:{index}") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    run(once=arguments.once)
