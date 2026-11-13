"""판정 1건의 관계 입력·과제 사전 분류(relation-rules.md 3.3, protocol-rules.md 10절 4단계). DB를 읽지 않는다.

- 관계 규칙·`hard:transaction-failed`·프로토콜 규칙의 "수집 미완료 보류·적용 안 함·`deferred_to_transaction`"
  분류는 `execute_methods` 전에 시도 시작 때 읽은 입력으로 끝낸다. 그 규칙은 과제를 만들지 않고 evidence
  `relationNotes`에만 남는다 — 중간·최종 판정의 n이 같아지고, 하드룰 대조 집합(evidence_v4)도 이 계획을 따른다.
- 판정이 있는 v3 요소: `hard:transaction-failed`(4단계), 관계 규칙(5단계, relation_rules.py), 프로토콜 규칙과
  `hard:unregistered-interface`(6단계, protocol_rules.py). 프로토콜 기능(켜진 항목 1개 이상)이 꺼져 있으면
  `hard:unregistered-interface`는 켜져 있어도 기록 없이 빠진다(protocol-rules.md 4.1 첫 행).
- 소프트룰 scope 선택은 관계 입력이 필요 없어 여기서 하지 않는다(attempt_v04 `_tasks`, soft_rules.in_scope).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from detector.hard_rules import TRANSACTION_FAILED, UNREGISTERED_INTERFACE
from detector.protocol_rules import feature_on, judge_protocol
from detector.relation_inputs import RelationInputs
from detector.relation_rules import judge_relations, note, relation_rules_need_inputs


@dataclass(frozen=True)
class RelationPlan:
    """inputs가 없으면 관계 입력이 필요한 방법이 없는 판정이다(evidence에 관계 키를 두지 않음 — v2와 같음)."""
    inputs: RelationInputs | None
    hard_ids: tuple[str, ...]  # 이 판정에서 과제로 만들 하드룰(내장 목록 순 → 관계 저장 순 → 프로토콜 저장 순)
    notes: tuple[Mapping[str, Any], ...] = ()
    # 관계·프로토콜 규칙 판정 결과(methodId → 방법 결과). 과제는 이 결과를 그대로 돌려준다(입력 평가는 시도 시작 때
    # 끝남, 3.3). 프로토콜 결과에는 `hard:unregistered-interface`도 들어갈 수 있다.
    relation_results: Mapping[str, Mapping[str, Any]] | None = None
    # 프로토콜 방법이 T일 때만 evidence.protocolReview(protocol-rules.md 5.3).
    protocol_review: Mapping[str, Any] | None = None

    def evidence_fields(self) -> dict[str, Any]:
        if self.inputs is None:
            return {}
        fields = {"relationInputs": self.inputs.evidence(), "relationNotes": [dict(note) for note in self.notes]}
        if self.protocol_review is not None:
            fields["protocolReview"] = dict(self.protocol_review)
        return fields


def needs_relation_inputs(state: Any, raw: Mapping[str, Any] | None = None) -> bool:
    """relation-rules.md 3.2: 관계 규칙·`hard:transaction-failed`가 선택됐거나 프로토콜 기능이 켜진 판정만 읽는다.

    raw(판정 관측 원래 값)가 있으면 후행 kind는 이 판정에서 골린 규칙만 센다. 켜진 관계 규칙이 없는 판정(v2 행 포함)은
    DB를 더 읽지 않는다.
    """
    return (TRANSACTION_FAILED in state.hard_rules.enabled_ids or
            relation_rules_need_inputs(getattr(state, "relation_rules", ()), raw) or
            feature_on(getattr(state, "protocol_rules", ())))


def plan_relations(state: Any, inputs: RelationInputs | None, raw: Mapping[str, Any] | None = None) -> RelationPlan:
    """켜진 하드룰 중 이 판정에서 과제로 평가할 것을 고르고, 과제가 아닌 분류를 relationNotes로 남긴다.

    순서: 내장 규칙(내장 목록 순) → 관계 규칙(저장 순) → 프로토콜 규칙. relationNotes도 같은 순서(내장 → 관계 → 프로토콜).
    """
    if needs_relation_inputs(state, raw) and inputs is None:
        raise ValueError("relation_inputs_required")
    protocol_rules = getattr(state, "protocol_rules", ())
    protocol = None
    if inputs is not None and feature_on(protocol_rules):
        enabled = state.hard_rules.enabled_ids
        unregistered = state.hard_rules.unregistered_interface_level if UNREGISTERED_INTERFACE in enabled else None
        protocol = judge_protocol(protocol_rules, unregistered, inputs,
                                  rule_set_revision=state.revisions.rule_set_revision)
    protocol_methods = dict(protocol.methods) if protocol else {}
    hard_ids, notes = [], []
    for rule_id in state.hard_rules.enabled_ids:
        if rule_id == TRANSACTION_FAILED:
            if inputs.transaction_keyed and inputs.transaction_executions == 0:
                # 거래 행은 PROCESS보다 늦게 온다(정상 경로). 재판정은 거래 행 도착 계기(10절 7단계).
                notes.append(note(TRANSACTION_FAILED, "transaction", "held", "transaction_pending", {"layer": "T"}))
                continue
        elif rule_id == UNREGISTERED_INTERFACE and rule_id not in protocol_methods:
            continue  # 이 판정에서 평가하지 않음(기능 꺼짐·붙이는 판정 아님·항목 있음 등, 4.1·5.2) → 기록 없음
        hard_ids.append(rule_id)
    results: dict[str, Mapping[str, Any]] = {}
    if inputs is not None and state.relation_rules:
        methods, relation_notes = judge_relations(state.relation_rules, inputs, raw or {})
        hard_ids.extend(method_id for method_id, _ in methods)
        results.update(methods)
        notes.extend(relation_notes)
    if protocol is not None:
        hard_ids.extend(method_id for method_id in protocol_methods if method_id != UNREGISTERED_INTERFACE)
        results.update(protocol_methods)
        notes.extend(protocol.notes)
    return RelationPlan(inputs, tuple(hard_ids), tuple(notes), results, protocol.review if protocol else None)
