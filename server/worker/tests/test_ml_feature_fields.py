"""ML 입력 변수 선택 목록과 세트 등록 hash 시험 벡터(trainer-service.md 입력 변수 세트 등록 규칙).

- 커밋된 두 JSON이 생성기와 같고, 목록의 모든 식이 실행 계약을 통과·계산된다.
- 벡터의 hash는 생성기와 별개로 hashlib로 다시 계산해 `trainer.catalog` 등록 함수와 같은지 본다(DB 없음).
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feature_fit import requires_fit
from feature_runtime import calculate_features, validate_definitions
from methods.registry import SOFT_RULE_FIELDS_BY_ID
from trainer import catalog, feature_fields

FIELDS_DOC = json.loads(feature_fields.FIELDS_PATH.read_text(encoding="utf-8"))
VECTORS_DOC = json.loads(feature_fields.VECTORS_PATH.read_text(encoding="utf-8"))
ROW = {"total_count": "10", "success_count": "9", "error_count": "1", "retry_count": "0",
       "start_time_raw": "2026-01-01T00:00:00", "end_time_raw": "2026-01-01T00:00:01.250"}
# 계약 고정값: 바뀌면 API 직접 등록과 이미 등록된 세트의 재사용이 깨진다.
ALL_SIX_CONTENT_HASH = "4a34bbfe81323afc89b6df344a393ebf9a115fdf1b1acfe2221244446fcc2a69"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class FieldCatalogTests(unittest.TestCase):
    def test_committed_json_matches_generator(self):
        self.assertEqual(FIELDS_DOC, feature_fields.build_fields_document(), "python -m trainer.feature_fields --write 로 재생성 필요")
        self.assertEqual(VECTORS_DOC, feature_fields.build_vectors_document(), "python -m trainer.feature_fields --write 로 재생성 필요")
        self.assertEqual(feature_fields.main(["--check"]), 0)

    def test_fixed_order_names_and_registry_sources(self):
        self.assertEqual(FIELDS_DOC["version"], "ml-feature-fields-v1")
        keys = [item["key"] for item in FIELDS_DOC["fields"]]
        self.assertEqual(keys, ["total_count", "success_count", "error_count", "retry_count", "elapsed_ms", "error_ratio"])
        for item in FIELDS_DOC["fields"]:
            registry = SOFT_RULE_FIELDS_BY_ID[item["key"]]
            self.assertEqual((item["displayName"], item["unit"]), (registry.display_name, registry.unit))
            expression = item["expression"]
            if expression["op"] == "field":
                self.assertEqual(expression, {"op": "field", "field": "P." + item["key"]})
            else:
                self.assertEqual(expression["ast"], registry.source["ast"])
                self.assertEqual(expression["provenance"], {"source": "ml-feature-fields-v1", "field": item["key"]})
                self.assertEqual((expression["outputType"], expression["unit"]), (registry.value_type, registry.unit))
            self.assertFalse(requires_fit(expression))
        self.assertIn("END_TIME", next(item for item in FIELDS_DOC["fields"] if item["key"] == "elapsed_ms")["note"])

    def test_every_field_validates_and_evaluates(self):
        definitions = [{"name": item["key"], "expression": item["expression"]} for item in FIELDS_DOC["fields"]]
        validate_definitions(definitions)
        values, missing = calculate_features(definitions, ROW)
        self.assertEqual(missing, [])
        self.assertEqual({key: Decimal(value) for key, value in values.items()},
                         {"total_count": 10, "success_count": 9, "error_count": 1, "retry_count": 0,
                          "elapsed_ms": 1250, "error_ratio": Decimal("0.1")})

    def test_missing_end_time_and_zero_total_are_missing_not_zero(self):
        definitions = feature_fields.definitions_for_fields(["elapsed_ms", "error_ratio"])
        values, missing = calculate_features(definitions, {**ROW, "end_time_raw": None, "total_count": "0"})
        self.assertEqual((values, missing), ({"elapsed_ms": None, "error_ratio": None}, ["elapsed_ms", "error_ratio"]))

    def test_request_rules(self):
        self.assertEqual([item["name"] for item in feature_fields.definitions_for_fields(["error_ratio", "total_count"])],
                         ["total_count", "error_ratio"])
        for case in VECTORS_DOC["invalidRequests"]:
            with self.subTest(case=case):
                with self.assertRaises(feature_fields.FeatureFieldError) as raised:
                    feature_fields.definitions_for_fields(case["fields"])
                self.assertEqual(str(raised.exception), case["error"])
        with self.assertRaises(feature_fields.FeatureFieldError):
            feature_fields.definitions_for_fields("error_count")


class HashVectorTests(unittest.TestCase):
    def test_vectors_reproduce_from_registration_functions(self):
        self.assertEqual({case["id"] for case in VECTORS_DOC["cases"]},
                         {"single_count", "all_six", "subset_unordered", "derived_only"})
        for case in VECTORS_DOC["cases"]:
            with self.subTest(case=case["id"]):
                definitions = case["definitions"]
                self.assertEqual(definitions, feature_fields.definitions_for_fields(case["fields"]))
                self.assertEqual([item["name"] for item in definitions],
                                 [key for key in feature_fields.FIELD_ORDER if key in case["fields"]])
                text = case["canonicalDefinitionsJson"]
                self.assertEqual(text, canonical(definitions))
                self.assertEqual(text, catalog.canonical_json(definitions))
                self.assertTrue(text.isascii())
                expected = hashlib.sha256(("operator_defined:" + text).encode("utf-8")).hexdigest()
                self.assertEqual(case["contentHash"], expected)
                self.assertEqual(catalog.feature_set_content_hash(definitions, "operator_defined"), expected)
                self.assertNotEqual(catalog.feature_set_content_hash(definitions, "model_generated"), expected)
                self.assertEqual(case["featureSet"], {"schema_version": catalog.SCHEMA_VERSION,
                                                      "transform_version": catalog.TRANSFORM_VERSION,
                                                      "output_schema": [item["name"] for item in definitions]})
                self.assertEqual(len(case["members"]), len(definitions))
                for position, (member, definition) in enumerate(zip(case["members"], definitions)):
                    member_json = canonical(definition["expression"])
                    self.assertEqual(member["canonicalExpressionJson"], member_json)
                    self.assertEqual(member["expressionHash"], hashlib.sha256(member_json.encode("utf-8")).hexdigest())
                    self.assertEqual(catalog.expression_hash(definition["expression"]), (member_json, member["expressionHash"]))
                    self.assertEqual({key: value for key, value in member.items()
                                      if key not in {"name", "canonicalExpressionJson", "expressionHash", "output_type", "output_unit"}},
                                     {"definition_key": definition["name"], "display_name": definition["name"],
                                      "definition_kind": "operator_defined", "value_policy": catalog.VALUE_POLICY,
                                      "availability_spec": catalog.AVAILABILITY_SPEC, "requires_fit": False,
                                      "model_input": True, "output_position": position, "output_name": definition["name"],
                                      "transform_settings": {}})
                    derived = definition["expression"]["op"] == "derived"
                    self.assertEqual((member["output_type"], member["output_unit"]),
                                     (definition["expression"]["outputType"], definition["expression"]["unit"]) if derived
                                     else ("decimal", None))

    def test_same_expression_has_same_member_hash_across_sets(self):
        hashes = {}
        for case in VECTORS_DOC["cases"]:
            for member in case["members"]:
                self.assertEqual(hashes.setdefault(member["name"], member["expressionHash"]), member["expressionHash"])
        self.assertEqual(next(case for case in VECTORS_DOC["cases"] if case["id"] == "all_six")["contentHash"],
                         ALL_SIX_CONTENT_HASH)


if __name__ == "__main__":
    unittest.main()
