"""소프트룰 임계값 일괄 추천(`recommend_thresholds`, llm-recommendation-job.md).

숫자는 코드가 계산하고 LLM은 상태와 근거 문장만 낸다. `mode=ai_proposal`은 예약만 있어 실행하지 않는다.
`llm_spec.model_version`으로 경로를 고른다: `rule_v2`(v0.2 번들, train 전체 p95) / `rule_v3`(Rule v3 묶음,
train을 원천 시각으로 80/20 나눈 참조 p95, 한국어 근거, 번들 검증·수리 1회).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from generation_client import GenerationRejected
from methods.registry import SOFT_RULE_FIELDS_BY_ID, SoftRuleField
from research_bundle import policy
from research_design import adapter_sha, checked_generate, rule_request, summary
from threshold_values import ThresholdSerializationError, display_threshold, exact_text, fixed_threshold

CONTRACT_VERSION = "threshold-recommendation-v1"
EVIDENCE_VERSION = "soft-rule-threshold-v1"
PROVIDER = "rule_v2"
PROVIDER_V3 = "rule_v3"
EVIDENCE_VERSION_V3 = "soft-rule-threshold-v2"
# 작업 llm_spec의 (model_version → evidence_version). 이 밖의 조합은 recommendation_llm_spec_invalid.
PROVIDERS = {PROVIDER: EVIDENCE_VERSION, PROVIDER_V3: EVIDENCE_VERSION_V3}
MINIMUM_N_V3 = 20
# 항목 `basis`: 임계값(p95)을 계산한 표본. 화면 문구는 WebUI 몫이고 계약에는 코드 값만 둔다.
BASIS_V2 = "train_p95"
BASIS_V3 = "train_reference_80_p95"
MODE_DATA_P95 = "data_p95_with_llm_rationale"
MODE_AI_PROPOSAL = "ai_proposal"
EXCLUSION_RULE = "status=S, explicit anomaly labels excluded; unlabeled retained"
MAX_TARGETS = 32
RATIONALE_MAX = 500
STATUSES = ("proposed", "request_evidence", "deferred", "no_applicable", "rejected")
# 전 항목이 이 사유면 생성계 전체 문제로 보고 작업을 실패시킨다. 직렬화 실패는 데이터 사실이라 빠진다.
GENERATION_REASONS = frozenset({"invalid_generation_output", "expected_target_mismatch", "factual_review_failed"})
# 룰 세트 contentHashVersion으로 받는 값(rule-set.md, Detector `HASH_VERSIONS`와 같은 집합). 워커는 hash를
# 다시 계산하지 않고 값을 결과 `ruleSet`에 그대로 옮기므로 버전별 직렬화 차이를 알 필요가 없다.
RULE_SET_HASH_VERSIONS = (1, 2, 3)
_HASH = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")


@dataclass(frozen=True)
class ThresholdPlan:
    mode: str
    rule_set: dict[str, Any]
    targets: dict[str, list[dict[str, Any]]]  # fieldId -> 그 필드의 룰(id 사전순)
    provider: str = PROVIDER


def _rule_set(spec: Mapping[str, Any]) -> dict[str, Any]:
    revision = spec.get("ruleSetRevision")
    content_hash = spec.get("ruleSetContentHash")
    version = spec.get("ruleSetContentHashVersion")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0 or \
            not isinstance(content_hash, str) or not _HASH.fullmatch(content_hash) or \
            type(version) is not int or version not in RULE_SET_HASH_VERSIONS:
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
    provider = llm_spec.get("model_version") if isinstance(llm_spec, Mapping) else None
    if not isinstance(provider, str) or provider not in PROVIDERS or \
            llm_spec.get("evidence_version") != PROVIDERS[provider] or \
            llm_spec.get("output_schema_version") != CONTRACT_VERSION:
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
    return ThresholdPlan(mode, _rule_set(spec), _targets(spec), provider)


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
              "displayThreshold": None, "unit": field.unit, "source": "data_p95", "basis": BASIS_V2,
              "aiProposal": None}
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


Progress = Callable[[int, int], None]


def _counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    statuses = [item["status"] for item in items]
    return {"targets": len(items), "proposed": statuses.count("proposed"),
            "requestEvidence": statuses.count("request_evidence"), "deferred": statuses.count("deferred"),
            "noApplicable": statuses.count("no_applicable"), "rejected": statuses.count("rejected")}


def recommend(plan: ThresholdPlan, rows: Sequence[Sequence[Any]], snapshot_id: Any,
              time_basis: str | None, *, source_tz: str | None = None,
              progress: Progress | None = None) -> dict[str, Any]:
    """필드당 1건을 계산한다. `GenerationUnavailable`은 잡지 않아 부분 결과 없이 작업 전체가 실패한다.

    `progress(처리한 필드 수, 전체 필드 수)`는 시작(0)과 필드마다 부른다. `source_tz`는 rule_v3 분할에만 쓴다.
    """
    if plan.provider == PROVIDER_V3:
        return _recommend_v3(plan, rows, snapshot_id, time_basis, source_tz, progress)
    root = os.environ["RESEARCH_BUNDLE_ROOT"]
    validator = policy(Path(root))
    fields = [SOFT_RULE_FIELDS_BY_ID[name] for name in sorted(plan.targets)]
    sample, excluded = reference_sample(rows, fields)
    items = []
    for field in fields:
        if progress:
            progress(len(items), len(fields))
        items.append(_item(validator, plan, field, sample, excluded[field.field_id]))
    if progress:
        progress(len(items), len(fields))
    return {"recommendationContractVersion": CONTRACT_VERSION, "mode": plan.mode, "ruleSet": plan.rule_set,
            "data": {"snapshotId": str(snapshot_id), "split": "train", "timeBasis": time_basis,
                     "exclusionRule": EXCLUSION_RULE, "rowCount": len(rows)},
            "heldWindowExclusion": None,  # rule_v2에는 보류 창 회피가 없다
            "provider": PROVIDER, "adapterSha": adapter_sha(root, "rule"), "items": items, "counts": _counts(items)}


# ---- rule_v3 ----

def _v3_runtime() -> Any:
    import generation_client
    from rule_v3_runtime import RuleV3NotConfigured
    runtime = getattr(generation_client.local_model(), "rule_v3", None)
    if runtime is None:
        raise RuleV3NotConfigured()
    return runtime


def _korean_reason(count: int, rows: int, constant: Any) -> str | None:
    """LLM을 부르지 않는 데이터 사실(계약: 값 없음·표본 부족·분산 없음 → no_applicable). 코드가 쓰는 한국어 문장."""
    if count == 0:
        return f"참조 기간 {rows}행에서 계산할 수 있는 값이 없어 백분위 임계값을 만들 수 없습니다."
    if count < MINIMUM_N_V3:
        return f"참조 기간의 유효 값이 {count}개라 백분위 임계값에 필요한 {MINIMUM_N_V3}개에 못 미칩니다."
    if constant is not None:
        return f"참조 기간의 유효 값 {count}개가 모두 {exact_text(constant)}라 백분위 임계값으로 구분할 수 없습니다."
    return None


def _item_v3(runtime: Any, plan: ThresholdPlan, field: SoftRuleField, split: Mapping[str, Any],
             work: Path, snapshot_id: Any) -> dict[str, Any]:
    import rule_v3_capture as capture
    import rule_v3_runtime as v3
    rules = plan.targets[field.field_id]
    reference = [capture.field_value(field.field_id, row) for row in split["reference"]]
    valid = [value for value in reference if value is not None]
    p95 = capture.p95(valid)
    current_n = sum(capture.field_value(field.field_id, row) is not None for row in split["current"])
    closed = {"field": field.field_id, "softRuleIds": [rule["id"] for rule in rules],
              "currentThreshold": rules[0].get("threshold"), "operator": None, "threshold": None,
              "displayThreshold": None, "unit": field.unit, "source": "data_p95", "basis": BASIS_V3,
              "aiProposal": None}
    evidence = {"n": len(valid), "min": float(min(valid)) if valid else None,
                "max": float(max(valid)) if valid else None, "p95": float(p95) if p95 is not None else None,
                "quantileMethod": "linear_(n-1)*p", "exclusionRule": EXCLUSION_RULE,
                "measurementId": None, "baselineId": None, "currentN": current_n}
    passed = {"schema": True, "facts": True, "expectedTarget": True}
    reason = _korean_reason(len(valid), len(reference), valid[0] if valid and len(set(valid)) == 1 else None)
    if reason:
        return {**closed, "status": "no_applicable", "rationale": reason, "reviews": passed,
                "evidence": evidence, "attempts": 0}
    package = runtime.package
    case = capture.write_case(work / field.field_id / "case", field.field_id, split,
                              {"dataset_id": f"snapshot_{snapshot_id}_{field.field_id}",
                               "origin": "snapshot train members, source-time split", "field": field.field_id})
    try:
        run = runtime.run_field(case, work / field.field_id / "rule")
    except TimeoutError as exc:
        from generation_client import GenerationUnavailable
        raise GenerationUnavailable("generation_timeout") from exc
    except package.io.InputFailure as exc:
        raise ValueError("recommendation_rule_input_rejected") from exc
    except package.io.ModelFailure as exc:
        raise ValueError("rule_v3_model_failure") from exc
    if any(record.get("adapterSha") != runtime.adapter_sha for record in run["attempts"]):
        # 어댑터 불일치는 이 필드가 아니라 작업 전체의 구성 문제다.
        raise ValueError("generation_adapter_version_mismatch")
    result = run["result"]
    record = result["input"]
    baseline = record["baseline"]
    evidence = {**evidence, "measurementId": record["measurement_id"], "baselineId": baseline.get("id")}
    item = {**closed, "status": "rejected", "rationale": None, "evidence": evidence,
            "attempts": len(result["attempts"])}
    target_failed = {"schema": True, "facts": True, "expectedTarget": False}
    if baseline.get("state") != "verified" or baseline.get("reference_valid_n") != len(valid) or \
            capture.canonical_number(baseline.get("value")) != capture.canonical_number(p95):
        # 번들 기준선과 서버 코드 p95가 다르면 임계값 출처를 정할 수 없다.
        return {**item, "rejectedReason": "expected_target_mismatch", "reviews": target_failed}
    if result["status"] != "rule_verified":
        code, reviews = v3.classify(result["attempts"][-1]["errors"] if result["attempts"] else [])
        return {**item, "rejectedReason": code, "reviews": reviews}
    decision = result["prediction"]["decisions"][0]
    rationale = decision["reason"].strip()
    checks = v3.rationale_checks(rationale, record)
    if not rationale or len(rationale) > v3.RATIONALE_MAX or not all(checks.values()):
        return {**item, "rejectedReason": "factual_review_failed",
                "reviews": {"schema": True, "facts": False, "expectedTarget": True}}
    state = decision["state"]
    if state != "proposed":
        return {**item, "status": state, "rationale": rationale, "reviews": passed}
    if decision["condition"] != {"op": "gt", "value": baseline["value"]}:
        return {**item, "rejectedReason": "expected_target_mismatch", "reviews": target_failed}
    try:
        threshold = fixed_threshold(p95, field.value_type, field.display_precision, field.display_precision_mode)
    except ThresholdSerializationError as exc:
        return {**item, "rejectedReason": str(exc), "reviews": passed}
    return {**item, "status": "proposed", "operator": "gt", "threshold": threshold,
            "displayThreshold": display_threshold(p95, field.display_precision, field.display_precision_mode),
            "rationale": rationale, "reviews": passed}


def _recommend_v3(plan: ThresholdPlan, rows: Sequence[Sequence[Any]], snapshot_id: Any, time_basis: str | None,
                  source_tz: str | None, progress: Progress | None) -> dict[str, Any]:
    import rule_v3_capture as capture
    runtime = _v3_runtime()
    fields = [SOFT_RULE_FIELDS_BY_ID[name] for name in sorted(plan.targets)]
    try:
        # 보류 창과 겹치면 키트 회피 규칙으로 행을 빼고 진행한다(뺀 행 수는 heldWindowExclusion).
        split, exclusion = capture.split_avoiding_held(rows, source_tz)
    except ValueError as exc:
        # 거래 묶음·시간 블록이 모자라 80/20으로 나눌 수 없거나, 보류 창을 빼면 남는 쪽이 없다.
        raise ValueError("recommendation_input_empty") from exc
    # 행 값 CSV·번들 산출물은 컨테이너 임시 폴더에만 두고 작업이 끝나면 지운다.
    work = Path(tempfile.mkdtemp(prefix="rule-v3-"))
    try:
        items = []
        for field in fields:
            if progress:
                progress(len(items), len(fields))
            items.append(_item_v3(runtime, plan, field, split, work, snapshot_id))
        if progress:
            progress(len(items), len(fields))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    counts = split["counts"]
    return {"recommendationContractVersion": CONTRACT_VERSION, "mode": plan.mode, "ruleSet": plan.rule_set,
            "data": {"snapshotId": str(snapshot_id), "split": "train", "timeBasis": time_basis,
                     "exclusionRule": EXCLUSION_RULE, "rowCount": len(rows),
                     "referenceRatio": capture.REFERENCE_RATIO, "referenceRows": counts["referenceRows"],
                     "currentRows": counts["currentRows"]},
            "heldWindowExclusion": exclusion,
            "provider": PROVIDER_V3, "adapterSha": runtime.adapter_sha, "items": items, "counts": _counts(items)}


def all_rejected(result: Mapping[str, Any]) -> bool:
    """모든 항목이 생성계 사유로 거절됐는가. 하나라도 다른 상태·사유가 있으면 작업은 성공이다."""
    return all(item["status"] == "rejected" and
               (item["rejectedReason"] in GENERATION_REASONS or item["rejectedReason"].startswith("generation_failed:"))
               for item in result["items"])
