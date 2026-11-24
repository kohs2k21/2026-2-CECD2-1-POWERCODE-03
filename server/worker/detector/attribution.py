"""ML 기여도(판정 근거) `baseline-shapley-v1` — evidence v4 `methodResults[ml].attribution`(detection-decision.md 6.1).

ML 방법이 이상(T)으로 판정한 행만 기준값(학습 fit 분할 중앙값 = RobustScaler `center_`) 대비 정확 Shapley 기여도를
계산한다. 판정 기한(`deadlineMs`) 밖, `prepare_attempt`에서 `judge_row` 뒤·`build_evidence` 앞에 한 번 부른다.

- 판정 불변: 방법 결과의 다른 키(status·anomalous·score·scoreDetail 등)와 종합은 바꾸지 않는다. 계산·무결성 확인·
  직렬화 확인을 한 보호 구간에서 하고 모든 예외는 기여도 `status=failed`로 흡수한다(시도는 계속).
- 계산: 참가자 g개(변수 묶음 `source-field-union-v1`)의 연합 2^g행을 같은 scaler·forest로 한 번에 점수 매긴다.
  행 순서 = 비트마스크 오름차순(비트 i = 참가자 i), 합은 마스크 오름차순 `math.fsum`, 가중치는 정수 계승 → float 1회 나눗셈.
- 무결성: ① 재현 — v(전체)가 저장할 방법 결과 `score`와 tol 안(`score_not_reproduced`) ② 효율성 — Σφ = v(전체) − v(∅)
  (`efficiency_mismatch`). tol = max(1e-9, 1e-9·|score|).
- 방향: φ·비율은 s 기준(`higher_is_anomaly`면 s = 점수, `lower_is_anomaly`면 s = −점수) = 이상 쪽으로 민 양.
  `scoreActual`·`scoreBaseline`·`scoreAfter`는 원래 방향 점수. 판정선 비교는 `ml_method_result`와 같은 식.
- 반사실: 이미 계산한 연합 점수 v(전체 − 상위 k개)를 다시 쓴다(추가 점수 계산 없음).
- 한계: 기여도는 모델이 점수를 매긴 방식에 대한 분석이며 실제 장애 원인이 아니다. 묶음 기준값(열별 중앙값 조합)이
  서로 맞는 값이라는 보장도 없다. 화면·설명 문장 규칙은 detection-decision.md 6.1 "표시 규칙".
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np

import model_adapters
from derived_expression import FIELDS

VERSION = "baseline-shapley-v1"
GROUPING = "source-field-union-v1"
MAX_PLAYERS = 12
MAX_BYTES = 16384
TOP_K = 3
SKIP_REASONS = frozenset({"not_anomalous", "method_unresolved", "too_many_players", "baseline_unavailable"})
FAILURE_CODES = frozenset({"reference_unavailable", "score_not_reproduced", "efficiency_mismatch", "invalid_score",
                           "non_finite_value", "size_exceeded", "exception"})
# 모델 키 → model_adapters 함수 이름(기준값, 행렬 점수). 이름으로 늦게 바인딩해 시험의 patch가 그대로 통한다.
ADAPTERS = {"iforest": ("attribution_baseline", "score_iforest_batch")}


class _Skip(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Fail(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------- 3절 참가자 묶음 source-field-union-v1 ----------

def _ast_sources(ast: Mapping[str, Any]) -> frozenset[str]:
    """derived AST의 모든 column 잎(연산자 종류와 무관). literal은 원천 없음."""
    op = ast["op"]
    if op == "column":
        return frozenset({"P." + FIELDS[ast["column"]][0]})
    if op == "literal":
        return frozenset()
    sources: frozenset[str] = frozenset()
    for child in ast["args"]:
        sources |= _ast_sources(child)
    return sources


def source_fields(expression: Mapping[str, Any]) -> frozenset[str]:
    """변수 정의 1개의 원천 키 집합(`P.<관측 키>`). 그룹 키(`groupBy`)는 값이 아니라 조건이라 넣지 않는다."""
    op = expression["op"]
    if op == "field":
        return frozenset({expression["field"]})
    if op == "derived":
        return _ast_sources(expression["ast"])
    if op in ("add", "subtract", "ratio"):
        return source_fields(expression["left"]) | source_fields(expression["right"])
    if op == "group_centered":
        return source_fields(expression["value"])
    if op == "group_median":
        return frozenset()  # 행 값을 쓰지 않는다
    raise ValueError(f"attribution_unknown_operation:{op}")


def group_players(definitions: Sequence[Mapping[str, Any]], names: Sequence[str]) -> list[tuple[str, tuple[int, ...]]]:
    """(playerId, featureNames 위치들) 목록. 원천 집합이 겹치는 변수끼리 union-find로 한 참가자, 원천이 빈 변수는 단독.

    참가자 순서 = 구성원 중 가장 앞 featureNames 위치, 구성원도 featureNames 순. id = 단독이면 변수 이름,
    묶음이면 `group:<첫 구성원 이름>`.
    """
    by_name = {item["name"]: item["expression"] for item in definitions}
    if len(set(names)) != len(names) or set(names) != set(by_name):
        raise ValueError("attribution_feature_schema_mismatch")
    sources = [source_fields(by_name[name]) for name in names]
    parent = list(range(len(names)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(names)):
        for right in range(left + 1, len(names)):
            if sources[left] & sources[right]:
                a, b = find(left), find(right)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    members: dict[int, list[int]] = {}
    for index in range(len(names)):
        members.setdefault(find(index), []).append(index)
    groups = sorted(members.values(), key=lambda group: group[0])
    return [(names[group[0]] if len(group) == 1 else "group:" + names[group[0]], tuple(group)) for group in groups]


# ---------- 2절 계산 ----------

def shapley_values(values: Sequence[float], players: int) -> list[float]:
    """정확 Shapley. values[mask] = 연합 점수(s 기준), 길이 2^players."""
    full = math.factorial(players)
    weights = [math.factorial(size) * math.factorial(players - size - 1) / full for size in range(players)]
    sizes = [bin(mask).count("1") for mask in range(len(values))]
    phi = []
    for player in range(players):
        bit = 1 << player
        phi.append(math.fsum(weights[sizes[mask]] * (values[mask | bit] - values[mask])
                             for mask in range(len(values)) if not mask & bit))
    return phi


def _finite_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise _Fail("invalid_score")
    number = float(value)
    if not math.isfinite(number):
        raise _Fail("invalid_score")
    return number


def _plain(value: Any) -> Any:
    """저장할 값을 Python 기본형으로(numpy 값 금지). 정수는 int, 그 밖의 수는 유한한 float만 남기고 아니면 non_finite_value."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise _Fail("non_finite_value")
        return number
    raise TypeError(f"attribution_value_type:{type(value).__name__}")


