"""소프트룰 임계값 일괄 추천(`recommend_thresholds`, llm-recommendation-job.md).

숫자는 코드가 계산하고 LLM은 상태와 근거 문장만 낸다. `mode=ai_proposal`은 예약만 있어 실행하지 않는다.
`llm_spec.model_version`으로 경로를 고른다: `rule_v2`(v0.2 번들, train 전체 p95) / `rule_v3`(Rule v3 묶음,
train을 원천 시각으로 80/20 나눈 참조 p95, 한국어 근거, 번들 검증·수리 1회).

대상(`input_spec.targets[]`)은 소프트룰과 Trainer 처리 시간 후보(`kind: candidate`)이고, 결과 항목 1건은
(필드, 적용 범위 `scope`) 묶음 하나다. 범위가 있는 묶음은 Detector `in_scope`와 같은 함수로 행을 거른다.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from detector.soft_rules import SCOPE_COLUMNS, in_scope
from generation_client import GenerationRejected
from generation_replicas import ReplicaUnavailable
from methods.registry import SOFT_RULE_FIELDS_BY_ID, SoftRuleField
from research_bundle import policy
from research_design import adapter_sha, checked_generate, rule_request, summary
from threshold_values import ThresholdSerializationError, display_threshold, exact_text, fixed_threshold

CONTRACT_VERSION = "threshold-recommendation-v1"
EVIDENCE_VERSION = "soft-rule-threshold-v1"
PROVIDER = "rule_v2"
PROVIDER_V3 = "rule_v3"
EVIDENCE_VERSION_V3 = "soft-rule-threshold-v2"
# 10차: 새 대상 모양(후보 대상·범위 있는 대상)을 쓰는 rule_v3 작업의 표시. 옛 워커는 이 값을 몰라 조용히 틀리지 않고
# recommendation_llm_spec_invalid로 끝난다(계약 배포 순서 절).
EVIDENCE_VERSION_V3_SCOPE = "soft-rule-threshold-v3"
# 작업 llm_spec의 model_version → 받는 evidence_version. 이 밖의 조합은 recommendation_llm_spec_invalid.
PROVIDERS = {PROVIDER: (EVIDENCE_VERSION,), PROVIDER_V3: (EVIDENCE_VERSION_V3, EVIDENCE_VERSION_V3_SCOPE)}
KIND_SOFT_RULE = "soft_rule"
KIND_CANDIDATE = "candidate"
# scope 판정 키(Detector SCOPE_COLUMNS 순서). processId는 필수(rule-set.md 소프트룰 scope 저장 검증).
SCOPE_KEYS = tuple(key for key, _ in SCOPE_COLUMNS)
MINIMUM_N_V3 = 20
# 항목 `basis`: 임계값(p95)을 계산한 표본. 화면 문구는 WebUI 몫이고 계약에는 코드 값만 둔다.
BASIS_V2 = "train_p95"
BASIS_V3 = "train_reference_80_p95"
MODE_DATA_P95 = "data_p95_with_llm_rationale"
MODE_AI_PROPOSAL = "ai_proposal"
EXCLUSION_RULE = "status=S, explicit anomaly labels excluded; unlabeled retained"
MAX_GROUPS = 32  # 결과 항목(필드 × 범위 묶음) 상한. 넘으면 soft_rule_limit_exceeded
MAX_TARGETS = 64  # 대상(소프트룰 + 후보) 상한. 넘으면 invalid_recommendation_targets
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
class TargetGroup:
    """결과 항목 1건 = 같은 (필드, scope 정규형) 대상 묶음. 소프트룰은 id 사전순, 후보 id는 요청 순서."""
    field: str
    scope: dict[str, str] | None = None
    rules: tuple[dict[str, Any], ...] = ()
    candidates: tuple[str, ...] = ()

    @property
    def scope_key(self) -> str | None:
        return _scope_key(self.scope)

    @property
    def current_threshold(self) -> Any:
        # 소프트룰이 여러 개면 첫 룰(id 사전순) 값. 후보만 있는 묶음은 null.
        return self.rules[0].get("threshold") if self.rules else None


@dataclass(frozen=True)
class ThresholdPlan:
    mode: str
    rule_set: dict[str, Any]
    groups: tuple[TargetGroup, ...]  # 결과 항목 순서: field → scope(null 먼저, 정규형 JSON 순)
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


def _scope_key(scope: Mapping[str, str] | None) -> str | None:
    """묶음 키·정렬용 정규형 JSON(키 정렬, 공백 없음). 범위 없음은 None."""
    return None if scope is None else json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _target_scope(value: Any) -> dict[str, str] | None:
    """`targets[].scope` → 정규형(판정 키 순서) 또는 None. rule-set.md v3 소프트룰 scope 정규형만 받는다.

    정규형: `processId` 필수, 키는 `processId`·`adapterType`·`processHubId` 안(표시용 `interfaceId` 없음), 값 있는 키만
    (null 값 키 없음), 값은 비지 않은 문자열. 이 밖의 모양은 고쳐 쓰지 않고 invalid_recommendation_targets.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping) or "processId" not in value or not set(value) <= set(SCOPE_KEYS) or \
            not all(isinstance(item, str) and item for item in value.values()):
        raise ValueError("invalid_recommendation_targets")
    return {key: value[key] for key in SCOPE_KEYS if key in value}


