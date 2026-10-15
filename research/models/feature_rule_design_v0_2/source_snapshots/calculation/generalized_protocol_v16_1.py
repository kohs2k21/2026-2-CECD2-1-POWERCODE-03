"""Input projection policy 3.0.1 over the frozen v16 output/validation contract.

This module corrects two model-input defects without rewriting the v16 run:
verified variable definitions now cross the stage boundary, and the semantic
mapping contract version is distinguished from a plan's reviewed mapping ID.
AST/output contract 3.0.0 and computational validation 2.2.0 are unchanged.
"""
from __future__ import annotations

import copy
import json

import generalized_protocol_v16 as frozen
from generalized_content_policy_v16 import assert_profile_admission
from generalized_measurement_v16 import validate_plan
from generalized_rules_v16 import (build_subject_registry, public_subject_registry as frozen_public_registry,
                                  _resolve_subject)

INPUT_POLICY_VERSION = "3.0.1"
OUTPUT_VERSION = frozen.VERSION
PLAN_VERSION = frozen.PLAN_VERSION
VERSION = INPUT_POLICY_VERSION
MAX_PLANS = frozen.MAX_PLANS

# These aliases retain the exact frozen generation and validation policy.
discovery_schema = frozen.discovery_schema
interpretation_schema = frozen.interpretation_schema
rule_schema = frozen.rule_schema
validate_discovery = frozen.validate_discovery
validate_interpretation = frozen.validate_interpretation
sha256 = frozen.sha256

VERSION_GUIDE = """
입력 정책은 projection3.0.1이며 출력 JSON version은 3.0.0 그대로다.
mapping.version=2.2.0은 의미 매핑 객체의 계약 버전이다. 변수 plan_version=2.2.0과 변수 mapping_version는 다른 필드다.
모든 변수의 schema_version/source_version/mapping_version은 exact_plan_version_bindings를 정확히 따른다. mapping_version=null이면 JSON null을 쓴다. 문자열 'null' 또는 '2.2.0'으로 바꾸지 않는다.
새 의미 매핑 제안은 검증된 매핑 버전이 아니다. 새 mapping.bindings를 제안했다는 이유로 변수 mapping_version를 바꾸지 않는다.
규칙 단계의 definition에서 계산식·목적·원천 컬럼은 코드가 검증한 원래 정의다. description은 저자의 미확인 제안이며 단위·계약 근거가 아니다. 원래 definition_sha256와 입력 문맥을 보존하며 계산식을 수정하지 않는다.
definition_status=invalid인 변수의 ID/proposal_sha256는 실패 기록이다. 그 reference는 실행 규칙의 입력으로 채택할 수 없다.
잘못된 변수에 필요한 검토는 확인된 valid raw_column reference를 선택해 review_requests로 남길 수 있다. raw 컬럼의 확인된 선언 HR 경로는 별도로 유지한다.
통계가 없으면 관측 숫자를 만들지 않는다. definition valid와 measurement deferred는 서로 다른 상태다.
data_profile 근거의 value_location은 같은 원시 열의 statistics를 가리킨다. 그 통계값은 완전히 동일하여 공개 입력에 한 번만 제공하며 근거 ID·원천은 유지한다.
"""


def _version_bindings(inventory):
    return {"schema_version": inventory["schema_version"], "source_version": inventory["source_version"],
            "mapping_version": inventory.get("mapping_version")}


def _contract(stage, inventory):
    guide = frozen._output_contract(stage)
    if stage == "variables":
        guide.pop("mapping_version", None)
        guide["mapping_contract_version"] = PLAN_VERSION
        guide["expected_plan_mapping_version"] = inventory.get("mapping_version")
    guide["output_version"] = OUTPUT_VERSION
    guide["input_policy_version"] = INPUT_POLICY_VERSION
    return guide


