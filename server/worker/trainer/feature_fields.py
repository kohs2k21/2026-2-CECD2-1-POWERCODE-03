"""ML 입력 변수 선택 목록(`ml-feature-fields-v1`)과 세트 등록 hash 시험 벡터의 Python 원본.

- 화면이 고르는 필드 키 → 변수 정의(`{"name", "expression"}`) 변환 규칙을 고정한다. 변수 이름 = 필드 키.
- 파생 필드(`elapsed_ms`·`error_ratio`)의 AST는 소프트룰 등록 필드(`methods.registry.SOFT_RULE_FIELDS`)의 것을 그대로 쓴다.
- `python -m trainer.feature_fields --write`가 `server/contracts/ml-feature-fields.json`과
  `server/contracts/feature-set-hash-vectors.json`을 만든다(`--check`는 커밋본 대조). API는 두 JSON을 읽거나
  시험 벡터로 쓴다(trainer-service.md 입력 변수 세트 등록 규칙). 런타임은 이 모듈을 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path
from typing import Any, Sequence

from feature_fit import requires_fit
from feature_runtime import validate_definitions
from methods.registry import SOFT_RULE_FIELDS_BY_ID
from trainer.catalog import (AVAILABILITY_SPEC, SCHEMA_VERSION, TRANSFORM_VERSION, VALUE_POLICY,
                             canonical_json, expression_hash, feature_set_content_hash)

FIELDS_VERSION = "ml-feature-fields-v1"
VECTORS_VERSION = "feature-set-hash-vectors-v1"
DEFINITION_KIND = "operator_defined"
CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
FIELDS_PATH = CONTRACTS / "ml-feature-fields.json"
VECTORS_PATH = CONTRACTS / "feature-set-hash-vectors.json"
MAX_FIELDS = 32

_COUNT_FIELDS = ("total_count", "success_count", "error_count", "retry_count")
_DERIVED_FIELDS = ("elapsed_ms", "error_ratio")
_NOTES = {
    "elapsed_ms": "END_TIME이 없거나 시각을 읽지 못한 행은 값이 비어 학습에서 제외된다(결측 변수가 하나라도 있는 행은 학습하지 않음)",
    "error_ratio": "TOTAL_COUNT가 0이거나 없는 행은 값이 비어 학습에서 제외된다",
}


class FeatureFieldError(ValueError):
    """선택 목록 밖 필드·중복·개수 위반. 값은 API 오류 코드와 같다."""


def _expression(key: str) -> dict[str, Any]:
    if key in _COUNT_FIELDS:
        return {"op": "field", "field": f"P.{key}"}
    item = SOFT_RULE_FIELDS_BY_ID[key]
    return {"op": "derived", "ast": json.loads(json.dumps(item.source["ast"])), "outputType": item.value_type,
            "unit": item.unit, "provenance": {"source": FIELDS_VERSION, "field": key}}


def build_fields() -> list[dict[str, Any]]:
    entries = []
    for key in _COUNT_FIELDS + _DERIVED_FIELDS:
        registry = SOFT_RULE_FIELDS_BY_ID[key]
        entry = {"key": key, "displayName": registry.display_name, "unit": registry.unit,
                 "expression": _expression(key)}
        if key in _NOTES:
            entry["note"] = _NOTES[key]
        entries.append(entry)
    return entries


FIELDS = build_fields()
FIELD_ORDER = [item["key"] for item in FIELDS]
_EXPRESSIONS = {item["key"]: item["expression"] for item in FIELDS}


def definitions_for_fields(fields: Sequence[Any]) -> list[dict[str, Any]]:
    """요청 순서와 무관하게 목록 순서로 정렬한 정의. 빈 목록·32개 초과·중복·목록 밖 필드는 거절."""
    if not isinstance(fields, (list, tuple)) or not 1 <= len(fields) <= MAX_FIELDS:
        raise FeatureFieldError("invalid_feature_count")
    if any(not isinstance(item, str) or item not in _EXPRESSIONS for item in fields):
        raise FeatureFieldError("unknown_feature_field")
    if len(set(fields)) != len(fields):
        raise FeatureFieldError("duplicate_feature_field")
    chosen = set(fields)
    return validate_definitions([{"name": key, "expression": json.loads(json.dumps(_EXPRESSIONS[key]))}
                                 for key in FIELD_ORDER if key in chosen])


def build_fields_document() -> dict[str, Any]:
    return {
        "version": FIELDS_VERSION,
        "generatedBy": "python -m trainer.feature_fields --write",
        "order": "catalog",
        "maxFields": MAX_FIELDS,
        "definitionKind": DEFINITION_KIND,
        "featureName": "key",
        "fields": json.loads(json.dumps(FIELDS)),
    }


def _member(definition: dict[str, Any], position: int) -> dict[str, Any]:
    expression = definition["expression"]
    expression_json, digest = expression_hash(expression)
    return {"name": definition["name"], "canonicalExpressionJson": expression_json, "expressionHash": digest,
            "definition_key": definition["name"], "display_name": definition["name"],
            "definition_kind": DEFINITION_KIND, "output_type": expression.get("outputType", "decimal"),
            "output_unit": expression.get("unit"), "value_policy": dict(VALUE_POLICY),
            "availability_spec": dict(AVAILABILITY_SPEC), "requires_fit": requires_fit(expression),
            "model_input": True, "output_position": position, "output_name": definition["name"],
            "transform_settings": {}}


VECTOR_CASES = (
    ("single_count", ["error_count"]),
    ("all_six", list(_COUNT_FIELDS + _DERIVED_FIELDS)),
    ("subset_unordered", ["error_ratio", "total_count", "elapsed_ms"]),
    ("derived_only", ["error_ratio", "elapsed_ms"]),
)
INVALID_REQUESTS = (
    ([], "invalid_feature_count"),
    (["unknown_field"], "unknown_feature_field"),
    (["P.error_count"], "unknown_feature_field"),
    (["error_count", "error_count"], "duplicate_feature_field"),
)


def build_vector(case_id: str, fields: Sequence[str]) -> dict[str, Any]:
    definitions = definitions_for_fields(fields)
    return {
        "id": case_id,
        "fields": list(fields),
        "definitions": definitions,
        "canonicalDefinitionsJson": canonical_json(definitions),
        "contentHash": feature_set_content_hash(definitions, DEFINITION_KIND),
        "featureSet": {"schema_version": SCHEMA_VERSION, "transform_version": TRANSFORM_VERSION,
                       "output_schema": [item["name"] for item in definitions]},
        "members": [_member(item, position) for position, item in enumerate(definitions)],
    }


def build_vectors_document() -> dict[str, Any]:
    return {
        "version": VECTORS_VERSION,
        "fieldsVersion": FIELDS_VERSION,
        "generatedBy": "python -m trainer.feature_fields --write",
        "definitionKind": DEFINITION_KIND,
        "rules": {
            "canonicalJson": "json.dumps(value, sort_keys=True, separators=(',', ':')) — 키 사전순, 공백 없음, ASCII 밖은 \\uXXXX",
            "contentHash": "sha256_hex(utf8('operator_defined:' + canonicalDefinitionsJson))",
            "expressionHash": "sha256_hex(utf8(canonicalExpressionJson)) — 종류 접두 없음",
            "definitionsOrder": "ml-feature-fields.json fields[] 순서(요청 순서 무관)",
        },
        "cases": [build_vector(case_id, fields) for case_id, fields in VECTOR_CASES],
        "invalidRequests": [{"fields": list(fields), "error": error} for fields, error in INVALID_REQUESTS],
    }


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _outputs() -> list[tuple[Path, str]]:
    return [(FIELDS_PATH, render(build_fields_document())), (VECTORS_PATH, render(build_vectors_document()))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trainer.feature_fields",
                                     description="ml-feature-fields.json·feature-set-hash-vectors.json 생성(--write) / 대조(--check)")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        stale = [path for path, text in _outputs() if not path.exists() or path.read_text(encoding="utf-8") != text]
        for path in stale:
            print(f"differs from generator: {path}", file=sys.stderr)
        return 1 if stale else 0
    if args.write:
        for path, text in _outputs():
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            print(f"wrote {path}")
        return 0
    sys.stdout.write(render(build_fields_document()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