def _past(raw: float, boundary: float, direction: str) -> bool:
    """`ml_method_result`와 같은 판정선 비교."""
    return raw >= boundary if direction == "higher_is_anomaly" else raw <= boundary


def _adapter(method_id: str) -> tuple[Callable[..., Any], Callable[..., Any]]:
    names = ADAPTERS.get(method_id.split(":", 1)[1])
    if names is None:
        raise _Fail("reference_unavailable")
    return getattr(model_adapters, names[0]), getattr(model_adapters, names[1])


def _compute(method: Mapping[str, Any], measured: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(measured, Mapping) or "reference" not in measured or "features" not in measured:
        raise _Fail("reference_unavailable")
    reference = measured["reference"]
    context, values, _missing = measured["features"]
    model = context["models"][0]
    names = list(model[5]["featureNames"])
    detail = method["scoreDetail"]
    direction, threshold = detail["direction"], detail["threshold"]
    if direction not in ("higher_is_anomaly", "lower_is_anomaly"):
        raise _Fail("invalid_score")
    boundary = _finite_float(float(threshold))
    stored = _finite_float(method["score"])
    players = group_players(context["definitions"], names)
    g = len(players)
    if g > MAX_PLAYERS:
        raise _Skip("too_many_players")
    baseline_of, score_batch = _adapter(method["methodId"])
    try:
        baseline = baseline_of(reference)
    except ValueError as exc:
        raise _Skip("baseline_unavailable") from exc
    center, unit = list(baseline["values"]), list(baseline["scaleIsUnit"])
    if len(center) != len(names) or len(unit) != len(names):
        raise _Skip("baseline_unavailable")
    actual = [float(values[name]) for name in names]
    owner = [0] * len(names)
    for player, (_pid, columns) in enumerate(players):
        for column in columns:
            owner[column] = player
    rows = [[actual[column] if mask >> owner[column] & 1 else center[column] for column in range(len(names))]
            for mask in range(1 << g)]
    try:
        scores = score_batch(reference, rows)
    except ValueError as exc:
        raise _Fail("invalid_score") from exc
    if len(scores) != 1 << g:
        raise _Fail("invalid_score")
    v = [_finite_float(score) for score in scores]
    full = (1 << g) - 1
    tol = max(1e-9, 1e-9 * abs(stored))
    if abs(v[full] - stored) > tol:
        raise _Fail("score_not_reproduced")
    sign = 1.0 if direction == "higher_is_anomaly" else -1.0
    s = [sign * score for score in v]
    phi = shapley_values(s, g)
    efficiency = math.fsum(phi) - (s[full] - s[0])
    if abs(efficiency) > tol:
        raise _Fail("efficiency_mismatch")
    spread, gap = s[full] - s[0], sign * boundary - s[0]
    top = sorted((player for player in range(g) if phi[player] > tol), key=lambda player: (-phi[player], player))[:TOP_K]
    counterfactual, cleared, removed = [], None, 0
    for k, player in enumerate(top, 1):
        removed |= 1 << player
        after = v[full & ~removed]
        warning = _past(after, boundary, direction)
        counterfactual.append({"replaced": [players[item][0] for item in top[:k]], "scoreAfter": after,
                               "warningAfterReplacement": warning})
        if not warning and cleared is None:
            cleared = k
    return {
        "version": VERSION, "status": "succeeded", "baselineKind": baseline["kind"], "grouping": GROUPING,
        "scoreActual": v[full], "scoreBaseline": v[0], "threshold": threshold, "direction": direction,
        "baselineWarning": _past(v[0], boundary, direction), "efficiencyError": efficiency,
        "players": [{"playerId": pid, "features": [names[column] for column in columns],
                     "actual": {names[column]: values[names[column]] for column in columns},
                     "baseline": {names[column]: center[column] for column in columns},
                     "scaleIsUnit": {names[column]: unit[column] for column in columns},
                     "phi": phi[player], "share": phi[player] / spread if spread > tol else None,
                     "thresholdGapShare": phi[player] / gap if gap > tol else None}
                    for player, (pid, columns) in enumerate(players)],
        "topK": [players[player][0] for player in top], "counterfactual": counterfactual, "clearedAtK": cleared,
        "pins": {"modelArtifactHash": model[2], "fitArtifactHash": model[4],
                 "featureNamesHash": hashlib.sha256(json.dumps(names, separators=(",", ":")).encode()).hexdigest()}}


def encoded_size(attribution: Mapping[str, Any]) -> int:
    """크기 상한 비교값: json.dumps(allow_nan=False, ensure_ascii=False, 구분자 `,`·`:`)의 UTF-8 바이트."""
    return len(json.dumps(attribution, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def compute_attribution(method: Mapping[str, Any], measured: Mapping[str, Any] | None, *,
                        clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """ML 방법 결과 1개의 `attribution` 객체. 예외를 올리지 않는다(보호 구간)."""
    if method.get("status") != "succeeded" or type(method.get("anomalous")) is not bool:
        return {"version": VERSION, "status": "skipped", "reason": "method_unresolved"}
    if method["anomalous"] is not True:
        return {"version": VERSION, "status": "skipped", "reason": "not_anomalous"}
    began = clock()

    def failed(code: str) -> dict[str, Any]:
        return {"version": VERSION, "status": "failed", "failureCode": code, "computeMs": (clock() - began) * 1000}

    try:
        attribution = _compute(method, measured)
        attribution["computeMs"] = (clock() - began) * 1000
        attribution = _plain(attribution)
        try:
            size = encoded_size(attribution)
        except ValueError:
            # _plain이 막지 못한 NaN·Infinity(allow_nan=False 거절). 판정 행 직렬화까지 가져가지 않는다.
            raise _Fail("non_finite_value") from None
        if size > MAX_BYTES:
            return failed("size_exceeded")
        return attribution
    except _Skip as skip:
        return {"version": VERSION, "status": "skipped", "reason": skip.reason}
    except _Fail as failure:
        return failed(failure.code)
    except Exception:  # noqa: BLE001 - 기여도 오류가 판정을 지우지 않는다
        return failed("exception")


def attach_attributions(methods: Sequence[dict[str, Any]], measured: Mapping[str, Mapping[str, Any]], *,
                        clock: Callable[[], float] = time.perf_counter) -> None:
    """ML 방법 결과마다 `attribution` 키를 더한다(다른 키는 그대로). measured = 기한 안에 채택된 방법별 측정값."""
    for method in methods:
        if method.get("category") != "ml":
            continue
        try:
            attribution = compute_attribution(method, measured.get(method.get("methodId")), clock=clock)
        except Exception:  # noqa: BLE001 - 보호 구간 밖으로 예외를 내지 않는다
            attribution = {"version": VERSION, "status": "failed", "failureCode": "exception", "computeMs": 0.0}
        method["attribution"] = attribution
