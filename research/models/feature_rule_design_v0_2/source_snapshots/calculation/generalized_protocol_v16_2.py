"""Input policy 3.0.2: explicit interpretation coverage and actual error paths.

The frozen discovery prompt, AST/output contract 3.0.0, computational policy
2.2.0 and rule policy 3.0.1 are reused. This module changes only rule-stage
instructions and feedback projection; validation verdicts are never changed.
"""
from __future__ import annotations

import copy
import json
import re

import generalized_protocol_v16_1 as previous
from generalized_content_policy_v16 import assert_profile_admission

INPUT_POLICY_VERSION = "3.0.2"
OUTPUT_VERSION = previous.OUTPUT_VERSION
PLAN_VERSION = previous.PLAN_VERSION
RULE_POLICY_VERSION = "3.0.1"
VERSION = INPUT_POLICY_VERSION
MAX_PLANS = previous.MAX_PLANS
POLICY_SCOPE = "rule_interpretation_coverage_and_feedback_paths"

# Discovery and validators are intentionally unchanged. A new input policy is
# not permission to relax grading, change ASTs or rewrite immutable subjects.
discovery_schema = previous.discovery_schema
interpretation_schema = previous.interpretation_schema
rule_schema = previous.rule_schema
validate_discovery = previous.validate_discovery
validate_interpretation = previous.validate_interpretation
discovery_messages = previous.discovery_messages
public_inventory = previous.public_inventory
public_subject_registry = previous.public_subject_registry
sha256 = previous.sha256

INTERPRETATION_POLICY = """
규칙 입력 정책 3.0.2: interpretations는 선택 사항이 아니다. interpretation_coverage.required_variable_ids의 각 정확한 variable_id마다 해석 객체를 반드시 한 개씩 출력한다. 누락·중복·다른 ID를 허용하지 않는다.
정의 valid/invalid, 측정 measured/deferred/rejected 여부와 무관하게 확인 가능한 모든 변수 ID를 해석한다. invalid 변수는 실패와 필요한 근거를 설명하되 실행 규칙 입력으로 사용하지 않는다. valid 정의의 측정이 deferred이면 정의는 존재하고 관측은 보류라는 점을 설명한다.
측정이 deferred/rejected이면 quantitative_claims=[]로 두고 관측 수치를 발명하지 않는다. measured여도 실제 해당 변수 measurement_id의 정확한 summary metric/value만 인용한다. 해석은 HR/SR 규칙이나 review_requests의 대체가 아니며 변수 정의·단위·결측·SHA를 바꾸지 않는다.
검증기의 legacy reviews 경로는 출력 interpretations를 뜻한다. 수정 피드백은 실제 출력 field/path와 actual_path를 제공하며 원래 오류 code와 경로도 보존한다.
"""


def _coverage(public_registry):
    """Describe the existing required-review check, using actual subject IDs."""
    required = []
    duplicates = []
    unresolved = 0
    for subject in public_registry.get("subjects", []):
        reference = subject.get("reference") or {}
        if reference.get("kind") != "variable":
            continue
        variable_id = reference.get("variable_id")
        if not isinstance(variable_id, str) or not variable_id:
            unresolved += 1
        elif variable_id not in required:
            required.append(variable_id)
        elif variable_id not in duplicates:
            duplicates.append(variable_id)
    return {
        "output_field": "interpretations",
        "required_variable_ids": required,
        "cardinality": "exactly_one_per_required_variable_id",
        "unknown_or_duplicate_interpretation_ids_allowed": False,
        "definition_states_covered": ["valid", "invalid"],
        "measurement_states_covered": ["measured", "deferred", "rejected"],
        "unmeasured_quantitative_claims": [],
        "quantitative_claim_source": "same variable's measured measurement_id and exact summary metric/value",
        "duplicate_source_variable_ids": duplicates,
        "unresolvable_source_variable_id_count": unresolved,
        "invent_variable_ids": False,
    }


def interpretation_messages(inventory, proposal, validation, measurements, registry=None):
    assert_profile_admission(inventory)
    messages = previous.interpretation_messages(inventory, proposal, validation, measurements, registry=registry)
    messages = copy.deepcopy(messages)
    content = json.loads(messages[1]["content"])
    content["interpretation_coverage"] = _coverage(content["subject_registry"])
    content["input_policy_version"] = INPUT_POLICY_VERSION
    content["input_policy_scope"] = POLICY_SCOPE
    content["output_contract"]["input_policy_version"] = INPUT_POLICY_VERSION
    messages[0]["content"] += INTERPRETATION_POLICY
    messages[1]["content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return messages


def _actual_interpretation_path(value):
    if not isinstance(value, str):
        return value
    # Replace the first complete path token only: /reviews/0, reviews.0,
    # reviews[0], and reviews. Nested metadata named reviews is unrelated.
    return re.sub(r"^(/?)reviews(?=$|[./\[])", r"\1interpretations", value)


def _project_error_paths(value):
    """Map legacy error locations without modifying persisted validations."""
    if isinstance(value, list):
        return [_project_error_paths(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    projected = {key: _project_error_paths(item) for key, item in value.items()}
    if "code" not in projected:
        return projected
    changed = False
    for key in ("field", "path"):
        original = projected.get(key)
        actual = _actual_interpretation_path(original)
        if actual != original:
            projected["original_" + key] = original
            projected[key] = actual
            changed = True
    if changed:
        projected["actual_path"] = projected.get("path") or projected.get("field")
    if projected.get("code") == "missing_review":
        # Keep the legacy code and plan_id for exact provenance. The model
        # needs the actual variable ID and output destination, not a new ID.
        variable_id = projected.get("plan_id")
        if isinstance(variable_id, str) and variable_id:
            projected["required_variable_id"] = variable_id
        if "message" in projected:
            projected["original_message"] = projected["message"]
        projected["message"] = ("Output interpretations requires exactly one interpretation for this variable_id; "
                                "if measurement is deferred/rejected, explain the state and use quantitative_claims=[].")
    return projected


def feedback_messages(stage, inventory, prior_proposal, validation, measurements=None, registry=None, messages=None):
    assert_profile_admission(inventory)
    if stage != "rules":
        return previous.feedback_messages(stage, inventory, prior_proposal, validation,
            measurements=measurements, registry=registry, messages=messages)
    if messages is None:
        messages = interpretation_messages(inventory, {}, {"variables": []}, measurements or [], registry=registry)
    result = previous.feedback_messages(stage, inventory, prior_proposal, validation,
        measurements=measurements, registry=registry, messages=messages)
    content = json.loads(result[-1]["content"])
    content["validation_errors"] = _project_error_paths(content["validation_errors"])
    initial = json.loads(result[1]["content"])
    content["interpretation_coverage"] = copy.deepcopy(initial.get("interpretation_coverage") or
        _coverage(initial["subject_registry"]))
    content["input_policy_version"] = INPUT_POLICY_VERSION
    content["input_policy_scope"] = POLICY_SCOPE
    result[-1]["content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return result
