"""v2 정책 판정의 evidence v4와 중간 판정 기록 payload를 조립한다. DB를 읽지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Mapping

from detector.decision import decide, requested_mode
from detector.hard_rules import (CONDITIONAL_HARD_RULE_IDS, HARD_RULE_ORDER, TRANSACTION_FAILED,
                                 UNREGISTERED_INTERFACE, HardRuleSelection, PROCESS_STATUS, is_protocol_rule_id,
                                 is_relation_rule_id, status_rule_policy)
from detector.policy import PolicyV2

SCHEMA_VERSION = 4
INTERIM_EVIDENCE_VERSION = "detector-evidence-v4"
OUTCOME_CLASSIFICATION = "detector-outcomes-v1"
RULE_SET_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PinnedRevisions:
    """시도 시작 때 함께 고정한 룰 세트·정책·활성 집합 리비전."""
    rule_set_revision: int
    rule_set_hash: str
    detection_policy_revision: int
    active_model_set_revision: int

    def __post_init__(self):
        numbers = (self.rule_set_revision, self.detection_policy_revision, self.active_model_set_revision)
        if any(type(value) is not int or value < 0 for value in numbers) or \
                not isinstance(self.rule_set_hash, str) or not RULE_SET_HASH.fullmatch(self.rule_set_hash):
            raise ValueError("invalid_pinned_revisions")


def _rule_entry(method: Mapping[str, Any], source_id: Any, rule_set_revision: int) -> dict[str, Any]:
    """v3 판독기가 쓰는 rules[] 형태. reason은 상태 규칙 외에는 not_applicable일 때만 둔다."""
    summary = method["evidence"][0] if method["evidence"] else {}
    result = "hit" if method["anomalous"] is True else "unavailable" if method["status"] != "succeeded" else \
        "not_applicable" if summary.get("result") == "not_applicable" else "no_hit"
    if method["methodId"] == PROCESS_STATUS:
        return {"ruleKey": "process-status", "version": method["methodVersion"], "sourceId": str(source_id),
                "observedStatus": summary.get("observedStatus"), "result": result, "severity": method["severity"],
                "reason": summary.get("reason") if result == "not_applicable" else
                "status_unknown" if result == "unavailable" else None}
    entry = {"ruleKey": method["methodId"].split(":", 1)[1],
             "version": method["methodVersion"] if method["category"] == "hard" else rule_set_revision,
             "result": result}
    return {**entry, "reason": summary["reason"]} if result == "not_applicable" else entry


def _expected_level(method: Mapping[str, Any], hard_rules: HardRuleSelection) -> str | None:
    """T 방법의 기대 위험도. 내장(상태·거래 실패·미등록 인터페이스)은 그 판정 입력에서, 관계·프로토콜 규칙은 시도에
    고정한 켜진 규칙의 severity에서 찾는다(없으면 None → 대조 실패)."""
    method_id = method["methodId"]
    if method_id == PROCESS_STATUS:
        return hard_rules.status_levels.get(method["code"])
    if method_id == UNREGISTERED_INTERFACE:
        return hard_rules.unregistered_interface_level
    if is_relation_rule_id(method_id):
        return dict(hard_rules.relation_rule_levels).get(method_id)
    if is_protocol_rule_id(method_id):
        return dict(hard_rules.protocol_rule_levels).get(method_id)
    summary = method["evidence"][0] if method["evidence"] else {}
    code = summary.get("responseCode") if isinstance(summary, Mapping) else None
    return hard_rules.transaction_failed_level(code) if isinstance(code, str) else None


def _check_hard_rules(methods: list[Mapping[str, Any]], hard_rules: HardRuleSelection,
                      evaluated: tuple[str, ...]) -> None:
    """기록할 정책이 방법을 평가한 입력과 다르면 판정을 남기지 않는다.

    대조 집합 = 이 판정에서 과제로 평가한 하드룰(relation_plan). 그중 내장 규칙은 켜진 내장 규칙과 같아야 하고,
    빠질 수 있는 것은 판정마다 평가가 달라지는 규칙(거래 실패 수집 미완료 보류, 미등록 인터페이스)뿐이다.
    위험도 일치는 사용자가 위험도를 편집하는 규칙 — 상태 규칙·거래 실패·미등록 인터페이스와 관계·프로토콜 규칙(규칙
    severity) — 을 본다. 관계·프로토콜 규칙은 시도에 고정한 켜진 규칙이어야 하고 그 판정에서 고른 것만 들어온다
    (relation-rules.md 9절, protocol-rules.md 5.2). 프로토콜 방법(hard:proto-*·미등록 인터페이스)은 판정마다 많아야 1개.
    """
    active = tuple(method["methodId"] for method in methods if method["category"] == "hard")
    built_in = tuple(method_id for method_id in evaluated if method_id in HARD_RULE_ORDER)
    expected_built_in = tuple(method_id for method_id in hard_rules.enabled_ids
                              if method_id in built_in or method_id not in CONDITIONAL_HARD_RULE_IDS)
    relation_levels = dict(hard_rules.relation_rule_levels)
    protocol_levels = dict(hard_rules.protocol_rule_levels)
    relation = tuple(method_id for method_id in evaluated if is_relation_rule_id(method_id))
    protocol = tuple(method_id for method_id in evaluated
                     if is_protocol_rule_id(method_id) or method_id == UNREGISTERED_INTERFACE)
    edited = {PROCESS_STATUS, TRANSACTION_FAILED, UNREGISTERED_INTERFACE, *relation, *protocol}
    if active != evaluated or built_in != expected_built_in or len(protocol) > 1 or any(
            method_id not in relation_levels for method_id in relation) or any(
            is_protocol_rule_id(method_id) and method_id not in protocol_levels for method_id in protocol) or any(
            method["methodId"] in edited and method["anomalous"] is True and
            method["severity"] != _expected_level(method, hard_rules) for method in methods):
        raise ValueError("hard_rule_selection_mismatch")


def _protocol_hit(methods: list[Mapping[str, Any]], review: Mapping[str, Any]) -> bool:
    """protocolReview가 가리키는 방법(항목 id, 미등록이면 내장 규칙)이 최종 결과에서 T인가."""
    method_id = review.get("protocolRuleId") or UNREGISTERED_INTERFACE
    return any(method["methodId"] == method_id and method["anomalous"] is True for method in methods)


def _projected(method: Mapping[str, Any]) -> bool:
    """v3 투영 rules[]·ruleSeverities 대상: 소프트룰과 내장 하드룰. 관계·프로토콜 규칙과 미등록 인터페이스는
    v3 판독기가 모르는 방법이라 넣지 않는다(근거는 methodResults, relation-rules.md 9절·protocol-rules.md 5.2)."""
    return method["category"] == "soft" or (method["methodId"] in HARD_RULE_ORDER and
                                            method["methodId"] != UNREGISTERED_INTERFACE)


def build_evidence(policy: PolicyV2, method_results: Iterable[Mapping[str, Any]], revisions: PinnedRevisions,
                   hard_rules: HardRuleSelection, *, source_id: Any, telemetry: Mapping[str, Any],
                   observation: Mapping[str, Any], input_manifest_hash: str, demo: Mapping[str, Any] | None = None,
                   input_origin: Mapping[str, Any] | None = None, evaluated_hard_ids: Iterable[str] | None = None,
                   relation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """최종 판정 evidence. decision_columns에 그대로 넘겨 판정 행 열 값을 얻는다.

    evaluated_hard_ids: 이 판정에서 과제로 평가한 하드룰(없으면 켜진 내장 규칙 전부 — v2 판정과 같음).
    relation: relation_plan의 evidence 키(relationInputs·relationNotes·protocolReview). 관계 입력을 읽지 않은 판정은
    비어 있다. protocolReview는 최종 methodResults에서 그 프로토콜 방법이 T일 때만 남긴다(시간 초과·슬롯 부족으로
    결과를 내지 못하면 뺀다, protocol-rules.md 5.3).
    """
    decided = decide(policy, method_results)
    methods, categories = decided["methodResults"], decided["categoryResults"]
    if any(method["cause"] == "pending" for method in methods):
        raise ValueError("final_decision_has_pending_method")
    _check_hard_rules(methods, hard_rules,
                      hard_rules.enabled_ids if evaluated_hard_ids is None else tuple(evaluated_hard_ids))
    status_method = next((method for method in methods if method["methodId"] == PROCESS_STATUS), None)
    status_policy = status_rule_policy(source_id, hard_rules) if status_method else None
    rule_methods = [method for method in methods if method["category"] != "ml"]
    ml_methods = [method for method in methods if method["category"] == "ml"]
    projected = [method for method in rule_methods if _projected(method)]
    rules = [_rule_entry(method, source_id, revisions.rule_set_revision) for method in projected]
    models = [{"modelKey": method["methodId"].split(":", 1)[1], "anomalous": method["anomalous"]}
              for method in ml_methods]
    skipped = {"status": "skipped", "anomalous": None, "evidence": []}
    evidence = {
        "schemaVersion": SCHEMA_VERSION, "rules": rules, "models": models,
        "checkers": {
            "hr": {"status": status_method["status"], "anomalous": status_method["anomalous"],
                   "evidence": [rules[0]]} if status_method else skipped,
            "sr": dict(skipped),
            "ml": {"status": categories["ml"]["status"], "anomalous": categories["ml"]["anomalous"],
                   "evidence": models}},
        "anomalous": decided["composite"]["anomalous"], "requestedMode": requested_mode(methods),
        "actualPaths": [name for name, group in (("rule", rule_methods), ("ml", ml_methods))
                        if any(method["status"] == "succeeded" for method in group)],
        "detectionPolicy": policy.document(),
        "detectionPolicyRevision": revisions.detection_policy_revision,
        "activeModelSetRevision": revisions.active_model_set_revision,
        "ruleSeverities": [method["severity"] for method in projected if method["anomalous"] is True],
        "ruleSetRevision": revisions.rule_set_revision, "ruleSetHash": revisions.rule_set_hash,
        **decided, "outcomes": {"classificationVersion": OUTCOME_CLASSIFICATION},
        "telemetry": telemetry, "statusRulePolicy": status_policy, "observation": observation,
        "inputManifestHash": input_manifest_hash}
    if relation:
        fields = dict(relation)
        review = fields.pop("protocolReview", None)
        evidence.update(fields)
        if review is not None and _protocol_hit(methods, review):
            evidence["protocolReview"] = review
    if demo is not None:
        evidence["demo"] = demo
    if input_origin is not None:
        evidence["input"] = input_origin
    return evidence


def build_interim_payload(policy: PolicyV2, method_results: Iterable[Mapping[str, Any]],
                          revisions: PinnedRevisions, elapsed_ms: float) -> dict[str, Any]:
    """첫 이상·근거 추가 시점의 전체 스냅샷. 미도착 방법은 cause=pending으로 넘긴다."""
    if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms):
        raise ValueError("interim_elapsed_required")
    decided = decide(policy, method_results)
    if decided["composite"]["anomalous"] is not True:
        raise ValueError("interim_requires_anomaly")
    return {"schemaVersion": SCHEMA_VERSION, "ruleSetRevision": revisions.rule_set_revision,
            "ruleSetHash": revisions.rule_set_hash, "detectionPolicy": policy.document(),
            "detectionPolicyRevision": revisions.detection_policy_revision,
            "activeModelSetRevision": revisions.active_model_set_revision,
            **decided, "elapsedMs": elapsed_ms}
