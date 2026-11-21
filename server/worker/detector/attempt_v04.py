"""v04 detect 작업의 시도 1회: 운영 상태 고정 → 방법 단위 실행 → 중간 판정 기록 → 최종 판정 저장."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
import uuid

import psycopg

import model_adapters
from detector.computation import load_model_context, model_reference
from detector.decision import decide, decision_columns, requested_mode
from detector.evidence_v4 import INTERIM_EVIDENCE_VERSION, build_evidence, build_interim_payload
from detector.finish import finish_detect_success
from detector.hard_rules import hard_rule_runners, unresolved_hard_result
from detector.method_results import ml_method_result, unresolved_result
from detector.method_runner import MethodExecution, MethodOutcome, MethodTask, execute_methods
from detector.operational_state import ActiveModel, PinnedState, pin_operational_state
from detector.policy import PolicyV2
from detector.protocol_rules import PROTOCOL_PATH_VERSION
from detector.relation_inputs import read_relation_inputs
from detector.relation_plan import RelationPlan, needs_relation_inputs, plan_relations
from detector.soft_rules import evaluate_soft_rule, in_scope, soft_rule_fields, unresolved_soft_result
from detector.source_latency import decision_source_time
from feature_fit import requires_fit
from feature_runtime import calculate_features, input_manifest_hash
from methods.registry import method_for_key
from telemetry import cost_family
from trainer.lease import lock_training_ownership

ML_RESULT_CODES = {"iforest": "IF_ANOMALY"}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Model:
    """모델 1개의 준비 결과. context가 없으면 load_error가 원인이다."""
    active: ActiveModel
    context: Mapping[str, Any] | None
    load_error: str | None = None


def load_models(conn: Any, state: PinnedState, source_id: uuid.UUID) -> list[_Model]:
    """모델 하나를 읽지 못해도 다른 방법의 결과는 지우지 않는다. 그 모델만 미판정이 된다(v04 시도·비교 평가 종합 공용)."""
    models = []
    for active in state.active_models:
        try:
            models.append(_Model(active, load_model_context(conn, active.config_version_id, active.model_key, source_id)))
        except ValueError as exc:
            models.append(_Model(active, None, str(exc)))
    return models


def _ml_unresolved(model: _Model, cause: str, *, status: str = "not_ready", failure_code: str | None = None) -> dict[str, Any]:
    method = method_for_key(model.active.model_key)
    settings = model.context["models"][0][5] if model.context else {}
    version = method.method_version if method else settings.get("methodVersion", "unknown")
    return unresolved_result(model.active.method_id, version, cause, status=status, failure_code=failure_code,
                             evidence=[{"reason": model.load_error}] if model.load_error else [],
                             modelVersionId=str(model.active.config_version_id), score=None, scoreDetail=None)


def _features(context: Mapping[str, Any], reference: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[dict, list]:
    definitions = context["definitions"]
    if any(requires_fit(item["expression"]) for item in definitions):
        return model_adapters.transform_iforest_features(reference, definitions, raw)
    return calculate_features(definitions, raw)


def _artifacts_present(row: tuple, root: Any) -> bool:
    if root is None or row[1] is None or row[3] is None:
        return False
    return all((Path(root) / path).is_file() for path in (row[1], row[3]))


def _run_model(model: _Model, raw: Mapping[str, Any], root: Any) -> MethodOutcome:
    """정상으로 바꾸지 않는다. 산출물이 없으면 not_configured, 있는데 맞지 않으면 artifact_invalid다."""
    active, context = model.active, model.context

    def unresolved(cause: str, **outcome: Any) -> MethodOutcome:
        return MethodOutcome(_ml_unresolved(model, cause, **outcome))

    method = method_for_key(active.model_key)
    if method is None or not method.executable_process or active.model_key not in ML_RESULT_CODES:
        return unresolved("not_executable")
    if context is None:
        return unresolved("not_configured")
    row = context["models"][0]
    if not _artifacts_present(row, root):
        return unresolved("not_configured")
    began = time.monotonic()
    try:
        reference = model_reference(context, root)
    except ValueError:
        return unresolved("failure", status="failed", failure_code="artifact_invalid")
    try:
        values, missing = _features(context, reference, raw)
    except model_adapters.ModelNotConfigured:
        return unresolved("not_configured")
    except (ArithmeticError, TypeError, ValueError):
        return unresolved("feature_unavailable")
    names = row[5].get("featureNames")
    if missing or not names or set(names) != set(values):
        return unresolved("feature_unavailable")
    scored = time.monotonic()
    try:
        score = method.adapter.score(reference, values)
    except model_adapters.ModelNotConfigured:
        return unresolved("not_configured")
    except ValueError:
        return unresolved("failure", status="failed", failure_code="invalid_score")
    return MethodOutcome(
        ml_method_result(active.method_id, method.method_version, str(active.config_version_id), score, row[6],
                         row[5].get("thresholdPolicy"), code=ML_RESULT_CODES[active.model_key]),
        {"preprocessMs": (scored - began) * 1000, "inferenceMs": (time.monotonic() - scored) * 1000,
         "features": (context, values, missing)})


def _tasks(state: PinnedState, models: list[_Model], raw: Mapping[str, Any], root: Any,
           relation: RelationPlan | None = None) -> list[MethodTask]:
    """순서: 하드룰(내장 목록 순 → 관계 → 프로토콜, relation_plan이 고른 것만) → 소프트룰(룰 세트 순) → ML(활성 집합 순).

    relation이 없으면 켜진 내장 하드룰 전부(관계 입력이 필요한 규칙이 없는 판정). 소프트룰은 scope가 판정 PROCESS와
    맞는 것만 과제로 만든다(relation-rules.md 4절 — 맞지 않으면 methodResults·n에서 뺀다).
    """
    fields = soft_rule_fields()
    versions = {rule.id: rule.method_version for rule in state.relation_rules}
    versions.update((rule.id, PROTOCOL_PATH_VERSION) for rule in state.protocol_rules)

    def timed(run: Callable[[], dict[str, Any]]) -> Callable[[], MethodOutcome]:
        def measure() -> MethodOutcome:
            began = time.monotonic()
            result = run()
            return MethodOutcome(result, {"ruleMs": (time.monotonic() - began) * 1000})
        return measure

    def hard_task(rule_id: str, run: Callable[[], dict[str, Any]]) -> MethodTask:
        return MethodTask(rule_id, "rule", timed(run),
                          lambda cause, **outcome: unresolved_hard_result(
                              rule_id, cause, method_version=versions.get(rule_id), **outcome))

    def soft_task(rule: Mapping[str, Any]) -> MethodTask:
        return MethodTask(rule["id"], "rule", timed(lambda: evaluate_soft_rule(rule, fields, raw)),
                          lambda cause, **outcome: unresolved_soft_result(rule, cause, **outcome))

    def ml_task(model: _Model) -> MethodTask:
        return MethodTask(model.active.method_id, "ml", lambda: _run_model(model, raw, root),
                          lambda cause, **outcome: _ml_unresolved(model, cause, **outcome))

    hard = hard_rule_runners(state.hard_rules, raw) if relation is None else hard_rule_runners(
        state.hard_rules, raw, relation.hard_ids, relation.inputs, relation.relation_results)
    return [*(hard_task(rule_id, run) for rule_id, run in hard),
            *(soft_task(rule) for rule in state.soft_rules if in_scope(rule, raw)),
            *(ml_task(model) for model in models)]


@dataclass(frozen=True)
class RowJudgment:
    """판정 1건의 방법 실행 결과. decided는 decide(methodResults·categoryResults·composite)이며 처음 읽을 때 계산한다
    (v04 시도는 build_evidence가 같은 decide를 부르므로 읽지 않는다 — 시도 경로의 계산·예외 순서가 그대로다)."""
    policy: PolicyV2
    execution: MethodExecution

    @cached_property
    def decided(self) -> dict[str, Any]:
        return decide(self.policy, self.execution.methods)


def judge_row(state: PinnedState, models: list[_Model], raw: Mapping[str, Any], root: Any,
              relation: RelationPlan | None, *, started_at: float,
              on_progress: Callable[[list[dict[str, Any]]], None] | None = None) -> RowJudgment:
    """판정 1건 = 방법 과제(_tasks) → execute_methods(기한·슬롯 부족·시간 초과 처리) → decide. DB를 읽거나 쓰지 않는다.

    입력은 호출자가 고정해 넘긴다: state(pin_operational_state·pin_fixed_state), models(load_models), raw(판정 관측
    원래 값), relation(relation_plan.plan_relations — 관계 입력 읽기와 보류·deferred 분류는 그 전에 끝남). 중간 판정
    기록은 하지 않는다. v04 시도만 on_progress로 InterimRecorder를 넘기고, 비교 평가는 넘기지 않는다.
    """
    execution = execute_methods(_tasks(state, models, raw, root, relation), state.policy.deadline_ms,
                                started_at=started_at, on_progress=on_progress)
    return RowJudgment(state.policy, execution)


class InterimRecorder:
    """종합이 처음 이상이 될 때와 근거가 늘 때만 기록한다. 기한을 넘기면 DB를 쓰지 않는다."""

    def __init__(self, conn: Any, state: PinnedState, message: Any, job_id: uuid.UUID, attempt_id: uuid.UUID,
                 attempt_token: uuid.UUID, *, started_at: float):
        self.conn, self.state, self.message = conn, state, message
        self.job_id, self.attempt_id, self.attempt_token = job_id, attempt_id, attempt_token
        self.started_at = started_at
        self.deadline = started_at + state.policy.deadline_ms / 1000
        self.basis: set[str] = set()

    def __call__(self, methods: list[dict[str, Any]]) -> None:
        composite = decide(self.state.policy, methods)["composite"]
        basis = set(composite["basisMethods"])
        if composite["anomalous"] is not True or basis <= self.basis:
            return
        payload = build_interim_payload(self.state.policy, methods, self.state.revisions,
                                        (time.monotonic() - self.started_at) * 1000)
        kind = "evidence_added" if self.basis else "first_anomaly"
        with self.conn.transaction():
            if not lock_training_ownership(self.conn, self.message, self.job_id, self.attempt_id, self.attempt_token):
                raise RuntimeError("detection_lease_lost")
            # 잠금을 기다리는 동안에도 기한이 지날 수 있어 쓰기 직전에 본다.
            if time.monotonic() > self.deadline:
                return
            self.conn.execute("""INSERT INTO run_decision_interims
                (id,job_id,attempt_id,kind,lease_token,evidence_version,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (uuid.uuid4(), self.job_id, self.attempt_id, kind, self.attempt_token, INTERIM_EVIDENCE_VERSION,
                 json.dumps(payload)))
        self.basis = basis


