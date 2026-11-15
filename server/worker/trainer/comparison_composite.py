"""비교 항목의 룰+모델 종합(`composite`) v1 — config-comparison-evaluation.md "종합(`composite`) v1" 소절.

판정은 Detector 함수를 그대로 부른다: 고정 리비전 판독·검사 `operational_state.pin_fixed_state`, 모델 준비
`attempt_v04.load_models`, 관계 입력 `relation_inputs.read_relation_inputs`·`relation_plan.plan_relations`, 행 판정
`attempt_v04.judge_row`(방법 실행 + `decision.decide`). 여기서는 행별 종합 `status`·`anomalous`와 라벨로 칸을 센다
(판정 로직을 다시 구현하지 않는다). 행별 판정은 작업 메모리에만 두고 저장하지 않는다.
"""
from __future__ import annotations

import re
import time
from typing import Any, Mapping, Sequence

from trainer.metrics import measurement

METRIC_VERSION = "comparison-composite-v1"
UNAVAILABLE_PREFIX = "composite_unavailable:"
K_EXCEEDS_SINGLE_MODEL = "policy_k_exceeds_single_model"
ALL_ROWS_EXCLUDED = "all_rows_excluded"
# Detector 오류 문구가 코드 형식이 아닐 때만 쓴다(문구를 그대로 노출하지 않음).
PREPARATION_FAILED = "composite_preparation_failed"
DECIDABLE_BIAS_NOTE = "partial_true_included_none_excluded"
RELATION_NOT_USED = "not_used"
RELATION_READ_AT_RUN = "read_at_comparison_run"
NORMAL_LABELS_UNAVAILABLE = "verified_normal_labels_unavailable"
ANOMALY_LABELS_UNAVAILABLE = "verified_anomaly_labels_unavailable"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}(?::[a-z][a-z0-9_:-]{0,99})*")


def unavailable_code(exc: BaseException) -> str:
    text = str(exc)
    return text if _CODE.fullmatch(text) else PREPARATION_FAILED


def _label(evidence: Any) -> str | None:
    label = evidence.get("label") if isinstance(evidence, Mapping) else None
    return label if label in ("normal", "anomaly") else None


def _rates(cells: Mapping[str, int], anomaly_labels: int, normal_labels: int) -> dict[str, Any]:
    """`categoryResults.ml`과 같은 null 규칙. 값은 measurement 모양(unit·coverage 포함).

    recall.coverage = 기준 행 안의 anomaly 라벨 행 / 모집단 anomaly 라벨 행, falsePositiveRate.coverage = 같은 식의 normal,
    precision.coverage = null.
    """
    tp, fp, tn, fn = cells["tp"], cells["fp"], cells["tn"], cells["fn"]
    if anomaly_labels:
        recall = measurement(tp / (tp + fn) if tp + fn else None, "ratio", numerator=tp, denominator=tp + fn,
                             coverage=(tp + fn) / anomaly_labels, reason=None if tp + fn else "no_scored_anomaly_rows")
    else:
        recall = measurement(None, "ratio", reason=ANOMALY_LABELS_UNAVAILABLE)
    if not normal_labels:
        precision = measurement(None, "ratio", reason=NORMAL_LABELS_UNAVAILABLE)
        fpr = measurement(None, "ratio", reason=NORMAL_LABELS_UNAVAILABLE)
    else:
        precision = measurement(None, "ratio", reason=ANOMALY_LABELS_UNAVAILABLE) if not anomaly_labels else \
            measurement(tp / (tp + fp) if tp + fp else None, "ratio", numerator=tp, denominator=tp + fp,
                        reason=None if tp + fp else "no_predicted_anomalies")
        fpr = measurement(fp / (fp + tn) if fp + tn else None, "ratio", numerator=fp, denominator=fp + tn,
                          coverage=(fp + tn) / normal_labels, reason=None if fp + tn else "no_scored_normal_rows")
    return {"recall": recall, "precision": precision, "falsePositiveRate": fpr}


def _basis(rows: Sequence[tuple[str, bool | None, str | None]], included: Any, name: str,
           anomaly_labels: int, normal_labels: int) -> dict[str, Any]:
    cells = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    counted = hits = 0
    for status, anomalous, label in rows:
        if not included(status, anomalous):
            continue
        counted += 1
        hits += anomalous is True
        if label is not None:
            cells["tp" if anomalous and label == "anomaly" else "fp" if anomalous else
                  "fn" if label == "anomaly" else "tn"] += 1
    confusion = {"tp": cells["tp"] if anomaly_labels else None, "fp": cells["fp"] if normal_labels else None,
                 "tn": cells["tn"] if normal_labels else None, "fn": cells["fn"] if anomaly_labels else None}
    population = len(rows)
    return {"basis": name, "confusion": confusion, **_rates(cells, anomaly_labels, normal_labels),
            "coverage": counted / population, "unavailableCount": population - counted, "hitCount": hits}


