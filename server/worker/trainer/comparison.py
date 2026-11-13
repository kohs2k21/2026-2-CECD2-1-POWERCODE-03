"""평가 세트 생성(`evaluation_set`)과 같은 세트 위 모델 버전 비교(`compare`) v1 실행.

계약: `server/contracts/config-comparison-evaluation.md` "v1 구현 범위(11/13)". 저장: migration 011
(`data_evaluation_sets`·`run_config_comparisons`·`run_config_comparison_items`·`run_evaluations` 계보 열).

v1 범위: 합성 없는 세트만, 작업당 modelKey 1종(기준선 0..1개), 항목 평가는 evaluate와 같은 핵심
(`runner._version_context`·`runner._measure_version`)을 그대로 쓴다. 교체 추천·composite·methodResults는 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Mapping, Sequence

from methods.registry import method_for_key
from trainer.lease import lock_training_ownership
from trainer.model_version import read_schema_version
from trainer.metrics import measurement
from trainer.operational_state import ActiveModel, OperationalState, _policy
from telemetry import percentile

COMPARISON_CONTRACT_VERSION = "config-comparison-v1"
EVALUATION_SET_VERSION = "evaluation-set-v1"
# 세트 내용 hash의 행 형식 이름. 바꾸면 기존 세트의 hash와 비교할 수 없으므로 새 이름을 쓴다.
CONTENT_HASH_BASIS = "observation_execution_revision_content_label_v1"
MAX_COMPARISON_CONFIGS = 8
METRIC_VERSION = "selected-paths-v3"
NORMAL_LABELS_UNAVAILABLE = "verified_normal_labels_unavailable"
ANOMALY_LABELS_UNAVAILABLE = "verified_anomaly_labels_unavailable"
_HASH = re.compile(r"[0-9a-f]{64}")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}(?::[a-z][a-z0-9_:-]{0,99})*")


# ---------- 평가 세트 ----------

def _canonical(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def set_members(conn: Any, snapshot_id: Any, purpose: str) -> list[tuple[str, str | None, int | None, str, str | None]]:
    """세트 행의 식별 정보. `_snapshot_rows`와 같은 모집단·같은 정렬(observation_id)이다."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.observation_id, o.execution_id, o.revision_no, o.content_hash, m.label_evidence
                 FROM data_snapshot_members AS m
                 JOIN data_observations AS o ON o.id = m.observation_id
                WHERE m.snapshot_id = %s AND m.split::text = %s
                ORDER BY m.observation_id""",
            (snapshot_id, purpose),
        )
        rows = cur.fetchall()
    return [(str(observation), None if execution is None else str(execution), revision, content,
             evidence.get("label") if isinstance(evidence, Mapping) else None)
            for observation, execution, revision, content, evidence in rows]


def set_generation(members: Sequence[tuple[str, str | None, int | None, str, str | None]],
                   *, snapshot_id: Any, purpose: str) -> tuple[dict[str, Any], str]:
    """`generation`과 `content_hash`.

    content_hash = SHA-256(정규 JSON {basis, snapshotId, purpose, rows}) — rows는 observation_id 오름차순의
    [observationId, executionId, revisionNo, observation content_hash, label]. 산출물 파일이 없으므로(합성 없음)
    파일 hash 대신 이 값이다. memberHash = 같은 순서의 observationId 목록만의 SHA-256(라벨·내용 무관 모집단 식별).
    """
    ordered = sorted(members, key=lambda row: row[0])
    rows = [list(row) for row in ordered]
    content_hash = hashlib.sha256(_canonical({"basis": CONTENT_HASH_BASIS, "snapshotId": str(snapshot_id),
                                              "purpose": purpose, "rows": rows})).hexdigest()
    member_hash = hashlib.sha256(_canonical([row[0] for row in ordered])).hexdigest()
    labels = [row[4] for row in ordered]
    anomaly, normal = labels.count("anomaly"), labels.count("normal")
    generation = {"version": EVALUATION_SET_VERSION, "rowCount": len(rows), "originalRowCount": len(rows),
                  "syntheticRowCount": 0, "truthCount": anomaly, "normalLabelCount": normal,
                  "unlabeledCount": len(rows) - anomaly - normal, "memberHash": member_hash,
                  "contentHashBasis": CONTENT_HASH_BASIS, "labelSource": "manual_snapshot_evidence"}
    return generation, content_hash


def execute_evaluation_set(conn: Any, job: dict[str, Any], message: Any,
                           attempt_id: uuid.UUID, attempt_token: uuid.UUID) -> dict[str, Any]:
    from trainer.runner import _finish
    spec = job["input"]
    set_id = uuid.UUID(spec["evaluationSetId"])
    with conn.cursor() as cur:
        cur.execute("""SELECT e.status, e.source_id, e.snapshot_id, e.purpose::text, e.synthetic_request,
                              s.status::text, s.schema_version, s.source_id
                         FROM data_evaluation_sets AS e JOIN data_snapshots AS s ON s.id = e.snapshot_id
                        WHERE e.id = %s AND e.job_id = %s""", (set_id, job["id"]))
        row = cur.fetchone()
    if row is None or row[0] != "queued":
        raise ValueError("evaluation_set_not_queued")
    status, source_id, snapshot_id, purpose, synthetic, snapshot_status, schema, snapshot_source = row
    if (str(source_id), str(snapshot_id), purpose) != (spec.get("sourceId"), spec.get("snapshotId"), spec.get("purpose")):
        raise ValueError("evaluation_set_contract_mismatch")
    if synthetic is not None:
        raise ValueError("synthetic_set_unsupported")
    if schema == "prepared-features-v1":
        raise ValueError("prepared_not_comparable")
    if snapshot_status != "ready" or schema != "process-v1" or snapshot_source != source_id:
        raise ValueError("evaluation_set_snapshot_unavailable")
    members = set_members(conn, snapshot_id, purpose)
    conn.commit()
    if not members:
        raise ValueError("evaluation_set_empty")
    generation, content_hash = set_generation(members, snapshot_id=snapshot_id, purpose=purpose)
    result = {"evaluationSetId": str(set_id), "evaluationSetContractVersion": EVALUATION_SET_VERSION,
              "sourceId": str(source_id), "snapshotId": str(snapshot_id), "purpose": purpose,
              "status": "ready", "contentHash": content_hash, "synthetic": None, "generation": generation}
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        with conn.cursor() as cur:
            cur.execute("""UPDATE data_evaluation_sets SET status='ready', generation=%s::jsonb, content_hash=%s,
                                  ready_at=clock_timestamp()
                            WHERE id=%s AND job_id=%s AND status='queued'""",
                        (json.dumps(generation), content_hash, set_id, job["id"]))
            if cur.rowcount != 1:
                raise RuntimeError("evaluation_set_ownership_lost")
        _finish(conn, message, job, attempt_id, attempt_token, result, None, True)
    return {**result, "_finalized": True}


# ---------- 비교 입력 ----------

def _uuid(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise ValueError("invalid_job_input")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("invalid_job_input") from exc


def _revision(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid_job_input")
    return value


def parse_compare_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """API가 접수 시 고정한 `input_spec`을 판독한다. 형식 오류는 작업 실패(`invalid_job_input`)."""
    configs = spec.get("configVersionIds")
    if not isinstance(configs, list):
        raise ValueError("invalid_job_input")
    if not configs:
        raise ValueError("comparison_configs_required")
    ids = [_uuid(value) for value in configs]
    if len(set(ids)) != len(ids):
        raise ValueError("invalid_job_input")
    if len(ids) > MAX_COMPARISON_CONFIGS:
        raise ValueError("comparison_too_many_configs")
    baselines = spec.get("baselines")
    if not isinstance(baselines, list):
        raise ValueError("invalid_job_input")
    if len(baselines) > 1:
        raise ValueError("comparison_model_key_mixed")
    parsed_baselines = []
    for item in baselines:
        if not isinstance(item, Mapping) or not isinstance(item.get("modelKey"), str) or not item["modelKey"]:
            raise ValueError("invalid_job_input")
        config = _uuid(item.get("configVersionId"))
        if config not in ids:
            raise ValueError("invalid_job_input")
        parsed_baselines.append({"modelKey": item["modelKey"], "configVersionId": config})
    content_hash = spec.get("ruleSetContentHash")
    if not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash):
        raise ValueError("invalid_job_input")
    policy_revision = _revision(spec.get("detectionPolicyRevision"))
    # 운영 뷰와 같은 판독: 리비전 0의 NULL만 내장 기본 정책, 그 밖에는 형식 검사(값을 지어내지 않음).
    policy = _policy(policy_revision, spec.get("detectionPolicy"))
    active_revision = _revision(spec.get("activeModelSetRevision"))
    baseline = parsed_baselines[0]["configVersionId"] if parsed_baselines else None
    others = sorted((value for value in ids if value != baseline), key=str)
    return {"sourceId": _uuid(spec.get("sourceId")), "evaluationSetId": _uuid(spec.get("evaluationSetId")),
            # 활성 항목을 항상 먼저(계약 R12), 나머지는 문자열 정렬 순서.
            "order": ([baseline] if baseline else []) + others, "baseline": baseline,
            "baselines": parsed_baselines, "activeModelSetRevision": active_revision,
            "ruleSetRevision": _revision(spec.get("ruleSetRevision")), "ruleSetContentHash": content_hash,
            "detectionPolicyRevision": policy_revision, "detectionPolicy": dict(policy),
            # v3 항목 평가에 쓰는 운영 정책은 접수 시 고정값이다(시도 시작 판독 아님).
            "pinned": OperationalState(active_revision,
                                       tuple(ActiveModel(item["configVersionId"], item["modelKey"]) for item in parsed_baselines),
                                       policy_revision, policy)}


# ---------- 항목 결과 ----------

def _ratio(numerator: int | None, denominator: int | None, empty_reason: str) -> dict[str, Any]:
    if numerator is None or denominator is None:
        return {"value": None, "numerator": None, "denominator": None, "reason": empty_reason}
    return {"value": numerator / denominator if denominator else None, "numerator": numerator,
            "denominator": denominator, "reason": None if denominator else empty_reason}


def _unlabeled_null(reason: str) -> dict[str, Any]:
    """ML 결과가 없는 분기의 무라벨 지표 3종(null + 그 분기 사유)."""
    return {"predictedAnomalyCount": None, "predictedAnomalyRate": measurement(None, "ratio", reason=reason),
            "scoreQuantiles": {"p50": None, "p95": None, "p99": None, "count": 0, "reason": reason}}


def unlabeled_measures(measured_ml: Mapping[str, Any]) -> dict[str, Any]:
    """라벨 없이 셀 수 있는 ML 지표. 분모 = ML succeeded이고 anomalous가 bool인 행(`predictions`), coverage = 분모/모집단.
    점수 분위수는 그 행들의 점수에 `telemetry.percentile`(가장 가까운 두 순위 사이 선형 보간)."""
    predictions, scores = measured_ml["predictions"], measured_ml["scores"]
    population, denominator = measured_ml["populationCount"], len(predictions)
    anomalies = sum(1 for value in predictions.values() if value)
    rate = measurement(anomalies / denominator if denominator else None, "ratio", numerator=anomalies,
                       denominator=denominator, coverage=denominator / population if population else None,
                       reason=None if denominator else "no_scored_rows")
    quantiles = {"p50": percentile(scores, .5), "p95": percentile(scores, .95), "p99": percentile(scores, .99),
                 "count": len(scores), "reason": None if scores else "no_scored_rows"}
    return {"predictedAnomalyCount": anomalies, "predictedAnomalyRate": rate, "scoreQuantiles": quantiles}


def ml_category(measured_ml: Mapping[str, Any]) -> dict[str, Any]:
    """`categoryResults.ml`. 혼동행렬 칸 null 규칙(계약 결과 절): 세트에 normal 라벨이 없으면 fp·tn·precision·FPR은
    0이 아니라 null + `verified_normal_labels_unavailable`, anomaly 라벨이 없으면 tp·fn·recall(과 precision) null.
    무라벨 지표(`predictedAnomalyCount`·`predictedAnomalyRate`·`scoreQuantiles`)는 measured에서만 값이 있고
    skipped·unavailable은 null + 그 분기 사유(`ml_path_not_selected`·`ml_results_unavailable`)."""
    if not measured_ml["selected"]:
        return {"measurementStatus": "skipped", "methodCount": 1, "confusion": None, "unavailableCount": None,
                "completeOnly": None, "reason": "ml_path_not_selected", **_unlabeled_null("ml_path_not_selected")}
    labels, confusion = measured_ml["labelCounts"], measured_ml["confusion"]
    population = measured_ml["populationCount"]
    unavailable = population - measured_ml["succeededCount"]
    if not measured_ml["succeededCount"]:
        missing = {"value": None, "numerator": None, "denominator": None, "reason": "ml_results_unavailable"}
        return {"measurementStatus": "unavailable", "methodCount": 1, "confusion": None, "unavailableCount": unavailable,
                "completeOnly": {"recall": dict(missing), "precision": dict(missing), "falsePositiveRate": dict(missing)},
                **_unlabeled_null("ml_results_unavailable")}
    anomaly, normal = labels["anomaly"] > 0, labels["normal"] > 0
    cells = {"tp": confusion["tp"] if anomaly else None, "fp": confusion["fp"] if normal else None,
             "tn": confusion["tn"] if normal else None, "fn": confusion["fn"] if anomaly else None}
    recall = _ratio(cells["tp"], None if not anomaly else cells["tp"] + cells["fn"],
                    ANOMALY_LABELS_UNAVAILABLE if not anomaly else "no_scored_anomaly_rows")
    if not normal:
        precision = _ratio(None, None, NORMAL_LABELS_UNAVAILABLE)
    elif not anomaly:
        precision = _ratio(None, None, ANOMALY_LABELS_UNAVAILABLE)
    else:
        precision = _ratio(cells["tp"], cells["tp"] + cells["fp"], "no_predicted_anomalies")
    fpr = _ratio(cells["fp"], None if not normal else cells["fp"] + cells["tn"],
                 NORMAL_LABELS_UNAVAILABLE if not normal else "no_scored_normal_rows")
    return {"measurementStatus": "measured", "methodCount": 1, "confusion": cells, "unavailableCount": unavailable,
            "completeOnly": {"recall": recall, "precision": precision, "falsePositiveRate": fpr},
            **unlabeled_measures(measured_ml)}


def succeeded_item(config_id: uuid.UUID, model_key: str, role: str, evaluation_id: uuid.UUID,
                   measured: Mapping[str, Any]) -> dict[str, Any]:
    summary = measured["summary"]
    evidence = measured["policyEvidence"]
    # 항목 평가에 실제 쓴 정책. v3는 접수 고정 운영 정책(ML만), v1·v2는 구성에 저장된 정책·룰이다(evaluate와 같음).
    applied = {"source": "accepted" if "detectionPolicyRevision" in evidence else "configuration",
               "detectionPolicy": evidence["detectionPolicy"], "ruleCount": measured["metrics"].get("ruleCount")}
    return {"configVersionId": str(config_id), "modelKey": model_key, "role": role, "status": "succeeded",
            "errorCode": None, "evaluationId": str(evaluation_id), "metricVersion": METRIC_VERSION,
            "evaluationPolicy": applied,
            "applicability": summary["applicability"], "reasonCodes": list(summary["reasonCodes"]),
            "counts": {**{key: summary[key] for key in ("populationCount", "scoredCount", "unavailableCount",
                                                        "unlabeledCount", "excludedCount")},
                       "excludedReasons": dict(summary["excludedReasons"])},
            "categoryResults": {"ml": ml_category({**measured["ml"], "populationCount": summary["populationCount"]})},
            # `_measure_version`의 `cost_family` 결과 그대로(평가한 행 기준, 제외 행 미포함). 실패 항목에는 없다.
            "executionCost": measured["executionCost"]}


def failed_item(config_id: uuid.UUID, model_key: str | None, role: str, error_code: str) -> dict[str, Any]:
    return {"configVersionId": str(config_id), "modelKey": model_key, "role": role, "status": "failed",
            "errorCode": error_code, "evaluationId": None}


AGREEMENT_INTERPRETATION = "checker_agreement_not_accuracy"


def pairwise_agreement(predictions: Sequence[tuple[str, Mapping[str, bool]]], row_count: int) -> list[dict[str, Any]]:
    """성공 항목 쌍별 ML 판정 일치(`pairwiseAgreement`). `predictions` = 항목 순서(기준선 먼저, 나머지 id 순)의
    (configVersionId, observation id → ML 판정). 비교 가능 행 = 두 항목 모두 ML succeeded + bool 판정인 행(교집합)이라
    항목마다 제외·미판정 행이 달라도 같은 행끼리만 센다. 라벨과 무관한 판정 일치이며 정확도가 아니다."""
    pairs = []
    for index, (a, left) in enumerate(predictions):
        for b, right in predictions[index + 1:]:
            cells = {"both": 0, "aOnly": 0, "bOnly": 0, "neither": 0}
            common = left.keys() & right.keys()
            for key in common:
                cells["both" if left[key] and right[key] else "aOnly" if left[key] else
                      "bOnly" if right[key] else "neither"] += 1
            comparable = len(common)
            agree = cells["both"] + cells["neither"]
            pairs.append({"a": a, "b": b, "comparableCount": comparable, **cells,
                          "agreementRate": measurement(agree / comparable if comparable else None, "ratio",
                                                       numerator=agree, denominator=comparable,
                                                       coverage=comparable / row_count if row_count else None,
                                                       reason=None if comparable else "no_comparable_rows"),
                          "interpretation": AGREEMENT_INTERPRETATION})
    return pairs


def build_summary(*, comparison_id: uuid.UUID, plan: Mapping[str, Any], evaluation_set: Mapping[str, Any],
                  items: Sequence[Mapping[str, Any]],
                  pairwise: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    from trainer.service_contract import CONTRACT_VERSION
    return {"serviceContractVersion": CONTRACT_VERSION, "comparisonContractVersion": COMPARISON_CONTRACT_VERSION,
            "comparisonId": str(comparison_id),
            "evaluationSet": {"id": str(evaluation_set["id"]), "contentHash": evaluation_set["contentHash"],
                              "purpose": evaluation_set["purpose"], "rowCount": evaluation_set["generation"]["rowCount"],
                              "originalRowCount": evaluation_set["generation"]["originalRowCount"],
                              "syntheticRowCount": evaluation_set["generation"]["syntheticRowCount"], "synthetic": False},
            "baselines": [{"modelKey": item["modelKey"], "configVersionId": str(item["configVersionId"])}
                          for item in plan["baselines"]],
            "activeModelSetRevision": plan["activeModelSetRevision"],
            "ruleSet": {"revision": plan["ruleSetRevision"], "contentHash": plan["ruleSetContentHash"], "fixedAt": "accepted"},
            "detectionPolicy": {"revision": plan["detectionPolicyRevision"], "policy": plan["detectionPolicy"],
                                "fixedAt": "accepted"},
            "manualApprovalRequired": True,
            "replacementRecommendations": None, "replacementRecommendationsReason": "not_implemented_v1",
            "itemCount": len(items), "succeededCount": sum(item["status"] == "succeeded" for item in items),
            "failedCount": sum(item["status"] == "failed" for item in items),
            "items": list(items), "pairwiseAgreement": list(pairwise)}


# ---------- 비교 실행 ----------

def _item_code(prefix: str, exc: BaseException) -> str:
    from trainer.runner import _terminal_error_code
    code = f"{prefix}:{_terminal_error_code(exc)}"
    return code if _ERROR_CODE.fullmatch(code) else f"{prefix}:invalid_job_input"


def _config_rows(conn: Any, config_id: uuid.UUID) -> tuple[Any, list[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT source_id, configuration_spec FROM ml_config_versions WHERE id=%s", (config_id,))
        config = cur.fetchone()
        cur.execute("SELECT model_key FROM ml_config_models WHERE config_version_id=%s ORDER BY model_key", (config_id,))
        keys = [row[0] for row in cur.fetchall()]
    return config, keys


def training_overlap(conn: Any, training_snapshot_id: Any, snapshot_id: Any, purpose: str) -> bool:
    """버전의 학습 스냅샷 train과 세트 행이 observation 또는 execution 단위로 겹치는가
    (`synthetic_execution.evaluate`의 두 검사와 같은 규칙, 행 수 상한 없이 SQL 한 문장).
    execution_id가 NULL인 관측은 등호 조인에서 빠지므로 observation 일치만으로 판정된다."""
    with conn.cursor() as cur:
        cur.execute('''SELECT EXISTS(
            SELECT 1 FROM data_snapshot_members tr
            JOIN data_snapshot_members te ON te.observation_id=tr.observation_id
            WHERE tr.snapshot_id=%s AND tr.split='train' AND te.snapshot_id=%s AND te.split::text=%s)
          OR EXISTS(
            SELECT 1 FROM data_snapshot_members tr JOIN data_observations tro ON tro.id=tr.observation_id
            JOIN data_observations ev ON ev.execution_id=tro.execution_id
            JOIN data_snapshot_members te ON te.observation_id=ev.id
            WHERE tr.snapshot_id=%s AND tr.split='train' AND te.snapshot_id=%s AND te.split::text=%s)''',
                    (training_snapshot_id, snapshot_id, purpose, training_snapshot_id, snapshot_id, purpose))
        return bool(cur.fetchone()[0])


def check_item(conn: Any, config_id: uuid.UUID, source_id: uuid.UUID, snapshot_id: Any,
               purpose: str) -> tuple[str | None, str | None]:
    """항목 실행 전 검사. (modelKey, 실패 코드) — 실패 코드가 None이면 평가로 넘어간다. 순서가 곧 우선순위다."""
    config, keys = _config_rows(conn, config_id)
    if config is None:
        return None, "config_not_found"
    model_key = keys[0] if len(keys) == 1 else None
    if config[0] != source_id:
        return model_key, "config_source_mismatch"
    if len(keys) != 1:
        return None, "comparison_config_not_comparable"
    spec = config[1] if isinstance(config[1], Mapping) else {}
    try:
        read_schema_version(spec)
    except ValueError:
        return model_key, "comparison_config_not_comparable"
    try:
        training_snapshot = uuid.UUID(spec.get("trainingSnapshotId"))
    except (TypeError, ValueError, AttributeError):
        training_snapshot = None
    training_schema = None
    if training_snapshot is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT schema_version FROM data_snapshots WHERE id=%s", (training_snapshot,))
            training_schema = cur.fetchone()
    if spec.get("preparedInputContract") is not None or spec.get("preparedDatasetId") is not None or \
            (training_schema is not None and training_schema[0] == "prepared-features-v1"):
        return model_key, "prepared_not_comparable"
    method = method_for_key(model_key)
    if method is None or not method.executable_process:
        code = f"method_not_executable:ml:{model_key}"
        return model_key, code if _ERROR_CODE.fullmatch(code) else "method_not_executable"
    if training_schema is None:
        # 겹침을 확인할 수 없으면 평가하지 않는다(겹치지 않는다고 가정하지 않음).
        return model_key, "evaluation_failed:training_snapshot_unavailable"
    if training_overlap(conn, training_snapshot, snapshot_id, purpose):
        return model_key, "training_evaluation_overlap"
    return model_key, None


def _load_set(conn: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("""SELECT source_id, snapshot_id, purpose::text, status, content_hash, generation, synthetic_request
                         FROM data_evaluation_sets WHERE id=%s""", (plan["evaluationSetId"],))
        row = cur.fetchone()
    if row is None or row[3] != "ready":
        raise ValueError("evaluation_set_not_ready")
    if row[0] != plan["sourceId"]:
        raise ValueError("evaluation_set_source_mismatch")
    if row[6] is not None:
        raise ValueError("synthetic_set_unsupported")
    return {"id": plan["evaluationSetId"], "snapshotId": row[1], "purpose": row[2], "contentHash": row[4],
            "generation": row[5]}


def _check_rule_set(conn: Any, plan: Mapping[str, Any]) -> None:
    """v1 항목 평가는 룰 세트를 적용하지 않는다(v3 = ML만, v1·v2 = 구성 저장 룰). 고정 리비전은 기록용이며
    저장소의 그 리비전 hash와 같은지만 확인한다 — 다르면 접수 값이 잘못된 것이므로 작업을 끝낸다."""
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM ops_rule_set_revisions WHERE revision=%s", (plan["ruleSetRevision"],))
        row = cur.fetchone()
    if row is None or row[0] != plan["ruleSetContentHash"]:
        raise ValueError("rule_set_revision_mismatch")


def execute_compare(conn: Any, job: dict[str, Any], root: Any, message: Any,
                    attempt_id: uuid.UUID, attempt_token: uuid.UUID) -> dict[str, Any]:
    from trainer import runner
    queue_wait = runner.queue_wait_ms(conn, message, attempt_id)
    plan = parse_compare_spec(job["input"])
    evaluation_set = _load_set(conn, plan)
    _check_rule_set(conn, plan)
    snapshot_id, purpose = evaluation_set["snapshotId"], evaluation_set["purpose"]
    members = set_members(conn, snapshot_id, purpose)
    rows = runner._snapshot_rows(conn, snapshot_id, purpose)
    conn.commit()
    _, content_hash = set_generation(members, snapshot_id=snapshot_id, purpose=purpose)
    if not rows or content_hash != evaluation_set["contentHash"] or [str(row[0]) for row in rows] != [row[0] for row in members]:
        raise ValueError("evaluation_set_changed")
    expected_key = plan["baselines"][0]["modelKey"] if plan["baselines"] else None
    comparison_id = uuid.uuid4()
    items: list[dict[str, Any]] = []
    evaluations: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any], dict[str, Any]]] = []
    # 성공 항목의 행별 ML 판정(작업 메모리에만, DB에 쓰지 않음) — pairwiseAgreement용.
    predictions: list[tuple[str, Mapping[str, bool]]] = []
    for config_id in plan["order"]:
        role = "active" if config_id == plan["baseline"] else "candidate"
        model_key, error = check_item(conn, config_id, plan["sourceId"], snapshot_id, purpose)
        conn.commit()
        if error is None and model_key is not None:
            # 작업당 modelKey 1종(v1). API 검증을 지난 뒤 바뀔 수 없는 값이라 어긋나면 작업 자체를 끝낸다.
            if expected_key is None:
                expected_key = model_key
            elif model_key != expected_key:
                raise ValueError("comparison_model_key_mixed")
        if error in ("config_not_found", "config_source_mismatch") and role == "active":
            # 기준선 열(FK·같은 source 트리거)을 채울 수 없다. 접수 뒤 생길 수 없는 상태라 작업을 같은 코드로 끝낸다.
            raise ValueError(error)
        if error is not None:
            items.append(failed_item(config_id, model_key, role, error))
            continue
        try:
            context, operational = runner._version_context(conn, config_id, plan["sourceId"], pinned=plan["pinned"])
            conn.commit()
            measured = runner._measure_version(
                context, operational, rows, root, started=time.monotonic(), queue_wait=queue_wait,
                provenance={"sourceId": str(plan["sourceId"]), "snapshotId": str(snapshot_id),
                            "configVersionId": str(config_id), "purpose": purpose,
                            "evaluationSetId": str(evaluation_set["id"]), "comparisonId": str(comparison_id)})
        except ValueError as exc:
            conn.rollback()
            items.append(failed_item(config_id, model_key, role, _item_code("evaluation_failed", exc)))
            continue
        evaluation_id = uuid.uuid4()
        evaluation_spec = {**runner._evaluation_spec(measured, METRIC_VERSION, purpose),
                           "evaluationSetId": str(evaluation_set["id"]), "evaluationSetContentHash": content_hash}
        evaluations.append((evaluation_id, config_id, evaluation_spec, measured))
        items.append(succeeded_item(config_id, model_key, role, evaluation_id, measured))
        predictions.append((str(config_id), measured["ml"]["predictions"]))
    succeeded = any(item["status"] == "succeeded" for item in items)
    summary = build_summary(comparison_id=comparison_id, plan=plan, evaluation_set=evaluation_set, items=items,
                            pairwise=pairwise_agreement(predictions, evaluation_set["generation"]["rowCount"]))
    with conn.transaction():
        if not lock_training_ownership(conn, message, job["id"], attempt_id, attempt_token):
            raise RuntimeError("training_ownership_lost")
        with conn.cursor() as cur:
            # 011 삽입 순서: 비교 → 평가 행 → 항목. 전부 실패여도 항목 사유를 남기려고 status='failed' 묶음을 쓴다.
            cur.execute("""INSERT INTO run_config_comparisons
                             (id, job_id, evaluation_set_id, source_id, baseline_config_version_id, active_revision,
                              status, summary, completed_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, clock_timestamp())""",
                        (comparison_id, job["id"], evaluation_set["id"], plan["sourceId"], plan["baseline"],
                         plan["activeModelSetRevision"], "succeeded" if succeeded else "failed", json.dumps(summary)))
            for evaluation_id, config_id, evaluation_spec, measured in evaluations:
                runner._insert_evaluation(cur, evaluation_id=evaluation_id, job_id=job["id"], config_id=config_id,
                                          snapshot_id=snapshot_id, purpose=purpose, metric_version=METRIC_VERSION,
                                          evaluation_spec=evaluation_spec, measured=measured,
                                          comparison_id=comparison_id, evaluation_set_id=evaluation_set["id"])
            for item in items:
                if item["errorCode"] == "config_not_found":
                    continue  # 항목 표는 ml_config_versions FK라 없는 버전은 summary.items에만 남는다.
                cur.execute("""INSERT INTO run_config_comparison_items
                                 (comparison_id, config_version_id, role, status, error_code, evaluation_id)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (comparison_id, item["configVersionId"], item["role"], item["status"], item["errorCode"],
                             item["evaluationId"]))
        if succeeded:
            runner._finish(conn, message, job, attempt_id, attempt_token, summary, None, True)
        else:
            runner._finish(conn, message, job, attempt_id, attempt_token, None, "comparison_all_items_failed", True,
                           failure_summary=summary)
    return {**summary, "_finalized": True}