def _telemetry(execution: MethodExecution, observation: Any, *, started: float, queue_wait: float) -> dict[str, Any]:
    methods = execution.methods
    elapsed_ms = (time.monotonic() - started) * 1000
    durations = {"totalMs": elapsed_ms}
    for key in ("preprocessMs", "inferenceMs", "ruleMs"):
        # 룰은 한 슬롯에서 차례로 돌므로 합계가 곧 룰 계산 시간이다.
        values = [item[key] for item in execution.measured.values() if key in item]
        if values:
            durations[key] = sum(values)
    telemetry = cost_family(durations, batch_size=1, samples=[elapsed_ms], queue_wait=queue_wait)
    active_ml = any(method["category"] == "ml" for method in methods)
    telemetry["details"].update(scope="detector_process_revision", sourceClockStatus=observation.clock_status,
                                concurrency=2 if requested_mode(methods) == "parallel" else 1,
                                concurrencyScope="checker_callbacks", modelNJobs=2 if active_ml else None)
    return telemetry


_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_RECHECK_KEYS = frozenset({"recheck", "recheckedDecisionId", "rows"})
_RECHECK_ROW_KEYS = frozenset({"layer", "observation_id", "revision_no"})


def _recheck_shape(relation: Any) -> tuple[str, list[Mapping[str, Any]]]:
    """readiness.relation 모양(relation-rules.md 6.3). 어긋나면 저장소 이상이라 시도 실패."""
    invalid = ValueError("relation_recheck_reference_invalid")
    if (not isinstance(relation, Mapping) or set(relation) != _RECHECK_KEYS or type(relation["recheck"]) is not int
            or relation["recheck"] != 1 or not isinstance(relation["recheckedDecisionId"], str)
            or not _CANONICAL_UUID.fullmatch(relation["recheckedDecisionId"])):
        raise invalid
    rows = relation["rows"]
    if not isinstance(rows, list) or not rows or any(
            not isinstance(row, Mapping) or set(row) != _RECHECK_ROW_KEYS or row["layer"] != "T" or
            not isinstance(row["observation_id"], str) or not _CANONICAL_UUID.fullmatch(row["observation_id"]) or
            not (row["revision_no"] is None or type(row["revision_no"]) is int) for row in rows):
        raise invalid
    return relation["recheckedDecisionId"], rows


