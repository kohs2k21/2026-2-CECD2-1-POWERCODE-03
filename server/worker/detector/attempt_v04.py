"""v04 detect 작업의 시도 1회: 운영 상태 고정 → 방법 단위 실행 → 중간 판정 기록(prepare_attempt) → 최종 판정 저장(save_attempt 한 건, save_batch 여러 건)."""
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
from detector.finish import BatchItem, BatchRejected, finish_detect_batch, finish_detect_success
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


@dataclass
class PendingSave:
    """판정·evidence까지 끝내고 저장만 남은 v04 시도 1건(prepare_attempt 결과). 임대 갱신(heartbeat) 정지는 호출자 몫이다.

    telemetry는 evidence["telemetry"]와 같은 객체다. 저장 문장을 만들 때(_success_statements) 묶음 크기 키를 넣거나 뺀다.
    """
    message: Any
    job: Mapping[str, Any]
    attempt_id: uuid.UUID
    attempt_token: uuid.UUID
    observation: Any
    fixed: tuple
    started: float
    execution: MethodExecution
    source_hash: Any
    recheck: dict[str, Any] | None
    telemetry: dict[str, Any]
    evidence: dict[str, Any]
    columns: dict[str, Any]
    heartbeat: Any


# 여러 건을 한 트랜잭션으로 저장한 판정의 evidence `telemetry.details` 키(detection-decision.md 19절). 값 = 그 트랜잭션의
# 판정 수(2 이상). 한 건씩 저장한 판정(묶음 실패 뒤 한 건씩 다시 저장한 판정 포함)에는 키를 두지 않는다.
# `batchSize`(처리량 throughput의 행 수 — 판정은 늘 1)와 다른 값이다.
STORE_BATCH_KEY = "judgmentStoreBatchSize"


def prepare_attempt(conn: Any, message: Any, job: Mapping[str, Any], attempt_id: uuid.UUID, attempt_token: uuid.UUID,
                    observation: Any, fixed: tuple, root: Any, *, started: float, queue_wait: float,
                    heartbeat: Any) -> PendingSave:
    """run_attempt의 저장 앞 단계. 고정 입력 fixed = (input_spec, 구성, manifest id, manifest hash, input_as_of[, readiness]).

    고정 입력 확인 → 재판정 참조 확인 → 운영 상태 고정 → 방법 실행(중간 판정 기록) → evidence·판정 열. 판정 행·작업 완료는
    쓰지 않는다(save_attempt·save_batch 몫). 실패는 지금처럼 예외로 올리고 호출자가 시도 실패로 처리한다(trainer `_finish`
    실패 분기). 재판정 시도(readiness.relation)도 운영 상태·관계 입력을 이 시도 시작 때 다시 고정한다(relation-rules.md 6.3).
    """
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
    return PendingSave(message, job, attempt_id, attempt_token, observation, fixed, started, execution, source_hash,
                       recheck, telemetry, evidence, columns, heartbeat)


def _feature_statements(pending: PendingSave) -> list[tuple[str, tuple[Any, ...]]]:
    """변수 캐시 INSERT 문장. 이 캐시 키에는 모델·fit 식별이 없어 fit이 필요한 값은 남기지 않는다."""
    observation = pending.observation
    statements = []
    for context, values, missing in (item["features"] for item in pending.execution.measured.values()
                                     if "features" in item):
        if context["featureSetId"] and not any(requires_fit(item["expression"]) for item in context["definitions"]):
            statements.append(("""INSERT INTO data_feature_materializations(observation_id,feature_set_id,
                        input_manifest_hash,values_json,missing_names) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)
                        ON CONFLICT DO NOTHING""", (observation.observation_id, context["featureSetId"],
                        input_manifest_hash(str(observation.observation_id), pending.source_hash, context["featureHash"]),
                        json.dumps(values), json.dumps(missing))))
    return statements


