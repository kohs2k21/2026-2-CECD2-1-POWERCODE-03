"""모델 교체 추천(`replacementRecommendations[]`) v1 계산 — 규칙 `replacement-recommendation-v1`.

계약: `server/contracts/config-comparison-evaluation.md` 결과 절의 `replacementRecommendations[]` 규칙과
"교체 추천 v1 대응(11/15)" 절. 비교 작업 1건이 작업 메모리에 가진 값(항목 결과·행별 ML 판정·행 라벨·버전 간 판정 일치)과
비교 대상 버전 행의 불변 값(`createdAt`·`displayName`)만 읽는 순수 함수다(DB·시계 없음, `generatedAt`은 인자).

v1 입력: 작업당 modelKey 1종이라 원소는 0..1개. 근거 지표는 모델 단독 `ml.completeOnly`의 recall·precision·FPR뿐이다
(합성 세트 없음 — 시나리오 탐지율 없음). 행별 판정·라벨로 공통 교집합 `C` 위에서 다시 센다(R16).
교체 추천은 라벨 근거로만 만든다. 무라벨 값(`unlabeledEvidence`)은 이미 계산된 값을 복사해 표시만 하고 판단에 쓰지 않는다.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from methods.registry import method_for_key

RULE_VERSION = "replacement-recommendation-v1"

# 임계값(R3~R6, 결정 5). Trainer 설정 모듈 상수이며 결과 `thresholds`에 사용값을 그대로 기록한다(R14).
MIN_LABELED_ANOMALY_COUNT = 10
MIN_LABELED_NORMAL_COUNT = 10
MIN_SCENARIO_DENOMINATOR = 10
RATIO_EPSILON = 0.0
LATENCY_P95_RELATIVE_LIMIT = 2.0
LATENCY_FLOOR_MS = 50
LATENCY_DEADLINE_RATIO = 0.5
COST_UNMEASURED_POLICY = "allow"
STALE_ACCEPT_POLICY = "reject"
TIE_BREAK = ("recall_tp", "fpr_fp", "precision", "scenario_min", "itemP95Ms", "created_at_asc")


def thresholds() -> dict[str, Any]:
    return {"minLabeledAnomalyCount": MIN_LABELED_ANOMALY_COUNT, "minLabeledNormalCount": MIN_LABELED_NORMAL_COUNT,
            "minScenarioDenominator": MIN_SCENARIO_DENOMINATOR, "ratioEpsilon": RATIO_EPSILON,
            "latencyP95RelativeLimit": LATENCY_P95_RELATIVE_LIMIT, "latencyFloorMs": LATENCY_FLOOR_MS,
            "latencyDeadlineRatio": LATENCY_DEADLINE_RATIO, "costUnmeasuredPolicy": COST_UNMEASURED_POLICY,
            "staleAcceptPolicy": STALE_ACCEPT_POLICY, "tieBreak": list(TIE_BREAK)}


RECALL = "ml.completeOnly.recall"
PRECISION = "ml.completeOnly.precision"
FPR = "ml.completeOnly.falsePositiveRate"
METRICS = (RECALL, PRECISION, FPR)
_DIRECTION = {RECALL: "higher_is_better", PRECISION: "higher_is_better", FPR: "lower_is_better"}
_LABEL = {RECALL: "모델 재현율", PRECISION: "모델 정밀도", FPR: "모델 오탐률"}

NORMAL_LABELS_UNAVAILABLE = "verified_normal_labels_unavailable"
ANOMALY_LABELS_UNAVAILABLE = "verified_anomaly_labels_unavailable"
INSUFFICIENT_DENOMINATOR = "insufficient_denominator"
NO_PREDICTED_ANOMALIES = "no_predicted_anomalies"

SELECTED_REASON = "better_than_active_on_all_basis_metrics"
COST_EXCEEDED = "cost_limit_exceeded:itemP95Ms"
COST_LIMIT_RULE = "max(latencyFloorMs, baseline*latencyP95RelativeLimit) and deadlineMs*latencyDeadlineRatio"
UNLABELED_INTERPRETATION = "checker_agreement_not_accuracy"
COMPOSITE_REFERENCE = "composite_reference_only"

# 추천 없음 사유 9종(확정 순서). 문구는 기획 PRD 3절 그대로(사용자 11/15 확정).
NO_RECOMMENDATION_SUMMARY = {
    "active_missing": "현재 운영 중인 버전이 없어 비교 기준이 없습니다. 운영 버전은 직접 선택해 적용하세요.",
    "active_item_failed": "현재 운영 버전의 평가가 실패해 비교할 수 없습니다(사유: {errorCode}).",
    "active_results_unavailable": "현재 운영 버전의 평가에 판정 불가 행이 있어 비교 기준으로 쓸 수 없습니다.",
    "no_candidates": "비교할 후보 버전이 없습니다.",
    "all_candidates_failed": "모든 후보 버전의 평가가 실패했습니다. 항목별 사유를 확인하세요.",
    "all_candidates_ineligible": "실행 가능하고 판정 불가 행이 없는 후보가 없습니다.",
    "basis_metrics_unavailable": "이 평가 세트에는 정답 라벨이 부족해 정량 비교 근거를 만들 수 없습니다.",
    "no_candidate_better_than_active": "현재 운영 버전보다 모든 근거 지표에서 같거나 나은 후보가 없습니다.",
    "cost_limit_exceeded": "정확도가 나은 후보가 있으나 판정 지연 한도를 넘어 추천하지 않습니다. 직접 선택할 수 있습니다.",
}

# `decision`은 사유 코드에서 결정적으로 정해지는 파생 값(결정 3). None = 추천 있음.
DECISIONS = {
    None: "recommend_replace",
    "no_candidate_better_than_active": "keep_current",
    "cost_limit_exceeded": "keep_current",
    "active_item_failed": "insufficient_evidence",
    "active_results_unavailable": "insufficient_evidence",
    "all_candidates_failed": "insufficient_evidence",
    "all_candidates_ineligible": "insufficient_evidence",
    "basis_metrics_unavailable": "insufficient_evidence",
    "active_missing": "not_applicable",
    "no_candidates": "not_applicable",
}


def decision_for(reason: str | None) -> str:
    return DECISIONS[reason]


def version_name(config_id: str, model_key: str | None, versions: Mapping[str, Mapping[str, Any]]) -> str:
    """summary 안 버전 이름(결정 8 A): `displayName`(v3)이 있으면 그것, 없으면 "<카탈로그 모델 이름> 버전 <id 앞 8자>"."""
    display = (versions.get(config_id) or {}).get("displayName")
    if isinstance(display, str) and display.strip():
        return display.strip()
    method = method_for_key(model_key) if isinstance(model_key, str) else None
    catalog = method.display_name if method is not None else (model_key or "모델")
    return f"{catalog} 버전 {config_id[:8]}"


# ---------- 항목 값 읽기(이미 계산된 값만) ----------

def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _item_p95(item: Mapping[str, Any]) -> float | None:
    metric = ((item.get("executionCost") or {}).get("metrics") or {}).get("itemP95Ms")
    return _number(metric.get("value")) if isinstance(metric, Mapping) else None


def _ml(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return (item.get("categoryResults") or {}).get("ml") or {}


def _hit_count(item: Mapping[str, Any]) -> int | None:
    composite = item.get("composite")
    return (composite.get("completeOnly") or {}).get("hitCount") if isinstance(composite, Mapping) else None


def _composite_recall(item: Mapping[str, Any], basis: str) -> float | None:
    composite = item.get("composite")
    if not isinstance(composite, Mapping):
        return None
    return ((composite.get(basis) or {}).get("recall") or {}).get("value")


# ---------- 공통 교집합 위 다시 세기 ----------

def _cells(rows: Sequence[str], predicted: Mapping[str, bool], labels: Mapping[str, str | None]) -> dict[str, int]:
    cells = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for key in rows:
        label = labels.get(key)
        if label not in ("anomaly", "normal"):
            continue
        anomalous = predicted[key]
        cells["tp" if anomalous and label == "anomaly" else "fp" if anomalous else
              "fn" if label == "anomaly" else "tn"] += 1
    return cells


def _rates(cells: Mapping[str, int], anomaly: int, normal: int) -> dict[str, dict[str, Any]]:
    predicted = cells["tp"] + cells["fp"]
    return {RECALL: {"value": cells["tp"] / anomaly if anomaly else None, "numerator": cells["tp"], "denominator": anomaly},
            PRECISION: {"value": cells["tp"] / predicted if predicted else None, "numerator": cells["tp"],
                        "denominator": predicted},
            FPR: {"value": cells["fp"] / normal if normal else None, "numerator": cells["fp"], "denominator": normal}}


def _basis_set(anomaly: int, normal: int, rates: Sequence[Mapping[str, Mapping[str, Any]]]
               ) -> tuple[list[str], list[dict[str, str]]]:
    """비교 집합 P(원소 공통)와 `excludedMetrics[]`. 라벨 0은 기존 null 규칙의 사유, 최소 표본 미달은
    `insufficient_denominator`(recall은 anomaly 라벨, precision·FPR은 normal 라벨 — R3·결정 5). 활성 또는 자격 후보
    하나라도 값이 null이면(precision 분모 0) 그 지표는 원소 전체에서 뺀다 — 사전식 비교가 모든 후보에 같은 지표를 쓰게."""
    reasons: dict[str, str | None] = {}
    reasons[RECALL] = ANOMALY_LABELS_UNAVAILABLE if not anomaly else \
        INSUFFICIENT_DENOMINATOR if anomaly < MIN_LABELED_ANOMALY_COUNT else None
    reasons[PRECISION] = NORMAL_LABELS_UNAVAILABLE if not normal else ANOMALY_LABELS_UNAVAILABLE if not anomaly else \
        INSUFFICIENT_DENOMINATOR if normal < MIN_LABELED_NORMAL_COUNT else \
        NO_PREDICTED_ANOMALIES if any(rate[PRECISION]["value"] is None for rate in rates) else None
    reasons[FPR] = NORMAL_LABELS_UNAVAILABLE if not normal else \
        INSUFFICIENT_DENOMINATOR if normal < MIN_LABELED_NORMAL_COUNT else None
    included = [metric for metric in METRICS if reasons[metric] is None]
    excluded = [{"metric": metric, "reason": reasons[metric]} for metric in METRICS if reasons[metric] is not None]
    return included, excluded


def _outcome(metric: str, target: Mapping[str, Any], baseline: Mapping[str, Any]) -> str:
    """분모가 같은 recall·FPR은 분자 정수 비교, precision은 비율 비교(`ratioEpsilon` 안이면 동등)."""
    if metric == PRECISION:
        difference = target["value"] - baseline["value"]
        return "equal" if abs(difference) <= RATIO_EPSILON else "better" if difference > 0 else "worse"
    difference = target["numerator"] - baseline["numerator"]
    if metric == FPR:
        difference = -difference
    return "equal" if difference == 0 else "better" if difference > 0 else "worse"


def _rate_text(rate: Mapping[str, Any]) -> str:
    return f"{rate['numerator']}/{rate['denominator']}({rate['value']:.3f})"


def _ms_text(value: float) -> str:
    return str(round(value)) if value >= 1 else f"{value:.1f}"


# ---------- 원소 계산 ----------

def recommend(*, comparison_id: str, job_id: str, evaluation_set: Mapping[str, Any],
              baselines: Sequence[Mapping[str, Any]], active_revision: int, items: Sequence[Mapping[str, Any]],
              predictions: Mapping[str, Mapping[str, bool]], labels: Mapping[str, str | None],
              pairwise: Sequence[Mapping[str, Any]], deadline_ms: float | None,
              versions: Mapping[str, Mapping[str, Any]], generated_at: str) -> list[dict[str, Any]]:
    """비교 작업 1건의 추천 원소 목록. v1은 작업당 modelKey 1종이라 길이 0..1.

    - `items`: summary `items`(항목 순서 = 활성 먼저, 후보는 UUID 문자열 순)
    - `predictions`: 성공 항목 configVersionId → (observation id → ML 판정; ML succeeded + bool 판정 행만)
    - `labels`: observation id → 행 라벨(`normal`·`anomaly` 외는 라벨 없음)
    - `pairwise`: summary `pairwiseAgreement`(복사 대상), `deadline_ms`: 접수 고정 정책 `deadlineMs`
    - `versions`: configVersionId → {`createdAt`, `displayName`}(버전 행의 불변 값)
    """
    model_key = baselines[0]["modelKey"] if baselines else next(
        (item["modelKey"] for item in items if isinstance(item.get("modelKey"), str)), None)
    if model_key is None:
        return []  # 전 항목이 키를 알 수 없는 실패 — 추천할 모델 종류가 없다.
    return [_element(model_key=model_key, comparison_id=comparison_id, job_id=job_id, evaluation_set=evaluation_set,
                     baselines=baselines, active_revision=active_revision, items=items, predictions=predictions,
                     labels=labels, pairwise=pairwise, deadline_ms=deadline_ms, versions=versions,
                     generated_at=generated_at)]


def _element(*, model_key: str, comparison_id: str, job_id: str, evaluation_set: Mapping[str, Any],
             baselines: Sequence[Mapping[str, Any]], active_revision: int, items: Sequence[Mapping[str, Any]],
             predictions: Mapping[str, Mapping[str, bool]], labels: Mapping[str, str | None],
             pairwise: Sequence[Mapping[str, Any]], deadline_ms: float | None,
             versions: Mapping[str, Mapping[str, Any]], generated_at: str) -> dict[str, Any]:
    baseline_id = str(baselines[0]["configVersionId"]) if baselines else None
    active = next((item for item in items if item["configVersionId"] == baseline_id), None) if baseline_id else None
    # 후보 = 이 modelKey의 후보 항목(R18). v1은 작업당 modelKey 1종이라 키를 알 수 없는 실패 항목도 이 원소의 후보로 센다.
    candidates = [item for item in items if item["role"] == "candidate" and item.get("modelKey") in (model_key, None)]
    element: dict[str, Any] = {
        "modelKey": model_key, "ruleVersion": RULE_VERSION, "decision": None,
        "comparisonId": comparison_id, "comparisonJobId": job_id, "evaluationSetId": str(evaluation_set["id"]),
        "baselineConfigVersionId": baseline_id, "baselineEvaluationId": active.get("evaluationId") if active else None,
        "baselineActiveRevision": active_revision, "targetVersionId": None, "targetEvaluationId": None,
        "summary": None, "reason": None, "noRecommendationReason": None,
        "comparedRowCount": None, "comparedLabelCounts": None, "basis": [], "excludedMetrics": [], "checks": None,
        "costs": [], "candidates": [], "references": [],
        "unlabeledEvidence": {"interpretation": UNLABELED_INTERPRETATION, "pairs": []},
        "thresholds": thresholds(), "generatedAt": generated_at}
    entries = {item["configVersionId"]: {"configVersionId": item["configVersionId"], "modelKey": item.get("modelKey"),
                                         "outcome": "excluded", "excludedReason": None, "lostAt": None}
               for item in candidates}

    def finish(reason: str | None) -> dict[str, Any]:
        element["candidates"] = [entries[item["configVersionId"]] for item in candidates]
        element["noRecommendationReason"] = reason
        element["decision"] = decision_for(reason)
        if reason is not None:
            element["summary"] = NO_RECOMMENDATION_SUMMARY[reason].replace(
                "{errorCode}", str(active.get("errorCode")) if active else "")
        return element

    # (a) 후보 자격 중 활성과 무관한 단계(순서대로 첫 실패): 항목 실패 → 실행 불가(방어) → applicability → 세트 용도.
    # `operational_ineligible`(prepared 계보)은 v1에서 항목 실패 `prepared_not_comparable`로 먼저 걸려 여기서 나오지 않는다.
    eligible = []
    for item in candidates:
        entry = entries[item["configVersionId"]]
        method = method_for_key(item["modelKey"]) if isinstance(item.get("modelKey"), str) else None
        if item["status"] != "succeeded":
            entry["excludedReason"] = f"item_failed:{item.get('errorCode')}"
        elif method is None or not method.executable_process:
            entry["excludedReason"] = "not_executable"
        elif item.get("applicability") != "valid":
            entry["excludedReason"] = f"applicability_{item.get('applicability')}"
        elif evaluation_set.get("purpose") != "validation":
            # 적용 gate가 validation 세트 평가만 받으므로 받아들여지지 않을 추천을 만들지 않는다(결정 7).
            entry["excludedReason"] = "evaluation_set_not_validation"
        else:
            entry["outcome"] = "eligible_not_compared"
            eligible.append(item)

    # 추천 없음 사유: 활성 단계 → 후보 단계(확정 순서). `active_results_unavailable` = 활성 판정 행 0(결정 4).
    if active is None:
        return finish("active_missing")
    if active["status"] != "succeeded":
        return finish("active_item_failed")
    active_rows = predictions.get(baseline_id) or {}
    if not active_rows:
        return finish("active_results_unavailable")
    if not candidates:
        return finish("no_candidates")
    if all(item["status"] != "succeeded" for item in candidates):
        return finish("all_candidates_failed")

    # 공통 교집합 C: 활성 판정 행에서 시작해 자격 후보를 항목 순서(UUID 문자열 순)로 넣는다. 넣으면 C가 비는 후보는
    # 넣지 않고 `common_rows_empty`로 자격 제외(결정 4 A).
    common = set(active_rows)
    compared = []
    for item in eligible:
        narrowed = common & set(predictions.get(item["configVersionId"]) or {})
        if not narrowed:
            entries[item["configVersionId"]].update(outcome="excluded", excludedReason="common_rows_empty")
            continue
        common = narrowed
        compared.append(item)
    if not compared:
        return finish("all_candidates_ineligible")
    # 무라벨 근거: 교집합에 들어간 자격 후보마다 이미 계산된 값만 복사(판단에 쓰지 않음). 쌍은 활성이 항상 a(활성 먼저).
    pairs = {(pair["a"], pair["b"]): pair for pair in pairwise}
    element["unlabeledEvidence"]["pairs"] = [
        _evidence(active, item, pairs.get((baseline_id, item["configVersionId"]))) for item in compared]
    rows = sorted(common)
    anomaly = sum(1 for key in rows if labels.get(key) == "anomaly")
    normal = sum(1 for key in rows if labels.get(key) == "normal")
    element["comparedRowCount"] = len(rows)
    element["comparedLabelCounts"] = {"anomalyLabelCount": anomaly, "normalLabelCount": normal}

    # (b) 1차 지표: C 위에서 활성·후보를 다시 센다(모델 단독 ml 경로).
    rates = {config: _rates(_cells(rows, predictions[config], labels), anomaly, normal)
             for config in [baseline_id] + [item["configVersionId"] for item in compared]}
    basis_metrics, excluded = _basis_set(anomaly, normal, list(rates.values()))
    element["excludedMetrics"] = excluded
    if not basis_metrics:
        return finish("basis_metrics_unavailable")
    baseline_rates = rates[baseline_id]
    rows_of: dict[str, list[dict[str, Any]]] = {}
    dominating = []
    for item in compared:
        config = item["configVersionId"]
        entry = entries[config]
        basis = [{"metric": metric, "family": "ml", "direction": _DIRECTION[metric],
                  "target": dict(rates[config][metric]), "baseline": dict(baseline_rates[metric]),
                  "outcome": _outcome(metric, rates[config][metric], baseline_rates[metric]), "comparedTo": baseline_id}
                 for metric in basis_metrics]
        rows_of[config] = basis
        worse = next((row["metric"] for row in basis if row["outcome"] == "worse"), None)
        if worse is not None:
            entry.update(outcome="excluded", excludedReason=f"worse_on:{worse}")
        elif all(row["outcome"] == "equal" for row in basis):
            entry.update(outcome="excluded", excludedReason="equal_to_active")
        else:
            dominating.append(item)
    if not dominating:
        return finish("no_candidate_better_than_active")

    # (c) 실행 비용 한도: 후보 itemP95Ms ≤ min(max(floor, 활성 × 2.0), deadlineMs × 0.5). 후보 값 null은 허용(R6).
    baseline_p95 = _item_p95(active)
    limits = [value for value in (
        max(LATENCY_FLOOR_MS, baseline_p95 * LATENCY_P95_RELATIVE_LIMIT) if baseline_p95 is not None else None,
        deadline_ms * LATENCY_DEADLINE_RATIO if deadline_ms is not None else None) if value is not None]
    limit = min(limits) if limits else None
    passing = []
    for item in dominating:
        p95 = _item_p95(item)
        if p95 is not None and limit is not None and p95 > limit:
            entries[item["configVersionId"]].update(outcome="excluded", excludedReason=COST_EXCEEDED)
        else:
            passing.append(item)
    if not passing:
        return finish("cost_limit_exceeded")

    # (e) 다수 후보: 사전식 6단계(R8). 값이 없는 후보가 있는 단계는 건너뛴다(정의된 경우만 비교).
    target, lost = _select(passing, rates, basis_metrics, versions)
    for item in passing:
        entry = entries[item["configVersionId"]]
        if item is target:
            entry.update(outcome="selected", excludedReason=None, lostAt=None)
        else:
            entry.update(outcome="eligible_not_selected", excludedReason=None, lostAt=lost[item["configVersionId"]])
    config = target["configVersionId"]
    p95 = _item_p95(target)
    cost_outcome = "unmeasured" if p95 is None else "within_limit"
    element.update(
        targetVersionId=config, targetEvaluationId=target.get("evaluationId"), reason=SELECTED_REASON,
        basis=rows_of[config],
        checks={"itemStatus": "succeeded", "executable": True,
                "methodUnavailableCount": _ml(target).get("unavailableCount"), "applicability": "valid",
                "costLimit": "unmeasured" if p95 is None else "passed"},
        costs=[{"metric": "execution_cost.itemP95Ms", "target": p95, "baseline": baseline_p95, "limit": limit,
                "limitRule": COST_LIMIT_RULE, "outcome": cost_outcome}],
        references=[{"metric": f"composite.{basis}.recall", "target": _composite_recall(target, basis),
                     "baseline": _composite_recall(active, basis), "note": COMPOSITE_REFERENCE}
                    for basis in ("completeOnly", "decidable")
                    if isinstance(target.get("composite"), Mapping) and isinstance(active.get("composite"), Mapping)])
    finish(None)
    element["summary"] = _summary(target, active, rows_of[config], p95, model_key, versions)
    return element


def _select(passing: Sequence[Mapping[str, Any]], rates: Mapping[str, Mapping[str, Mapping[str, Any]]],
            basis_metrics: Sequence[str], versions: Mapping[str, Mapping[str, Any]]
            ) -> tuple[Mapping[str, Any], dict[str, int]]:
    """사전식 6단계: 1 recall tp 큰 쪽 → 2 fp 작은 쪽 → 3 precision 큰 쪽(`ratioEpsilon` 밖 차이만) → 4 시나리오 탐지율
    최솟값(v1 입력 없음 — 모두 동등) → 5 itemP95Ms 작은 쪽 → 6 createdAt 빠른 쪽. 6단계까지 같으면 항목 순서 앞쪽이고
    뒤 후보는 `lostAt` 6. 각 단계는 남은 후보 모두 값이 있을 때만 비교한다."""
    def tp(item): return rates[item["configVersionId"]][RECALL]["numerator"] if RECALL in basis_metrics else None
    def fp(item): return rates[item["configVersionId"]][FPR]["numerator"] if FPR in basis_metrics else None
    def precision(item): return rates[item["configVersionId"]][PRECISION]["value"] if PRECISION in basis_metrics else None
    def created(item): return (versions.get(item["configVersionId"]) or {}).get("createdAt")
    steps = ((1, tp, "max"), (2, fp, "min"), (3, precision, "max_ratio"), (4, lambda item: None, "max"),
             (5, _item_p95, "min"), (6, created, "min"))
    remaining = list(passing)
    lost: dict[str, int] = {}
    for step, key, mode in steps:
        if len(remaining) < 2:
            break
        values = [key(item) for item in remaining]
        if any(value is None for value in values):
            continue
        best = min(values) if mode == "min" else max(values)
        keep = [item for item, value in zip(remaining, values)
                if (value >= best - RATIO_EPSILON if mode == "max_ratio" else value == best)]
        for item in remaining:
            if item not in keep:
                lost[item["configVersionId"]] = step
        remaining = keep
    for item in remaining[1:]:
        lost[item["configVersionId"]] = 6
    return remaining[0], lost


def _summary(target: Mapping[str, Any], active: Mapping[str, Any], basis: Sequence[Mapping[str, Any]],
             p95: float | None, model_key: str, versions: Mapping[str, Mapping[str, Any]]) -> str:
    """R13 틀: "{대상}이 현재 운영 {현재}보다 {지표} {target} > {baseline}, 지연 P95 {ms}ms(한도 내)". 엄격 우위 행만 쓰고
    낮을수록 좋은 지표(오탐률)는 `<`로 적는다. 후보 지연이 미측정이면 지연 구절을 넣지 않는다."""
    parts = [f"{_LABEL[row['metric']]} {_rate_text(row['target'])} "
             f"{'<' if row['direction'] == 'lower_is_better' else '>'} {_rate_text(row['baseline'])}"
             for row in basis if row["outcome"] == "better"]
    text = (f"{version_name(target['configVersionId'], model_key, versions)}이 현재 운영 "
            f"{version_name(active['configVersionId'], model_key, versions)}보다 " + ", ".join(parts))
    return text + (f", 지연 P95 {_ms_text(p95)}ms(한도 내)" if p95 is not None else "")


def _evidence(active: Mapping[str, Any], item: Mapping[str, Any], pair: Mapping[str, Any] | None) -> dict[str, Any]:
    """무라벨 근거 1쌍. 값은 항목 결과·`pairwiseAgreement`에 이미 있는 값 복사뿐(새 계산 없음)."""
    pair = pair or {}

    def both(read):
        return {"baseline": read(active), "candidate": read(item)}
    return {"baseline": active["configVersionId"], "candidate": item["configVersionId"],
            "comparableCount": pair.get("comparableCount"), "both": pair.get("both"),
            "baselineOnly": pair.get("aOnly"), "candidateOnly": pair.get("bOnly"), "neither": pair.get("neither"),
            "hitCount": both(_hit_count),
            "predictedAnomalyCount": both(lambda value: _ml(value).get("predictedAnomalyCount")),
            "excludedCount": both(lambda value: (value.get("counts") or {}).get("excludedCount")),
            "mlUnavailableCount": both(lambda value: _ml(value).get("unavailableCount")),
            "itemP95Ms": both(_item_p95)}