def recheck_reference(conn: Any, observation: Any, fixed: tuple) -> dict[str, Any] | None:
    """재판정 시도면 evidence `relationRecheck`, 첫 판정이면 None(relation-rules.md 6.3·6.4). manifest hash가 재판정 hash인데
    `readiness.relation`이 없어도 시도 실패다.

    DB는 readiness 안을 검사하지 않으므로(manifest 트리거는 readiness를 읽지 않음) 시도 시작 때 확인한다: manifest hash가
    재판정 hash인지, 재판정 대상 판정이 이 PROCESS 관측의 판정인지, 계기 거래 행마다 관측이 있고 `layer='T'`·같은 원본·
    판정 PROCESS와 같은 `transaction_id`·리비전 일치·`persisted_at` ≤ manifest `input_as_of`인지. 어긋나면 방법 결과 보류가
    아니라 시도 실패(`relation_recheck_reference_invalid`, fail-closed).
    """
    from detector.jobs import recheck_manifest_hash
    invalid = ValueError("relation_recheck_reference_invalid")
    readiness = fixed[5] if len(fixed) > 5 else None
    relation = readiness.get("relation") if isinstance(readiness, Mapping) else None
    recheck_hash = (recheck_manifest_hash(observation.source_id, observation.observation_id, observation.revision_no)
                    if type(observation.revision_no) is int else None)
    if not isinstance(readiness, Mapping) or "relation" not in readiness:
        if fixed[3] == recheck_hash:
            raise invalid  # 재판정 manifest인데 참조가 없다(readiness를 못 읽었거나 저장소 이상) — 첫 판정으로 돌리지 않는다
        return None
    decision_id, rows = _recheck_shape(relation)
    if fixed[3] != recheck_hash:
        raise invalid
    with conn.cursor() as cur:
        cur.execute("SELECT process_observation_id FROM run_process_decisions WHERE id=%s", (decision_id,))
        rechecked = cur.fetchone()
        if rechecked is None or str(rechecked[0]) != str(observation.observation_id):
            raise invalid
        cur.execute("""SELECT r.observation_id,o.layer::text,o.source_id,o.revision_no,o.persisted_at<=%s,
                t.transaction_id IS NOT NULL AND t.transaction_id=p.transaction_id
            FROM unnest(%s::uuid[]) AS r(observation_id)
            LEFT JOIN data_observations o ON o.id=r.observation_id
            LEFT JOIN data_transactions t ON t.observation_id=r.observation_id
            LEFT JOIN data_processes p ON p.observation_id=%s""",
                    (fixed[4], [row["observation_id"] for row in rows], observation.observation_id))
        found = {str(row[0]): row[1:] for row in cur.fetchall()}
    for row in rows:
        layer, source, revision, as_of, same_transaction = found.get(row["observation_id"], (None,) * 5)
        if (layer != "T" or str(source) != str(observation.source_id) or revision != row["revision_no"]
                or as_of is not True or same_transaction is not True):
            raise invalid
    # 계기 거래 행은 persisted_at 순(마지막 = 도착을 확정한 행, recheck_store)이다.
    return {"recheckedDecisionId": decision_id, "transactionObservationId": rows[-1]["observation_id"]}