def _success_statements(pending: PendingSave, store_batch_size: int | None = None
                        ) -> tuple[tuple[str, tuple[Any, ...]] | None, tuple[str, tuple[Any, ...]], dict[str, Any]]:
    """(detector 측정 INSERT 또는 None(재판정), 판정 INSERT, 작업 result_summary). 측정 소요는 이 함수를 부른 시각까지다.
    한 건 저장(save_attempt)은 소유권 확인·변수 캐시 INSERT 뒤, 측정·판정 INSERT를 보내기 직전이다. 묶음 저장(save_batch)은
    묶음 트랜잭션에 들어간 직후 소유권 확인(첫 묶음 1단계) 전이라, 앞선 건의 판정을 기다린 시간은 detector 단계에 들고
    소유권 확인·변수 캐시 INSERT 시간은 들지 않는다.

    store_batch_size: 여러 건을 한 트랜잭션으로 저장하면 그 판정 수를 telemetry.details[STORE_BATCH_KEY]에 넣고, None(한 건
    저장)이면 키를 뺀다. evidence·result_summary가 같은 telemetry 객체를 쓰므로 둘 다 같은 값이다.
    """
    observation, fixed, recheck, columns, job = (pending.observation, pending.fixed, pending.recheck, pending.columns,
                                                 pending.job)
    details = pending.telemetry.get("details")
    if isinstance(details, dict):
        if store_batch_size is None:
            details.pop(STORE_BATCH_KEY, None)
        else:
            details[STORE_BATCH_KEY] = store_batch_size
    # 원천 → 판정 지연(detection-decision.md 18절): 조건이 참일 때만 관측 원천 시각을 복사하고 measured.
    # decided_at과 지연은 같은 clock_timestamp() 한 번(MATERIALIZED CTE n)으로 계산한다. 음수도 그대로 measured.
    occurred = decision_source_time(observation, rechecked=recheck is not None)
    measurement = None
    if recheck is None:
        # 재판정은 측정 행을 넣지 않는다 [제안, relation-rules.md 6.4] — 같은 관측이 지연 집계에 두 번 들어가지 않게.
        # 판정 INSERT(잠금 81736) 앞에 둔다. detector 단계의 끝이 판정 INSERT·완료 문장 앞으로 조금 당겨진다.
        measurement = ("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                VALUES(%s,%s,'detector',NULL,%s)""",
                       (uuid.uuid4(), observation.observation_id, (time.monotonic() - pending.started) * 1000))
    decision = ("""WITH n AS MATERIALIZED (SELECT clock_timestamp() AS t)
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
                 columns["anomaly_score"], json.dumps(pending.evidence), occurred, occurred, occurred,
                 job["id"], fixed[2], pending.attempt_id, pending.attempt_token))
    result = {"status": columns["status"], "executionCost": pending.telemetry, "checkers": pending.evidence["checkers"]}
    return measurement, decision, result


def save_attempt(conn: Any, pending: PendingSave) -> None:
    """한 건 저장(run_attempt의 저장 단계). 실패는 예외로 올리고 호출자가 시도 실패로 처리한다."""
    message, job = pending.message, pending.job
    # 판정 저장 소요(detection-decision.md 19절): 판정 쓰기 트랜잭션 진입 → COMMIT 완료, Detector 단조 시계.
    store_started = time.monotonic()
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], pending.attempt_id, pending.attempt_token):
            raise RuntimeError("detection_lease_lost")
        with conn.cursor() as cur:
            for sql, params in _feature_statements(pending):
                cur.execute(sql, params)
        measurement, decision, result = _success_statements(pending)
        # 측정·판정 INSERT와 시도·작업 완료·ack를 pipeline 한 묶음으로 보낸다(detector/finish.py — 잠금 구간 왕복 2회).
        finish_detect_success(conn, message, job, pending.attempt_id, result,
                              [*([measurement] if measurement is not None else []), decision])
    # 바깥 트랜잭션 블록이 끝나며 COMMIT됐다. 호출 전에 열린 트랜잭션이 있었다면(블록이 savepoint) 여기서 COMMIT한다.
    conn.commit()
    if pending.recheck is None:
        record_judgment_store(conn, pending.observation.observation_id, (time.monotonic() - store_started) * 1000)


def run_attempt(conn: Any, message: Any, job: Mapping[str, Any], attempt_id: uuid.UUID, attempt_token: uuid.UUID,
                observation: Any, fixed: tuple, root: Any, *, started: float, queue_wait: float, heartbeat: Any) -> None:
    """시도 1회 = prepare_attempt(판정) → save_attempt(한 건 저장). 성공 저장의 시도·작업 완료·ack는 detector/finish.py가
    판정 INSERT와 한 묶음으로 쓴다. 여러 건을 한 트랜잭션으로 저장하는 경로는 save_batch(runner 다중 작업자)다."""
    save_attempt(conn, prepare_attempt(conn, message, job, attempt_id, attempt_token, observation, fixed, root,
                                       started=started, queue_wait=queue_wait, heartbeat=heartbeat))


def save_batch(conn: Any, pendings: list[PendingSave]) -> bool:
    """첫 판정 여러 건(2건 이상, 재판정 없음)을 트랜잭션 하나(pipeline 두 묶음)로 저장하고 COMMIT을 한 번 한다.

    전역 잠금 81736 획득·COMMIT(WAL flush)이 판정마다가 아니라 묶음마다 한 번이다. 문장·순서는 detector/finish.py
    finish_detect_batch(판정 INSERT는 pendings 순서 = 임대 순서라 판정 이벤트도 그 순서다).
    - 성공: True. COMMIT 뒤 judgment_store 행을 판정마다 1행씩 한 트랜잭션으로 쓴다. 값은 모두 이 묶음 저장 소요(진입 →
      COMMIT 완료)이고, 묶음 크기는 각 판정 evidence `telemetry.details.judgmentStoreBatchSize`에 남는다.
    - 실패(소유권 행 없음·ack 0행·DB 오류·그 밖의 예외): 묶음 전체를 ROLLBACK하고 False(아무것도 남지 않음). 호출자가
      각 건을 save_attempt(한 건 저장)로 다시 저장하므로 문제가 있는 건만 지금과 같은 원인 코드로 실패·유실된다.
    """
    if len(pendings) < 2 or any(item.recheck is not None for item in pendings):
        raise ValueError("save_batch_requires_first_decisions")
    size = len(pendings)
    # 판정 저장 소요(19절): 묶음 트랜잭션 진입 → COMMIT 완료. 묶음 안 판정마다 같은 값이다.
    store_started = time.monotonic()
    try:
        with conn.transaction():
            items = []
            for item in pendings:
                measurement, decision, result = _success_statements(item, size)
                items.append(BatchItem(item.message, item.job, item.attempt_id, item.attempt_token,
                                       _feature_statements(item), measurement, decision, result))
            finish_detect_batch(conn, items)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 묶음 전체를 되돌리고 한 건씩 저장으로 넘긴다
        if isinstance(exc, (psycopg.Error, BatchRejected)):
            LOGGER.warning("detector batch store of %s decisions rolled back; saving one by one: %s", size,
                           type(exc).__name__)
        else:
            # DB 오류·확인 실패가 아닌 예외는 묶음 경로의 코드 결함일 수 있어 traceback을 남긴다(한 건씩 저장은 그대로 한다).
            LOGGER.error("detector batch store of %s decisions failed unexpectedly; saving one by one", size,
                         exc_info=exc)
        conn.rollback()
        return False
    record_judgment_stores(conn, [item.observation.observation_id for item in pendings],
                           (time.monotonic() - store_started) * 1000)
    return True


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


def record_judgment_stores(conn: Any, observation_ids: list[uuid.UUID], duration_ms: float) -> bool:
    """묶음 저장(save_batch) COMMIT 뒤 record_judgment_store를 판정 수만큼 한 트랜잭션·한 문장으로 한다.

    행마다 record_judgment_store와 같은 열·값 규칙이다(stage `judgment_store`, 구성 NULL, duration_ms = 묶음 저장 소요로 모두
    같음). 같은 비동기 커밋, 같은 실패 처리(DB 오류면 경고 로그만 남기고 묶음의 표본을 모두 버림, 판정·작업은 그대로).
    """
    try:
        with conn.transaction():
            conn.execute("SET LOCAL synchronous_commit = off")
            conn.execute("""INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms)
                SELECT u.id,u.observation_id,'judgment_store',NULL,%s
                  FROM unnest(%s::uuid[],%s::uuid[]) AS u(id,observation_id)""",
                         (duration_ms, [uuid.uuid4() for _ in observation_ids], list(observation_ids)))
    except psycopg.Error as exc:
        LOGGER.warning("judgment_store measurements skipped for %s observations: %s", len(observation_ids),
                       type(exc).__name__)
        return False
    return True