def _targets(spec: Mapping[str, Any], provider: str, evidence: Any) -> tuple[TargetGroup, ...]:
    """`targets[]` → 결과 항목 묶음. 옛 모양 `{id, field, threshold}`는 `kind: soft_rule`·`scope: null`로 읽는다.

    새 대상 모양(`kind: candidate` 또는 null 아닌 `scope`)은 rule_v3 + `soft-rule-threshold-v3`에서만 받는다.
    `scope.softRuleIds` 재필터는 소프트룰에만, `scope.candidateIds` 재필터는 후보에만 적용한다.
    """
    rules = spec.get("targets")
    scope = spec.get("scope")
    if not isinstance(rules, list) or not isinstance(scope, Mapping) or \
            not all(isinstance(rule, Mapping) and isinstance(rule.get("id"), str) and
                    isinstance(rule.get("field"), str) for rule in rules):
        raise ValueError("invalid_recommendation_targets")
    parsed: list[tuple[str, dict[str, Any], dict[str, str] | None]] = []
    for rule in rules:
        kind = rule.get("kind", KIND_SOFT_RULE)
        # 후보 값은 워커가 다시 계산하므로 threshold는 null만 받는다.
        if kind not in (KIND_SOFT_RULE, KIND_CANDIDATE) or (kind == KIND_CANDIDATE and rule.get("threshold") is not None):
            raise ValueError("invalid_recommendation_targets")
        parsed.append((kind, dict(rule), _target_scope(rule.get("scope"))))
    if any(kind == KIND_CANDIDATE or target_scope is not None for kind, _, target_scope in parsed):
        if provider != PROVIDER_V3:
            # rule_v2로 되돌린 상태에서 새 대상을 필드 전체 p95로 조용히 계산하지 않는다.
            raise ValueError("recommendation_targets_require_rule_v3")
        if evidence != EVIDENCE_VERSION_V3_SCOPE:
            raise ValueError("recommendation_llm_spec_invalid")
    selection = {KIND_SOFT_RULE: scope.get("softRuleIds"), KIND_CANDIDATE: scope.get("candidateIds")}
    if any(ids is not None and not isinstance(ids, list) for ids in selection.values()):
        raise ValueError("invalid_recommendation_targets")
    if selection[KIND_CANDIDATE] is None and any(kind == KIND_CANDIDATE for kind, _, _ in parsed):
        raise ValueError("invalid_recommendation_targets")  # 후보를 고르지 않은 요청에 후보 대상이 있다
    # softRuleIds null = 소프트룰 전체(현행). candidateIds 배열이면 그 안의 후보만.
    parsed = [item for item in parsed if selection[item[0]] is None or item[1]["id"] in selection[item[0]]]
    if not parsed:
        raise ValueError("soft_rules_empty")
    if len(parsed) > MAX_TARGETS:
        raise ValueError("invalid_recommendation_targets")
    if len({rule["id"] for _, rule, _ in parsed}) != len(parsed):
        raise ValueError("invalid_recommendation_targets")
    # 소프트룰은 id 사전순(현행 currentThreshold 규칙), 후보는 요청 순서.
    ordered = sorted((item for item in parsed if item[0] == KIND_SOFT_RULE), key=lambda item: item[1]["id"]) + \
        [item for item in parsed if item[0] == KIND_CANDIDATE]
    grouped: dict[tuple[str, str | None], tuple[dict[str, str] | None, list, list]] = {}
    for kind, rule, target_scope in ordered:
        _, soft, candidates = grouped.setdefault((rule["field"], _scope_key(target_scope)), (target_scope, [], []))
        if kind == KIND_SOFT_RULE:
            soft.append(rule)
        else:
            candidates.append(rule["id"])
    if len(grouped) > MAX_GROUPS:
        raise ValueError("soft_rule_limit_exceeded")
    for _, rule, _ in ordered:
        field = SOFT_RULE_FIELDS_BY_ID.get(rule["field"])
        if field is None or field.status != "supported":
            raise ValueError("soft_rule_field_unknown:" + rule["field"])
    keys = sorted(grouped, key=lambda key: (key[0], key[1] is not None, key[1] or ""))
    return tuple(TargetGroup(key[0], grouped[key][0], tuple(grouped[key][1]), tuple(grouped[key][2])) for key in keys)