def composite_measures(rows: Sequence[tuple[str, bool | None, str | None]], *, rule_set_revision: int,
                       policy_revision: int, relation_inputs: str) -> dict[str, Any]:
    """행마다 (종합 status, 종합 anomalous, 라벨 normal|anomaly|None) → `composite`. rows = 평가 대상 행(제외 뒤), 1행 이상.

    completeOnly = status complete 행(모든 방법 succeeded), decidable = anomalous가 None이 아닌 행(partial이어도 T 포함).
    """
    if not rows:
        raise ValueError(ALL_ROWS_EXCLUDED)
    labels = [label for _, _, label in rows]
    anomaly, normal = labels.count("anomaly"), labels.count("normal")
    return {"metricVersion": METRIC_VERSION, "ruleSetRevision": rule_set_revision,
            "detectionPolicyRevision": policy_revision, "populationCount": len(rows),
            "labelCounts": {"anomalyLabelCount": anomaly, "normalLabelCount": normal,
                            "unlabeledCount": len(rows) - anomaly - normal},
            "relationInputs": relation_inputs,
            "completeOnly": _basis(rows, lambda status, _: status == "complete", "complete_only", anomaly, normal),
            "decidable": {**_basis(rows, lambda _, anomalous: anomalous is not None, "decidable", anomaly, normal),
                          "biasNote": DECIDABLE_BIAS_NOTE}}


def judge_item(conn: Any, *, plan: Mapping[str, Any], policy_value: Any, config_id: Any, model_key: str,
               source_id: Any, rows: Sequence[Any], root: Any) -> tuple[dict[str, Any] | None, str | None]:
    """항목 1개의 종합. (composite, None) 또는 (None, 사유 코드 — `composite_unavailable:` 뒤에 붙는 부분).

    rows = 평가 대상 행(`_measure_version`과 같은 제외 뒤 행, (observation_id, allowed_values, split, label_evidence)).
    입력: 접수 때 고정한 룰 세트 리비전·hash, 정책 리비전·값(운영 뷰와 같은 형식), 항목 모델 1개만(활성 집합의 다른
    모델은 넣지 않음). 준비·행 판정에서 Detector가 ValueError를 내면 종합만 null이다(항목은 그대로 성공).
    """
    from detector.attempt_v04 import judge_row, load_models
    from detector.operational_state import pin_fixed_state
    from detector.relation_inputs import read_relation_inputs
    from detector.relation_plan import needs_relation_inputs, plan_relations

    if plan["detectionPolicy"]["k"]["ml"] > 1:
        # Q-E6: 항목의 ML 방법은 1개라 k.ml > 1이면 Detector 규칙(k > n은 실행 불가)으로 계산할 수 없다. k를 바꾸지 않는다.
        return None, K_EXCEEDS_SINGLE_MODEL
    if not rows:
        return None, ALL_ROWS_EXCLUDED
    try:
        state = pin_fixed_state(conn, plan["ruleSetRevision"], plan["ruleSetContentHash"],
                                plan["detectionPolicyRevision"], policy_value,
                                [{"configVersionId": str(config_id), "modelKey": model_key}],
                                active_model_set_revision=plan["activeModelSetRevision"])
        models = load_models(conn, state, source_id)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        return None, unavailable_code(exc)
    failed = next((model.load_error for model in models if model.load_error is not None), None)
    if failed is not None:
        return None, unavailable_code(ValueError(failed))
    judged: list[tuple[str, bool | None, str | None]] = []
    relation_used = False
    try:
        for observation_id, raw, _, label_evidence in rows:
            # 행마다 시도 시작과 같은 기준: 기한은 이 행의 시작부터, 관계 입력은 필요한 판정만 비교 실행 시점에 읽는다.
            started = time.monotonic()
            inputs = None
            if needs_relation_inputs(state, raw):
                inputs = read_relation_inputs(conn, source_id, observation_id)
                conn.commit()
                relation_used = True
            relation = plan_relations(state, inputs, raw)
            composite = judge_row(state, models, raw, root, relation, started_at=started).decided["composite"]
            judged.append((composite["status"], composite["anomalous"], _label(label_evidence)))
    except ValueError as exc:
        conn.rollback()
        return None, unavailable_code(exc)
    return composite_measures(judged, rule_set_revision=plan["ruleSetRevision"],
                              policy_revision=plan["detectionPolicyRevision"],
                              relation_inputs=RELATION_READ_AT_RUN if relation_used else RELATION_NOT_USED), None
