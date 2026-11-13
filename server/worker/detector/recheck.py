"""관계 규칙 재판정 탐색(contracts/relation-rules.md 6절, protocol-rules.md 10절 7단계)의 순수 부분. DB를 읽지 않는다.

- 대상 판정: 거래 행이 와야 풀리는 기록이 있는 v04 첫 판정 — relationNotes `held`(관계 규칙 수집 미완료 보류,
  `hard:transaction-failed` `transaction_pending`)와 `deferred_to_transaction`(`required_step`·프로토콜). 재판정 판정
  (evidence `relationRecheck`)은 대상이 아니다(1회).
- 기간: 거래 행(실행마다 최신 리비전 중 가장 이른 `persisted_at`)이 판정 `decided_at` + 24시간 안에 왔을 때만.
- 사전 검사(6.2): 시도와 같은 규칙(relation_plan)으로 **현재 룰 세트**에서 관계 입력에 기대는 하드룰(①~⑤ — 거래 실패,
  관계 규칙, 프로토콜·미등록 인터페이스)을 다시 평가해, N이 아닌 결과(T 또는 방법 결과 보류)가 하나라도 있고 그 결과로
  종합(`grade`·`risk.level`·`basisMethods` 집합)이 바뀌는 판정만 재판정한다. 사전 검사는 거르기용이고 정본은 재판정
  시도가 시작 때 다시 읽은 입력으로 낸 결과다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from detector.decision import decide
from detector.hard_rules import (TRANSACTION_FAILED, UNREGISTERED_INTERFACE, evaluate_transaction_failed,
                                 is_protocol_rule_id, is_relation_rule_id)
from detector.method_results import with_elapsed
from detector.relation_inputs import TRANSACTION_SETTLE_SECONDS
from detector.relation_plan import RelationPlan

# 잠금 번호는 DB 세션 배정(11/12). 81742 → 81740(재판정 작업 생성의 manifest 잠금)만, 81741과 같은 트랜잭션에서 잡지 않는다.
RECHECK_LOCK_CLASS = 81742
RECHECK_LOCK_KEY = 0
# 탐색 기간(거래 행 도착 ≤ 판정 decided_at + 24시간), 최소 지연(도착 확정과 같은 상수), 거래 관측 커서 겹침.
WINDOW_SECONDS = 24 * 3600
SETTLE_SECONDS = TRANSACTION_SETTLE_SECONDS
OVERLAP_SECONDS = 60
# relationNotes 중 거래 행이 와야 풀리는 기록.
AWAITING_RESULTS = frozenset({"held", "deferred_to_transaction"})


def awaiting_transaction(notes: Any) -> bool:
    """판정의 relationNotes에 거래 행 도착을 기다리는 기록이 있는가."""
    return isinstance(notes, list) and any(
        isinstance(item, Mapping) and item.get("result") in AWAITING_RESULTS for item in notes)


def within_window(first_transaction_at: datetime, decided_at: datetime, window_seconds: float = WINDOW_SECONDS) -> bool:
    """거래 행이 판정 뒤 기간 안에 왔는가(판정보다 먼저 온 거래 행 — CSV 재생 — 은 항상 안)."""
    return first_transaction_at <= decided_at + timedelta(seconds=window_seconds)


def relation_dependent(method_id: str) -> bool:
    """관계 입력으로 평가하는 하드룰(사전 검사 ①~⑤가 다시 평가하는 방법)."""
    return method_id in (TRANSACTION_FAILED, UNREGISTERED_INTERFACE) or is_relation_rule_id(method_id) or \
        is_protocol_rule_id(method_id)


def reevaluated(state: Any, plan: RelationPlan) -> dict[str, dict[str, Any]]:
    """현재 룰 세트로 다시 평가한 관계 기대 하드룰 결과(methodId → 방법 결과). 과제 순서(plan.hard_ids) 그대로."""
    results: dict[str, dict[str, Any]] = {}
    if plan.inputs is None:
        return results
    for method_id in plan.hard_ids:
        if method_id == TRANSACTION_FAILED:
            results[method_id] = with_elapsed(evaluate_transaction_failed(plan.inputs, state.hard_rules), 0.0)
        elif method_id in (plan.relation_results or {}):
            results[method_id] = with_elapsed(dict(plan.relation_results[method_id]), 0.0)
    return results


def _open(result: Mapping[str, Any]) -> bool:
    """N이 아닌 결과: T 또는 방법 결과 보류(데이터 문제·입력 상한 — hard 대분류를 판단 불가로 만든다)."""
    return result["anomalous"] is True or result["status"] != "succeeded"


def composite_key(composite: Any) -> tuple | None:
    """비교 키 (grade, risk.level, basisMethods 집합). 모양이 어긋나면(근거 항목이 문자열이 아님 등) None."""
    if not isinstance(composite, Mapping) or not isinstance(composite.get("basisMethods"), list) or \
            not all(isinstance(item, str) for item in composite["basisMethods"]):
        return None
    risk = composite.get("risk") if isinstance(composite.get("risk"), Mapping) else {}
    grade, level = composite.get("grade"), risk.get("level")
    if not (grade is None or isinstance(grade, str)) or not (level is None or isinstance(level, str)):
        return None
    return grade, level, frozenset(composite["basisMethods"])


@dataclass(frozen=True)
class Precheck:
    """target이면 재판정한다. reason: open_result_absent | composite_unchanged | composite_changed | precheck_unavailable."""
    target: bool
    reason: str


def precheck(state: Any, plan: RelationPlan, old_methods: Any, old_composite: Any) -> Precheck:
    """6.2: N 아닌 결과가 있고 종합이 바뀌면 대상. 옛 판정의 관계 무관 방법(내장 PROCESS 규칙·소프트룰·ML) 결과는 그대로
    두고 관계 기대 하드룰만 현재 결과로 바꿔 종합을 다시 계산한다(현재 정책).

    옛 evidence를 읽지 못하거나(형식 이상) 현재 정책으로 종합을 계산할 수 없으면(k 범위 등) 거르지 않고 대상으로 넘긴다 —
    정본은 재판정 시도이고, 거르기가 틀려 재판정을 빠뜨리는 쪽이 더 나쁘다.
    """
    current = reevaluated(state, plan)
    if not any(_open(result) for result in current.values()):
        return Precheck(False, "open_result_absent")
    before = composite_key(old_composite)
    if before is None or not isinstance(old_methods, list) or not all(
            isinstance(item, Mapping) and isinstance(item.get("methodId"), str) for item in old_methods):
        return Precheck(True, "precheck_unavailable")
    kept = [item for item in old_methods if not relation_dependent(item["methodId"])]
    try:
        after = composite_key(decide(state.policy, [*kept, *current.values()])["composite"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return Precheck(True, "precheck_unavailable")
    return Precheck(True, "composite_changed") if after != before else Precheck(False, "composite_unchanged")


def ordered_unique(values: Iterable[Any]) -> list[Any]:
    """처음 나온 순서를 지키며 중복을 뺀다(오래 기다린 거래 먼저)."""
    seen: set[Any] = set()
    return [value for value in values if not (value in seen or seen.add(value))]