def parse_job(spec: Mapping[str, Any], llm_spec: Mapping[str, Any] | None) -> ThresholdPlan:
    """`input_spec`·`llm_spec`을 스냅샷을 만들기 전에 검사한다. 오류는 종료 코드로 끝난다."""
    mode = MODE_DATA_P95 if spec.get("mode") is None else spec["mode"]
    if mode == MODE_AI_PROPOSAL:
        raise ValueError("recommendation_mode_not_available")
    if mode != MODE_DATA_P95:
        raise ValueError("recommendation_mode_invalid")
    provider = llm_spec.get("model_version") if isinstance(llm_spec, Mapping) else None
    if not isinstance(provider, str) or provider not in PROVIDERS or \
            llm_spec.get("evidence_version") not in PROVIDERS[provider] or \
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
    rule_set = _rule_set(spec)
    return ThresholdPlan(mode, rule_set, _targets(spec, provider, llm_spec.get("evidence_version")), provider)


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


def scoped(rows: Sequence[Any], scope: Mapping[str, str] | None,
           raw: Callable[[Any], Mapping[str, Any]]) -> Sequence[Any]:
    """묶음 범위에 드는 행. Detector 판정과 같은 함수(`detector.soft_rules.in_scope`)를 그대로 쓴다.

    값 있는 판정 키마다 행의 원래 값(trim 없음)과 정확 일치, 대조 값 없음(`graph_value_absent`)·문자열 아님은 제외.
    범위 없음(None)은 받은 행 그대로(복사 없음). `raw(row)`는 행에서 PROCESS 원래 값 사전을 꺼낸다.
    """
    if scope is None:
        return rows
    rule = {"scope": scope}
    return [row for row in rows if in_scope(rule, raw(row))]


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


def _closed(group: TargetGroup, field: SoftRuleField, basis: str) -> dict[str, Any]:
    """항목 공통 앞부분(상태 무관). `scope`·`candidateIds`는 10차 추가 — 옛 모양 대상이면 null·[]."""
    return {"field": field.field_id, "scope": group.scope, "softRuleIds": [rule["id"] for rule in group.rules],
            "candidateIds": list(group.candidates), "currentThreshold": group.current_threshold,
            "operator": None, "threshold": None, "displayThreshold": None, "unit": field.unit,
            "source": "data_p95", "basis": basis, "aiProposal": None}


