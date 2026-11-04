"""v2 정책 판정의 evidence v4와 중간 판정 기록 payload를 조립한다. DB를 읽지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Mapping

from detector.decision import decide, requested_mode
from detector.hard_rules import HardRuleSelection, PROCESS_STATUS, status_rule_policy
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


def _check_hard_rules(methods: list[Mapping[str, Any]], hard_rules: HardRuleSelection) -> None:
    """기록할 정책이 방법을 평가한 입력과 다르면 판정을 남기지 않는다."""
    active = tuple(method["methodId"] for method in methods if method["category"] == "hard")
    levels = hard_rules.status_levels
    if active != hard_rules.enabled_ids or any(
            method["methodId"] == PROCESS_STATUS and method["anomalous"] is True and
            method["severity"] != levels.get(method["code"]) for method in methods):
        raise ValueError("hard_rule_selection_mismatch")


def build_evidence(policy: PolicyV2, method_results: Iterable[Mapping[str, Any]], revisions: PinnedRevisions,
                   hard_rules: HardRuleSelection, *, source_id: Any, telemetry: Mapping[str, Any],
                   observation: Mapping[str, Any], input_manifest_hash: str, demo: Mapping[str, Any] | None = None,
                   input_origin: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """최종 판정 evidence. decision_columns에 그대로 넘겨 판정 행 열 값을 얻는다."""
    decided = decide(policy, method_results)
    methods, categories = decided["methodResults"], decided["categoryResults"]
    if any(method["cause"] == "pending" for method in methods):
        raise ValueError("final_decision_has_pending_method")
    _check_hard_rules(methods, hard_rules)
    status_method = next((method for method in methods if method["methodId"] == PROCESS_STATUS), None)
    status_policy = status_rule_policy(source_id, hard_rules) if status_method else None
    rule_methods = [method for method in methods if method["category"] != "ml"]
    ml_methods = [method for method in methods if method["category"] == "ml"]
    rules = [_rule_entry(method, source_id, revisions.rule_set_revision) for method in rule_methods]
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
        "ruleSeverities": [method["severity"] for method in rule_methods if method["anomalous"] is True],
        "ruleSetRevision": revisions.rule_set_revision, "ruleSetHash": revisions.rule_set_hash,
        **decided, "outcomes": {"classificationVersion": OUTCOME_CLASSIFICATION},
        "telemetry": telemetry, "statusRulePolicy": status_policy, "observation": observation,
        "inputManifestHash": input_manifest_hash}
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