def run_attempt(conn: Any, message: Any, job: Mapping[str, Any], attempt_id: uuid.UUID, attempt_token: uuid.UUID,
                observation: Any, fixed: tuple, root: Any, *, started: float, queue_wait: float, heartbeat: Any) -> None:
    """고정 입력 fixed = (input_spec, 구성, manifest id, manifest hash, input_as_of[, readiness]). 실패는 호출자가 시도 실패로
    처리한다(trainer `_finish` 실패 분기). 재판정 시도(readiness.relation)도 운영 상태·관계 입력을 이 시도 시작 때 다시
    고정한다(relation-rules.md 6.3). 성공 저장의 시도·작업 완료·ack는 detector/finish.py가 판정 INSERT와 한 묶음으로 쓴다."""
    if fixed[1] is not None or "configVersionId" in fixed[0]:
        raise ValueError("input_manifest_mismatch")
    recheck = recheck_reference(conn, observation, fixed)
    state = pin_operational_state(conn)
    models = load_models(conn, state, observation.source_id)
    # 관계 입력은 운영 상태 고정과 같은 트랜잭션에서 한 번 읽는다(relation-rules.md 3.2). 필요 없는 판정은 읽지 않는다.
    raw = observation.allowed_values
    relation = plan_relations(state, read_relation_inputs(conn, observation.source_id, observation.observation_id)
                              if needs_relation_inputs(state, raw) else None, raw)
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM data_observations WHERE id=%s", (observation.observation_id,))
        source_hash = cur.fetchone()[0]
        cur.execute("SELECT ingestion_method,dataset_key FROM ops_sources WHERE id=%s", (observation.source_id,))
        source_mode = cur.fetchone()
    conn.commit()
    recorder = InterimRecorder(conn, state, message, job["id"], attempt_id, attempt_token, started_at=started)
    execution = judge_row(state, models, observation.allowed_values, root, relation, started_at=started,
                          on_progress=recorder).execution
    methods = execution.methods
    if heartbeat.lost:
        raise RuntimeError("detection_lease_lost")
    telemetry = _telemetry(execution, observation, started=started, queue_wait=queue_wait)
    demo = None
    if source_mode and source_mode[0] == "demo-replay":
        demo = {"historicalReplay": True, "runKey": source_mode[1].removeprefix("demo-replay:"),
                "scope": "Process replay; source occurrence latency unavailable"}
    # 판정과 evidence 모두 state.hard_rules 같은 객체를 쓴다.
    evidence = build_evidence(state.policy, methods, state.revisions, state.hard_rules, source_id=observation.source_id,
                              telemetry=telemetry,
                              observation={"id": str(observation.observation_id), "revisionNo": observation.revision_no,
                                           "inputAsOf": fixed[4].isoformat()},
                              input_manifest_hash=fixed[3], demo=demo, input_origin=fixed[0].get("inputOrigin"),
                              evaluated_hard_ids=relation.hard_ids,
                              relation={**relation.evidence_fields(),
                                        **({"relationRecheck": recheck} if recheck is not None else {})})
    columns = decision_columns(evidence)
    # 판정 저장 소요(detection-decision.md 19절): 판정 쓰기 트랜잭션 진입 → COMMIT 완료, Detector 단조 시계.
    store_started = time.monotonic()
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("detection_lease_lost")
        with conn.cursor() as cur:
            for context, values, missing in (item["features"] for item in execution.measured.values() if "features" in item):
                # 이 캐시 키에는 모델·fit 식별이 없어 fit이 필요한 값은 남기지 않는다.
                if context["featureSetId"] and not any(requires_fit(item["expression"]) for item in context["definitions"]):
                    cur.execute("""INSERT INTO data_feature_materializations(observation_id,feature_set_id,
                        input_manifest_hash,values_json,missing_names) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)
                        ON CONFLICT DO NOTHING""", (observation.observation_id, context["featureSetId"],
                        input_manifest_hash(str(observation.observation_id), source_hash, context["featureHash"]),
                        json.dumps(values), json.dumps(missing)))
        # 원천 → 판정 지연(detection-decision.md 18절): 조건이 참일 때만 관측 원천 시각을 복사하고 measured.
        # decided_at과 지연은 같은 clock_timestamp() 한 번(MATERIALIZED CTE n)으로 계산한다. 음수도 그대로 measured.
        occurred = decision_source_time(observation, rechecked=recheck is not None)
        before = []
        if recheck is None:
            # 재판정은 측정 행을 넣지 않는다 [제안, relation-rules.md 6.4] — 같은 관측이 지연 집계에 두 번 들어가지 않게.
            # 판정 INSERT(잠금 81736) 앞에 둔다. detector 단계의 끝이 판정 INSERT·완료 문장 앞으로 조금 당겨진다.
            before.append(("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                VALUES(%s,%s,'detector',NULL,%s)""",
                (uuid.uuid4(), observation.observation_id, (time.monotonic() - started) * 1000)))
        before.append(("""WITH n AS MATERIALIZED (SELECT clock_timestamp() AS t)
            INSERT INTO run_process_decisions
            (id,execution_id,process_observation_id,config_version_id,input_manifest_hash,input_as_of,
            status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,
            source_log_occurred_at,source_latency_ms,latency_status,
            job_id,input_manifest_id,result_attempt_id,result_lease_token)
            VALUES(%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,(SELECT t FROM n),
            %s::timestamptz,extract(epoch FROM (SELECT t FROM n)-%s::timestamptz)*1000,
            CASE WHEN %s::timestamptz IS NULL THEN 'unavailable' ELSE 'measured' END::measurement_status,
            %s,%s,%s,%s)""",
            (uuid.uuid4(), observation.execution_id, observation.observation_id, fixed[3], fixed[4],
             columns["status"], columns["rule_status"], columns["ml_status"], columns["decision_code"],
             columns["anomaly_score"], json.dumps(evidence), occurred, occurred, occurred,
             job["id"], fixed[2], attempt_id, attempt_token)))
        # 측정·판정 INSERT와 시도·작업 완료·ack를 pipeline 한 묶음으로 보낸다(detector/finish.py — 잠금 구간 왕복 2회).
        finish_detect_success(conn, message, job, attempt_id,
                              {"status": columns["status"], "executionCost": telemetry, "checkers": evidence["checkers"]},
                              before)
    # 바깥 트랜잭션 블록이 끝나며 COMMIT됐다. 호출 전에 열린 트랜잭션이 있었다면(블록이 savepoint) 여기서 COMMIT한다.
    conn.commit()
    if recheck is None:
        record_judgment_store(conn, observation.observation_id, (time.monotonic() - store_started) * 1000)


def record_judgment_store(conn: Any, observation_id: uuid.UUID, duration_ms: float) -> bool:
    """판정 COMMIT 뒤 별도 짧은 트랜잭션으로 stage `judgment_store` 1행(detector 행과 같은 열 — 구성 NULL).

    실패해도 판정·작업은 이미 커밋됐으므로 시도를 실패로 바꾸지 않는다. 로그만 남기고 표본을 버린다.
    측정 표본은 장애 때 잃어도 되므로 비동기 커밋(판정마다 WAL flush 대기를 하나 더 만들지 않음).
    """
    try:
        with conn.transaction():
            conn.execute("SET LOCAL synchronous_commit = off")
            conn.execute("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                VALUES(%s,%s,'judgment_store',NULL,%s)""", (uuid.uuid4(), observation_id, duration_ms))
    except psycopg.Error as exc:
        LOGGER.warning("judgment_store measurement skipped for observation %s: %s", observation_id, type(exc).__name__)
        return False
    return True