def _item(validator: Any, plan: ThresholdPlan, group: TargetGroup, sample: Sequence[Mapping[str, Any]],
          excluded: Mapping[str, int]) -> dict[str, Any]:
    field = SOFT_RULE_FIELDS_BY_ID[group.field]
    closed = _closed(group, field, BASIS_V2)
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
        # 어댑터 불일치는 이 묶음이 아니라 작업 전체의 구성 문제다.
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
    """묶음(필드 × 범위)당 1건을 계산한다. `GenerationUnavailable`은 잡지 않아 부분 결과 없이 작업 전체가 실패한다.

    `progress(처리한 묶음 수, 전체 묶음 수)`는 시작(0)과 묶음마다 부른다. `source_tz`는 rule_v3 분할에만 쓴다.
    """
    if plan.provider == PROVIDER_V3:
        return _recommend_v3(plan, rows, snapshot_id, time_basis, source_tz, progress)
    root = os.environ["RESEARCH_BUNDLE_ROOT"]
    validator = policy(Path(root))
    groups = plan.groups
    # 범위마다 표본을 한 번 만든다(범위 없음 = 현행: 전체 행, 대상 필드 전부). 새 대상 모양은 parse_job이
    # rule_v2에서 거절하므로 이 경로의 범위는 늘 None이다(필터는 방어용).
    samples: dict[str | None, tuple[list[dict[str, Any]], dict[str, dict[str, int]]]] = {}
    for key in dict.fromkeys(group.scope_key for group in groups):
        members = [group for group in groups if group.scope_key == key]
        fields = [SOFT_RULE_FIELDS_BY_ID[group.field] for group in members]
        samples[key] = reference_sample(scoped(rows, members[0].scope, lambda row: row[1]), fields)
    items = []
    for group in groups:
        if progress:
            progress(len(items), len(groups))
        sample, excluded = samples[group.scope_key]
        items.append(_item(validator, plan, group, sample, excluded[group.field]))
    if progress:
        progress(len(items), len(groups))
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


def _v3_executors(runtime: Any) -> list[Any]:
    """묶음 실행기: 준비된 GPU 복제본(generation_replicas) + 주 프로세스 runtime(맨 뒤).

    묶음이 적으면 복제본부터 써서 주 GPU를 대화형 설명(explain)에 비워 둔다. 복제본이 없으면 [runtime] 하나다.
    """
    import generation_client
    pool = getattr(generation_client.local_model(), "rule_v3_replicas", None)
    replicas = list(pool.ready()) if pool is not None else []
    return [*replicas, runtime]


class _Executors:
    """비어 있는 실행기. 목록 앞쪽(복제본)부터 주고 맨 뒤(주 프로세스)는 `main_ok`일 때만 준다. 뺀 실행기는 돌아오지 않는다."""

    def __init__(self, executors: Sequence[Any]):
        self._executors = list(executors)
        self._main = len(self._executors) - 1
        self._free = set(range(len(self._executors)))
        self._live = set(self._free)
        self._changed = threading.Condition()

    def take(self, main_ok: bool) -> tuple[int, Any] | None:
        """받을 수 있는 실행기가 비면 그것을, 모두 바쁘면 기다리고, 남은 실행기가 없으면 None."""
        with self._changed:
            while True:
                usable = {index for index in self._live if main_ok or index != self._main}
                if not usable:
                    return None
                free = usable & self._free
                if free:
                    index = min(free)
                    self._free.discard(index)
                    return index, self._executors[index]
                self._changed.wait()

    def give(self, index: int) -> None:
        with self._changed:
            self._free.add(index)
            self._changed.notify_all()

    def drop(self, index: int) -> None:
        with self._changed:
            self._live.discard(index)
            self._changed.notify_all()