def _replace_content(messages, content):
    messages = copy.deepcopy(messages)
    messages[0]["content"] += VERSION_GUIDE
    messages[1]["content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return messages


def public_inventory(inventory):
    """Remove exact duplicated column statistics only, keeping all proof IDs.

The original inventory and code evidence are never changed. Declarations,
units, business documents and non-identical facts retain their entire value.
"""
    assert_profile_admission(inventory)
    public = frozen.public_inventory(inventory)
    columns = {(table["id"], column["name"]): (column, f"/tables/{ti}/columns/{ci}/statistics")
               for ti, table in enumerate(public.get("tables", []))
               for ci, column in enumerate(table.get("columns", []))}
    count = 0
    for fact in public.get("evidence", []):
        target = fact.get("target", {})
        matched = columns.get((target.get("table"), target.get("column")))
        if (fact.get("kind") != "data_profile" or matched is None or
                not isinstance(matched[0].get("statistics"), dict) or "value" not in fact or
                fact["value"] != matched[0]["statistics"]):
            continue
        fact.pop("value")
        fact["value_location"] = {"inventory_json_pointer": matched[1],
                                  "table": target["table"], "column": target["column"]}
        count += 1
    public["public_projection"] = {"version": INPUT_POLICY_VERSION,
        "scope": "public_data_profile_statistics_dedup", "deduplicated_value_count": count,
        "full_source_inventory_unchanged": True}
    return public


def discovery_messages(inventory):
    assert_profile_admission(inventory)
    messages = frozen.discovery_messages(inventory)
    content = json.loads(messages[1]["content"])
    content["inventory"] = public_inventory(inventory)
    content["output_contract"] = _contract("variables", inventory)
    content["input_policy_version"] = INPUT_POLICY_VERSION
    content["exact_plan_version_bindings"] = _version_bindings(inventory)
    return _replace_content(messages, content)


def public_subject_registry(registry, inventory):
    """Expose verified definitions once; provenance/context are never rewritten."""
    assert_profile_admission(inventory)
    public = frozen_public_registry(registry)
    public["input_policy_version"] = INPUT_POLICY_VERSION
    for original, visible in zip(registry["subjects"], public["subjects"]):
        visible["rule_subject_admitted"] = original.get("definition_status") == "valid"
        if not visible["rule_subject_admitted"]:
            visible["definition"] = None
            visible["resolution_guidance"] = "Invalid proposal: preserve ID/proposal SHA; select a valid raw_column reference for review rather than using this input"
            continue
        resolved, errors = _resolve_subject(original["reference"], inventory, registry)
        if errors or resolved is None:
            raise ValueError("Verified public definition failed its immutable subject binding")
        if original["reference"]["kind"] != "variable":
            continue
        plan = original["plan"]
        checked = validate_plan(plan, inventory)
        if checked.get("definition_sha256") != original["definition_sha256"] or not checked["passed"]:
            raise ValueError("Variable definition changed after validation")
        visible["definition"] = {"expression": copy.deepcopy(plan["expression"]),
                                 "purpose": plan["purpose"], "description": plan.get("description"),
                                 "source_columns": copy.deepcopy(checked["identity"]["source_columns"])}
        visible["definition_description_status"] = "unverified_author_proposal_text"
    return public


def interpretation_messages(inventory, proposal, validation, measurements, registry=None):
    assert_profile_admission(inventory)
    registry = registry or build_subject_registry(inventory, proposal.get("variables", []), validation, measurements)
    messages = frozen.interpretation_messages(inventory, proposal, validation, measurements, registry=registry)
    content = json.loads(messages[1]["content"])
    content["inventory"] = public_inventory(inventory)
    content["subject_registry"] = public_subject_registry(registry, inventory)
    content["output_contract"] = _contract("rules", inventory)
    content["input_policy_version"] = INPUT_POLICY_VERSION
    content["exact_plan_version_bindings"] = _version_bindings(inventory)
    return _replace_content(messages, content)


def feedback_messages(stage, inventory, prior_proposal, validation, measurements=None, registry=None, messages=None):
    assert_profile_admission(inventory)
    if messages is None:
        messages = discovery_messages(inventory) if stage == "variables" else interpretation_messages(
            inventory, {}, {"variables": []}, measurements or [], registry=registry)
    messages = frozen.feedback_messages(stage, inventory, prior_proposal, validation,
        measurements=measurements, registry=registry, messages=messages)
    content = json.loads(messages[-1]["content"])
    # The initial message already carries the field guide. The same full
    # immutable decoder contract remains active for this repair invocation.
    content["output_contract"] = {"output_version": OUTPUT_VERSION,
        "same_contract_as_initial": True, "decoder_enforces_full_schema": True}
    content["input_policy_version"] = INPUT_POLICY_VERSION
    content["exact_plan_version_bindings"] = _version_bindings(inventory)
    content["version_error_guidance"] = {"mapping.version": PLAN_VERSION,
        "variables[*].plan_version": PLAN_VERSION, "variables[*].mapping_version": inventory.get("mapping_version"),
        "variables[*].schema_version": "exact_plan_version_bindings.schema_version",
        "variables[*].source_version": "exact_plan_version_bindings.source_version",
        "output.version": OUTPUT_VERSION}
    if stage == "rules" and registry is not None:
        # The rule feedback registry already supplies each immutable input's
        # confirmed unit and missing policy. Repeating the physical column
        # unit list consumes repair context without supplying a new fact.
        content.pop("confirmed_source_columns", None)
        content["confirmed_input_context_location"] = "immutable_subject_registry; physical missing tokens remain in the unchanged initial inventory"
    messages[-1]["content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return messages
