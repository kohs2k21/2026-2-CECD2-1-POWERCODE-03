"""소프트룰 임계값 일괄 추천(`recommend_thresholds`, llm-recommendation-job.md).

숫자는 코드가 계산하고 LLM은 상태와 근거 문장만 낸다. `mode=ai_proposal`은 예약만 있어 실행하지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from generation_client import GenerationRejected
from methods.registry import SOFT_RULE_FIELDS_BY_ID, SoftRuleField
from research_bundle import policy
from research_design import adapter_sha, checked_generate, rule_request, summary
from threshold_values import ThresholdSerializationError, display_threshold, exact_text, fixed_threshold

CONTRACT_VERSION = "threshold-recommendation-v1"
EVIDENCE_VERSION = "soft-rule-threshold-v1"
PROVIDER = "rule_v2"
MODE_DATA_P95 = "data_p95_with_llm_rationale"
MODE_AI_PROPOSAL = "ai_proposal"
EXCLUSION_RULE = "status=S, explicit anomaly labels excluded; unlabeled retained"
MAX_TARGETS = 32
RATIONALE_MAX = 500
STATUSES = ("proposed", "request_evidence", "deferred", "no_applicable", "rejected")
# 전 항목이 이 사유면 생성계 전체 문제로 보고 작업을 실패시킨다. 직렬화 실패는 데이터 사실이라 빠진다.
GENERATION_REASONS = frozenset({"invalid_generation_output", "expected_target_mismatch", "factual_review_failed"})
_HASH = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")


@dataclass(frozen=True)
class ThresholdPlan:
    mode: str
    rule_set: dict[str, Any]
    targets: dict[str, list[dict[str, Any]]]  # fieldId -> 그 필드의 룰(id 사전순)


def _rule_set(spec: Mapping[str, Any]) -> dict[str, Any]:
    revision = spec.get("ruleSetRevision")
    content_hash = spec.get("ruleSetContentHash")
    version = spec.get("ruleSetContentHashVersion")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or \
            not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash) or \
            isinstance(version, bool) or version not in (1, 2):
        raise ValueError("invalid_rule_set_reference")
    return {"revision": revision, "contentHash": content_hash, "contentHashVersion": version}


def _targets(spec: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rules = spec.get("targets")
    scope = spec.get("scope")
    if not isinstance(rules, list) or not isinstance(scope, Mapping) or \
            not all(isinstance(rule, Mapping) and isinstance(rule.get("id"), str) and
                    isinstance(rule.get("field"), str) for rule in rules):
        raise ValueError("invalid_recommendation_targets")
    selected = scope.get("softRuleIds")
    if selected is not None:
        if not isinstance(selected, list):
            raise ValueError("invalid_recommendation_targets")
        rules = [rule for rule in rules if rule["id"] in selected]
    if not rules:
        raise ValueError("soft_rules_empty")
    if len(rules) > MAX_TARGETS:
        raise ValueError("soft_rule_limit_exceeded")
    if len({rule["id"] for rule in rules}) != len(rules):
        raise ValueError("invalid_recommendation_targets")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in sorted(rules, key=lambda item: item["id"]):
        field = SOFT_RULE_FIELDS_BY_ID.get(rule["field"])
        if field is None or field.status != "supported":
            raise ValueError("soft_rule_field_unknown:" + rule["field"])
        grouped.setdefault(rule["field"], []).append(dict(rule))
    return grouped


def parse_job(spec: Mapping[str, Any], llm_spec: Mapping[str, Any] | None) -> ThresholdPlan:
    """`input_spec`·`llm_spec`을 스냅샷을 만들기 전에 검사한다. 오류는 종료 코드로 끝난다."""
    mode = MODE_DATA_P95 if spec.get("mode") is None else spec["mode"]
    if mode == MODE_AI_PROPOSAL:
        raise ValueError("recommendation_mode_not_available")
    if mode != MODE_DATA_P95:
        raise ValueError("recommendation_mode_invalid")
    if not isinstance(llm_spec, Mapping) or llm_spec.get("evidence_version") != EVIDENCE_VERSION or \
            llm_spec.get("output_schema_version") != CONTRACT_VERSION or llm_spec.get("model_version") != PROVIDER:
        raise ValueError("recommendation_llm_spec_invalid")
    data = spec.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("recommendation_data_range_missing")
    if not data.get("snapshotId"):
        if not data.get("from") or not data.get("until"):
            raise ValueError("recommendation_data_range_missing")
        # 스냅샷 생성은 persisted_at 기간만 지원한다. 다른 기준을 조용히 바꿔 계산하지 않는다.
        if data.get("timeBasis") not in (None, "persisted_at"):
            raise ValueError("recommendation_time_basis_unsupported")
    return ThresholdPlan(mode, _rule_set(spec), _targets(spec))


def reference_sample(rows: Sequence[Sequence[Any]], fields: Sequence[SoftRuleField]
                     ) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """train 멤버에서 status=S·이상 라벨 제외 표본을 고르고 필드 값을 계산한다.

    값이 없는 행은 `sourceMissingPolicy`에 따라 not_applicable·unavailable로 세고 표본에서 뺀다.
    """
    sample: list[dict[str, Any]] = []
    excluded = {field.field_id: {"not_applicable": 0, "unavailable": 0} for field in fields}
    for _, raw, _, label_evidence in rows:
        label = label_evidence.get("label") if isinstance(label_evidence, dict) else None
        if raw.get("status") != "S" or label == "anomaly":
            continue
        values = {}
        for field in fields:
            result = field.evaluate(raw)
            values[field.field_id] = result.value
            if result.status != "available":
                excluded[field.field_id][result.status] += 1
        sample.append({"features": values, "label": label})
    return sample, excluded


def _definition(field: SoftRuleField) -> dict[str, Any]:
    return {"name": field.field_id,
            "expression": {"op": "derived", "ast": field.ast, "outputType": "number", "unit": field.unit}}


def _no_applicable_reason(request: Mapping[str, Any], excluded: Mapping[str, int]) -> str | None:
    """기준선을 세울 수 없는 데이터 사실이면 문장을, 아니면 None을 준다. 이 경우 LLM을 부르지 않는다."""
    measurement, baseline = request["measurement"], request["baseline"]
    count = measurement["valid_count"]
    if baseline["state"] != "verified":
        if count == 0:
            return (f"No usable reference values: {excluded['not_applicable']} rows are not applicable "
                    f"and {excluded['unavailable']} rows could not be computed.")
        if "minimum_reference_sample" in baseline["needed"]:
            return f"Only {count} reference values are available; a percentile threshold needs at least 20."
        return "The unit of this field is not verified, so a percentile threshold cannot be compared."
    if measurement["variation"] == "constant":
        return f"All {count} reference values are {exact_text(measurement['p95'])}; " \
               "a percentile threshold has no discriminative value."
    return None


def _rejected(item: dict[str, Any], reason: str, reviews: dict[str, bool]) -> dict[str, Any]:
    return {**item, "status": "rejected", "rejectedReason": reason, "rationale": None, "reviews": reviews}


def _generation_failure(code: str) -> str:
    if code in ("invalid_generation_response", "invalid_generation_output"):
        return "invalid_generation_output"
    return "generation_failed:" + (code if _CODE.fullmatch(code) else "generation_call_rejected")


def _item(validator: Any, plan: ThresholdPlan, field: SoftRuleField, sample: Sequence[Mapping[str, Any]],
          excluded: Mapping[str, int]) -> dict[str, Any]:
    rules = plan.targets[field.field_id]
    closed = {"field": field.field_id, "softRuleIds": [rule["id"] for rule in rules],
              "currentThreshold": rules[0].get("threshold"), "operator": None, "threshold": None,
              "displayThreshold": None, "unit": field.unit, "source": "data_p95", "aiProposal": None}
    passed = {"schema": True, "facts": True, "expectedTarget": True}

    def evidence(measured: Mapping[str, Any], p95: Any, measurement_id: str | None, baseline_id: str | None) -> dict[str, Any]:
        return {"n": measured["valid_count"], "min": measured["min"], "max": measured["max"], "p95": p95,
                "quantileMethod": measured["quantile_method"], "exclusionRule": EXCLUSION_RULE,
                "measurementId": measurement_id, "baselineId": baseline_id}

    # 값이 유한해도 양 끝이 극단이면 분위 보간이 넘친다. 비유한값은 hash도 JSON도 만들 수 없어 먼저 거른다.
    measured = summary([row["features"][field.field_id] for row in sample])
    if measured["p95"] is not None and not math.isfinite(measured["p95"]):
        return _rejected({**closed, "evidence": evidence(measured, None, None, None)},
                         "threshold_serialization_failed", passed)
    request = rule_request(sample, _definition(field), f"rule-set-r{plan.rule_set['revision']}", EXCLUSION_RULE, exact_text)
    measurement = request["measurement"]
    reason = _no_applicable_reason(request, excluded)
    item = {**closed, "status": "no_applicable", "rationale": reason, "reviews": passed,
            "evidence": evidence(measurement, measurement["p95"], request["measurement_id"],
                                 None if reason else request["baseline"]["id"])}
    if reason:
        return item
    failed = {"schema": False, "facts": False, "expectedTarget": False}
    try:
        receipt = checked_generate("rule", request, report_timeout=True)
    except GenerationRejected as exc:
        return _rejected(item, _generation_failure(exc.code), failed)
    except json.JSONDecodeError:
        return _rejected(item, "invalid_generation_output", failed)
    except ValueError as exc:
        # 어댑터 불일치는 이 필드가 아니라 작업 전체의 구성 문제다.
        if str(exc) == "generation_adapter_version_mismatch":
            raise
        return _rejected(item, _generation_failure(str(exc)), failed)
    output = receipt.get("output")
    if receipt.get("truncated") is not False or not isinstance(output, dict):
        return _rejected(item, "invalid_generation_output", failed)
    validation = validator.validate_rule(request, output)
    if not validation["schema_passed"]:
        return _rejected(item, "invalid_generation_output", failed)
    facts = validator.factual_review(request, output)
    reviews = {"schema": True, "facts": facts["passed"], "expectedTarget": validation["passed"]}
    if not validation["passed"]:
        return _rejected(item, "expected_target_mismatch", reviews)
    if not facts["passed"]:
        return _rejected(item, "factual_review_failed", reviews)
    rationale = output["reason"].strip()
    if not rationale or len(rationale) > RATIONALE_MAX:
        return _rejected(item, "invalid_generation_output", {**reviews, "schema": False})
    if output["state"] != "proposed":
        return {**item, "status": output["state"], "rationale": rationale, "reviews": reviews}
    p95 = measurement["p95"]
    try:
        threshold = fixed_threshold(p95, field.value_type, field.display_precision, field.display_precision_mode)
    except ThresholdSerializationError as exc:
        return _rejected(item, str(exc), reviews)
    return {**item, "status": "proposed", "operator": request["reference_policy"]["operator"],
            "threshold": threshold,
            "displayThreshold": display_threshold(p95, field.display_precision, field.display_precision_mode),
            "rationale": rationale, "reviews": reviews}


def recommend(plan: ThresholdPlan, rows: Sequence[Sequence[Any]], snapshot_id: Any,
              time_basis: str | None) -> dict[str, Any]:
    """필드당 1건을 계산한다. `GenerationUnavailable`은 잡지 않아 부분 결과 없이 작업 전체가 실패한다."""
    root = os.environ["RESEARCH_BUNDLE_ROOT"]
    validator = policy(Path(root))
    fields = [SOFT_RULE_FIELDS_BY_ID[name] for name in sorted(plan.targets)]
    sample, excluded = reference_sample(rows, fields)
    items = [_item(validator, plan, field, sample, excluded[field.field_id]) for field in fields]
    statuses = [item["status"] for item in items]
    return {"recommendationContractVersion": CONTRACT_VERSION, "mode": plan.mode, "ruleSet": plan.rule_set,
            "data": {"snapshotId": str(snapshot_id), "split": "train", "timeBasis": time_basis,
                     "exclusionRule": EXCLUSION_RULE, "rowCount": len(rows)},
            "provider": PROVIDER, "adapterSha": adapter_sha(root, "rule"), "items": items,
            "counts": {"targets": len(items), "proposed": statuses.count("proposed"),
                       "requestEvidence": statuses.count("request_evidence"), "deferred": statuses.count("deferred"),
                       "noApplicable": statuses.count("no_applicable"), "rejected": statuses.count("rejected")}}


def all_rejected(result: Mapping[str, Any]) -> bool:
    """모든 항목이 생성계 사유로 거절됐는가. 하나라도 다른 상태·사유가 있으면 작업은 성공이다."""
    return all(item["status"] == "rejected" and
               (item["rejectedReason"] in GENERATION_REASONS or item["rejectedReason"].startswith("generation_failed:"))
               for item in result["items"])