def _run_groups(executors: Sequence[Any], groups: Sequence[Any],
                run: Callable[[Any, Any], dict[str, Any]], progress: Progress | None) -> list[dict[str, Any]]:
    """묶음을 실행기에 나눠 동시에 돌린다. 결과는 `groups` 순서, `progress`는 이 함수를 부른 스레드에서만 부른다.

    묶음마다 비어 있는 실행기 중 목록 앞쪽(복제본)을 먼저 잡는다. `executors`의 맨 뒤는 주 프로세스 runtime이다.
    복제본이 묶음을 끝내지 못하고 꺼지면(`ReplicaUnavailable`: GPU 메모리 부족·자식 종료) 그 실행기를 이 작업에서 빼고
    같은 묶음을 남은 실행기에서 다시 돌린다(자식 종료는 주 프로세스 제외). 뺀 실행기는 다시 쓰지 않으므로 묶음마다
    실행기당 최대 1회, 작업 전체 재실행은 복제본 수 이하다. 받을 실행기가 없으면 그 예외(`generation_failed`)가 실패다.
    재실행은 같은 작업 스레드 안이라 진행률은 묶음당 한 번만 센다.
    그 밖의 예외는 기존대로: 시작 안 한 묶음은 취소하고 실행 중인 묶음이 끝나길 기다린 뒤, 묶음 순서상 가장 앞의 실패
    예외를 올린다(직렬 실행과 같은 종류의 실패).
    """
    free = _Executors(executors)
    # 실패를 본 작업 스레드가 바로 세운다. 주 스레드의 취소보다 먼저, 이미 꺼낸 다음 묶음도 돌리지 않게 한다.
    abort = threading.Event()

    def task(group: Any) -> dict[str, Any] | None:
        main_ok = True
        unavailable = ReplicaUnavailable("replica_none_left", rerun_on_main=False)  # 실행기가 처음부터 없을 때
        while True:
            if abort.is_set():
                return None
            taken = free.take(main_ok)
            if taken is None:  # 다시 돌릴 실행기가 없다. 마지막 복제본 장애가 이 묶음의 실패다
                abort.set()
                raise unavailable
            index, executor = taken
            dropped = False
            try:
                if abort.is_set():
                    return None
                return run(executor, group)
            except ReplicaUnavailable as exc:
                dropped = True
                free.drop(index)
                unavailable, main_ok = exc, main_ok and exc.rerun_on_main
            except BaseException:
                abort.set()
                raise
            finally:
                if not dropped:
                    free.give(index)

    total = len(groups)
    pool = ThreadPoolExecutor(max_workers=max(1, min(len(executors), total)), thread_name_prefix="rule-v3-group")
    try:
        futures = [pool.submit(task, group) for group in groups]
        pending, completed, failed = set(futures), 0, False
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                if future.cancelled():
                    continue
                if future.exception() is not None:
                    if not failed:
                        failed = True
                        for other in pending:
                            other.cancel()
                elif not failed and future.result() is not None:  # None = 실패 뒤 건너뛴 묶음
                    completed += 1
                    if progress:
                        progress(completed, total)
    except BaseException:
        abort.set()
        raise
    finally:
        # 진행률 기록 실패 등으로 여기서 나가도 남은 묶음을 돌리지 않는다. 실행 중인 묶음은 끝까지 기다린다.
        pool.shutdown(wait=True, cancel_futures=True)
    if failed:
        for future in futures:
            if not future.cancelled() and future.exception() is not None:
                raise future.exception()
    return [future.result() for future in futures]


def _korean_reason(count: int, rows: int, constant: Any) -> str | None:
    """LLM을 부르지 않는 데이터 사실(계약: 값 없음·표본 부족·분산 없음 → no_applicable). 코드가 쓰는 한국어 문장."""
    if count == 0:
        return f"참조 기간 {rows}행에서 계산할 수 있는 값이 없어 백분위 임계값을 만들 수 없습니다."
    if count < MINIMUM_N_V3:
        return f"참조 기간의 유효 값이 {count}개라 백분위 임계값에 필요한 {MINIMUM_N_V3}개에 못 미칩니다."
    if constant is not None:
        return f"참조 기간의 유효 값 {count}개가 모두 {exact_text(constant)}라 백분위 임계값으로 구분할 수 없습니다."
    return None


def _current_reason(scope: Mapping[str, str] | None) -> str:
    """현재 쪽 유효 값 0개(계약: 번들 호출 전 no_applicable). 번들 Rule 입력은 현재 쪽 하한이 없어(0개면 입력 거부 또는
    proposed 불가 `no_current_numeric_observations`) 0개만 코드가 끝낸다."""
    target = "이 범위의 " if scope is not None else ""
    return f"현재 기간에 {target}유효 값이 없어 비교할 수 없습니다."


def _dataset_id(snapshot_id: Any, group: TargetGroup, index: int) -> str:
    """번들 capture `dataset_id`. 범위 없는 묶음은 현행 이름 그대로(번들 `source_version` hash를 거쳐 Rule 입력 ID에
    들어가므로 옛 요청의 프롬프트가 바뀌지 않게), 범위 있는 묶음은 항목 순번 `_g<n>`(범위 값은 넣지 않음)."""
    name = f"snapshot_{snapshot_id}_{group.field}"
    return name if group.scope is None else f"{name}_g{index}"


def _item_v3(runtime: Any, group: TargetGroup, index: int, split: Mapping[str, Any],
             work: Path, snapshot_id: Any) -> dict[str, Any]:
    """묶음 하나. `split`은 작업당 1회 나눈 뒤 이 묶음 범위로 거른 참조·현재 행이다. 작업 폴더는 `g<index>`."""
    import rule_v3_capture as capture
    import rule_v3_runtime as v3
    field = SOFT_RULE_FIELDS_BY_ID[group.field]
    reference = [capture.field_value(field.field_id, row) for row in split["reference"]]
    valid = [value for value in reference if value is not None]
    p95 = capture.p95(valid)
    current_n = sum(capture.field_value(field.field_id, row) is not None for row in split["current"])
    closed = _closed(group, field, BASIS_V3)
    evidence = {"n": len(valid), "min": float(min(valid)) if valid else None,
                "max": float(max(valid)) if valid else None, "p95": float(p95) if p95 is not None else None,
                "quantileMethod": "linear_(n-1)*p", "exclusionRule": EXCLUSION_RULE,
                "measurementId": None, "baselineId": None, "currentN": current_n}
    passed = {"schema": True, "facts": True, "expectedTarget": True}
    reason = _korean_reason(len(valid), len(reference), valid[0] if valid and len(set(valid)) == 1 else None) or \
        (_current_reason(group.scope) if current_n == 0 else None)
    if reason:
        return {**closed, "status": "no_applicable", "rationale": reason, "reviews": passed,
                "evidence": evidence, "attempts": 0}
    package = runtime.package
    folder = work / f"g{index}"
    case = capture.write_case(folder / "case", field.field_id, split,
                              {"dataset_id": _dataset_id(snapshot_id, group, index),
                               "origin": "snapshot train members, source-time split", "field": field.field_id})
    try:
        run = runtime.run_field(case, folder / "rule")
    except TimeoutError as exc:
        from generation_client import GenerationUnavailable
        raise GenerationUnavailable("generation_timeout") from exc
    except package.io.InputFailure as exc:
        raise ValueError("recommendation_rule_input_rejected") from exc
    except package.io.ModelFailure as exc:
        raise ValueError("rule_v3_model_failure") from exc
    if any(record.get("adapterSha") != runtime.adapter_sha for record in run["attempts"]):
        # 어댑터 불일치는 이 묶음이 아니라 작업 전체의 구성 문제다.
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
    executors = _v3_executors(runtime)
    groups = list(enumerate(plan.groups))
    try:
        # 분할은 작업당 1회(묶음 범위와 무관). 보류 창과 겹치면 키트 회피 규칙으로 행을 빼고 진행한다(뺀 행 수는
        # heldWindowExclusion). 범위가 있는 묶음은 나눈 뒤 쪽마다 거른다.
        split, exclusion = capture.split_avoiding_held(rows, source_tz)
    except ValueError as exc:
        # 거래 묶음·시간 블록이 모자라 80/20으로 나눌 수 없거나, 보류 창을 빼면 남는 쪽이 없다.
        raise ValueError("recommendation_input_empty") from exc
    sides: dict[str | None, dict[str, Any]] = {}
    for _, group in groups:
        if group.scope_key not in sides:
            sides[group.scope_key] = split if group.scope is None else {
                **split, "reference": scoped(split["reference"], group.scope, lambda row: row),
                "current": scoped(split["current"], group.scope, lambda row: row)}
    # 행 값 CSV·번들 산출물은 컨테이너 임시 폴더에만 두고 작업이 끝나면 지운다.
    work = Path(tempfile.mkdtemp(prefix="rule-v3-"))

    def run(executor: Any, entry: tuple[int, TargetGroup]) -> dict[str, Any]:
        index, group = entry
        # 복제본 장애 뒤 다시 돌 때 앞 시도의 묶음 폴더(g<index>)를 지운다. 앞 실행기는 이미 끝났고(종료 확인 뒤 예외)
        # 폴더 경로는 프롬프트에 들어가지 않으므로 결과는 같다. 첫 시도에는 폴더가 없다.
        shutil.rmtree(work / f"g{index}", ignore_errors=True)
        return _item_v3(executor, group, index, sides[group.scope_key], work, snapshot_id)

    try:
        if len(executors) == 1:
            items = []
            for entry in groups:
                if progress:
                    progress(len(items), len(groups))
                items.append(run(runtime, entry))
            if progress:
                progress(len(items), len(groups))
        else:
            # GPU 복제본이 있으면 묶음을 나눠 동시에 처리한다. 항목 내용·순서는 직렬과 같다.
            if progress:
                progress(0, len(groups))
            items = _run_groups(executors, groups, run, progress)
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
