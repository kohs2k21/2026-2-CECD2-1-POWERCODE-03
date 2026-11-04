"""방법 레지스트리·카탈로그 생성(Issue #111): schema 통과, 계약 v2 값 일치, 파라미터 검증, 필드 값 계산."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import derived_expression
import model_adapters
from methods import registry

REPO = Path(__file__).resolve().parents[3]
SCHEMA = json.loads(registry.SCHEMA_PATH.read_text(encoding="utf-8"))

# method-catalog.md 소프트룰 등록 필드 표(v2): fieldId → (valueType, unit, displayPrecision, mode, sourceMissingPolicy)
CONTRACT_FIELDS = {
    "total_count": ("integer", "count", 0, None, None),
    "success_count": ("integer", "count", 0, None, None),
    "error_count": ("integer", "count", 0, None, None),
    "retry_count": ("integer", "count", 0, None, None),
    "elapsed_ms": ("number", "ms", 0, "fixed", {"END_TIME": "not_applicable", "START_TIME": "unavailable"}),
    "error_ratio": ("number", "ratio", 4, "significant", None),
}


class CatalogGenerationTests(unittest.TestCase):
    def test_generated_catalog_passes_schema_and_matches_committed_json(self):
        catalog = registry.build_catalog()
        jsonschema.Draft202012Validator(SCHEMA).validate(catalog)
        committed_text = registry.CATALOG_PATH.read_text(encoding="utf-8")
        self.assertEqual(json.loads(committed_text), catalog, "python -m methods.registry --write 로 재생성 필요")
        self.assertEqual(committed_text, registry.render_catalog(catalog))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog.json"
            self.assertEqual(registry.main(["--write", "--output", str(target)]), 0)
            self.assertEqual(target.read_bytes(), committed_text.encode("utf-8"))
            self.assertEqual(registry.main(["--check", "--output", str(target)]), 0)
            target.write_text("{}", encoding="utf-8")
            self.assertEqual(registry.main(["--check", "--output", str(target)]), 1)

    def test_top_level_blocks_match_contract(self):
        catalog = registry.build_catalog()
        self.assertEqual(catalog["catalogVersion"], "method-catalog-v2")
        self.assertEqual(catalog["categoryDisplayNames"], {"hard": "이상 규칙", "soft": "이상 징후 규칙", "ml": "머신러닝 모델"})
        self.assertEqual(catalog["hardRules"], {"definedBy": "detection-decision.md", "displayName": "이상 규칙",
                                                "contractVersion": "detection-decision-v1",
                                                "idPattern": "^hard:[a-z][a-z0-9_-]{1,63}$"})
        self.assertEqual(catalog["softRule"]["displayName"], catalog["categoryDisplayNames"]["soft"])
        self.assertEqual(catalog["softRule"]["maxRules"], 32)
        self.assertEqual(catalog["softRule"]["operators"], ["gte", "gt", "lte", "lt"])
        # 하드룰 항목은 카탈로그 JSON에 없고(Detector 소유) 레지스트리 참조 상수만 있다.
        self.assertNotIn("items", catalog["hardRules"])
        self.assertEqual(registry.HARD_RULE_IDS, (("hard:process-status", "process-status-hr-v1"),
                                                  ("hard:negative-duration", "negative-duration-v1")))
        pattern = catalog["hardRules"]["idPattern"]
        import re
        self.assertTrue(all(re.fullmatch(pattern, rule_id) for rule_id, _ in registry.HARD_RULE_IDS))

    def test_soft_rule_fields_match_contract_table(self):
        catalog = registry.build_catalog()
        fields = {item["fieldId"]: item for item in catalog["softRuleFields"]}
        self.assertEqual(list(fields), list(CONTRACT_FIELDS))
        for field_id, (value_type, unit, precision, mode, policy) in CONTRACT_FIELDS.items():
            item = fields[field_id]
            self.assertEqual(item["valueType"], value_type, field_id)
            self.assertEqual(item["unit"], unit, field_id)
            self.assertEqual(item["displayPrecision"], precision, field_id)
            self.assertEqual(item.get("displayPrecisionMode"), mode, field_id)
            self.assertEqual(item.get("sourceMissingPolicy"), policy, field_id)
            self.assertEqual(item["operators"], ["gte", "gt", "lte", "lt"])
            self.assertEqual(item["status"], "supported")
            self.assertLessEqual(len(field_id), 54)
            # 파생식은 derived_expression 검증을 통과하고 계산 단위가 필드 unit과 같아야 한다.
            ast = registry.SOFT_RULE_FIELDS_BY_ID[field_id].ast
            self.assertEqual(derived_expression.validate(ast), "number")
            self.assertEqual(derived_expression.unit(ast), unit)
        self.assertEqual(fields["total_count"]["source"], {"kind": "column", "column": "TOTAL_COUNT", "ruleDslField": "P.total_count"})
        self.assertEqual(fields["elapsed_ms"]["source"]["ast"]["args"][0]["column"], "END_TIME")
        self.assertEqual(fields["error_ratio"]["source"]["ast"]["op"], "divide")

    def test_ml_methods_match_contract_and_executable_items_have_server_adapters(self):
        catalog = registry.build_catalog()
        methods = {item["methodId"]: item for item in catalog["methods"]}
        self.assertEqual(list(methods), ["ml:iforest", "ml:minibatch_kmeans", "ml:eif"])
        iforest = methods["ml:iforest"]
        self.assertEqual(iforest["methodVersion"], "process-iforest-v1")
        self.assertEqual(iforest["defaults"], {"n_estimators": 128, "max_samples": 512, "contamination": "auto",
                                               "random_state": 42, "thresholdPolicy": "train-internal-p99-v1"})
        self.assertEqual(iforest["executable"], {"process": True, "window": True})
        self.assertEqual(iforest["inputRequirements"]["minTrainRows"], 600)
        self.assertEqual(iforest["runtime"]["artifactFormat"], model_adapters.FORMAT_VERSION)
        self.assertEqual(methods["ml:minibatch_kmeans"]["executableReason"], {"process": "server_adapter_missing"})
        self.assertEqual(methods["ml:eif"]["executable"], {"process": False, "window": False})
        for item in catalog["methods"]:
            # defaults는 자신의 parameterSchema(jsonschema·내장 검증기 모두)를 통과한다.
            jsonschema.Draft202012Validator(item["parameterSchema"]).validate(item["defaults"])
            registry.validate_parameters(item["parameterSchema"], item["defaults"])
            for path in (item["uiTarget"]["path"], item["uiTarget"].get("ref"), item["sourceRef"]["path"]):
                if path is not None:
                    self.assertTrue((REPO / path).exists(), path)
            method = registry.ML_METHODS_BY_ID[item["methodId"]]
            self.assertEqual(method.executable_process, item["executable"]["process"])
            if item["executable"]["process"]:
                self.assertEqual(item["uiTarget"]["scope"], "server")
                self.assertIs(method.adapter._resolve(method.adapter.fit_name), model_adapters.train_iforest)
                self.assertIs(method.adapter._resolve(method.adapter.prepare_name), model_adapters.prepare_iforest)
                self.assertIs(method.adapter._resolve(method.adapter.score_name), model_adapters.score_iforest)
            else:
                self.assertIsNone(method.adapter)
        for field in catalog["softRuleFields"]:
            self.assertTrue((REPO / field["sourceRef"]["path"]).exists())

    def test_server_registry_does_not_import_research(self):
        source = (REPO / "server/worker/methods/registry.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"^\s*(from|import)\s+research", "server/는 research/ 런타임 import 금지")


class ParameterValidationTests(unittest.TestCase):
    def test_defaults_fill_missing_keys_and_requests_override(self):
        resolved = registry.iforest_parameters(None)
        self.assertEqual(resolved, registry.IFOREST_DEFAULTS)
        resolved = registry.iforest_parameters({"n_estimators": 64})
        self.assertEqual(resolved["n_estimators"], 64)
        self.assertEqual(resolved["max_samples"], 512)
        self.assertEqual(registry.iforest_parameters({}), registry.IFOREST_DEFAULTS)

    def test_builtin_validator_agrees_with_jsonschema_on_rejections(self):
        schema = registry.IFOREST_PARAMETER_SCHEMA
        cases = {
            "unknown_parameter:bootstrap": {"bootstrap": True},
            "parameter_type_mismatch:n_estimators": {"n_estimators": True},
            "parameter_type_mismatch:random_state": {"random_state": "42"},
            "parameter_below_minimum:n_estimators": {"n_estimators": 15},
            "parameter_above_maximum:max_samples": {"max_samples": 4097},
            "parameter_below_minimum:random_state": {"random_state": -1},
            "parameter_const_mismatch:contamination": {"contamination": 0.1},
            "parameter_const_mismatch:thresholdPolicy": {"thresholdPolicy": "p95"},
        }
        validator = jsonschema.Draft202012Validator(schema)
        for code, overrides in cases.items():
            merged = {**registry.IFOREST_DEFAULTS, **overrides}
            self.assertFalse(validator.is_valid(merged), code)
            with self.assertRaisesRegex(registry.MethodParameterError, code):
                registry.validate_parameters(schema, merged)
        with self.assertRaisesRegex(registry.MethodParameterError, "missing_parameter:random_state"):
            registry.validate_parameters(schema, {k: v for k, v in registry.IFOREST_DEFAULTS.items() if k != "random_state"})
        with self.assertRaisesRegex(registry.MethodParameterError, "parameters_not_object"):
            registry.iforest_parameters([1, 2])

    def test_resolve_training_method_error_codes_follow_contract(self):
        method, parameters = registry.resolve_training_method({})
        self.assertEqual((method.method_id, method.model_key, parameters), ("ml:iforest", "iforest", registry.IFOREST_DEFAULTS))
        method, parameters = registry.resolve_training_method({"modelKey": "iforest", "parameters": {"random_state": 7}})
        self.assertEqual(parameters["random_state"], 7)
        with self.assertRaisesRegex(ValueError, "^unsupported_model$"):
            registry.resolve_training_method({"modelKey": "nope"})
        with self.assertRaisesRegex(ValueError, "^unsupported_model$"):
            registry.resolve_training_method({"modelKey": 42})
        with self.assertRaisesRegex(ValueError, "^method_not_executable:ml:minibatch_kmeans$"):
            registry.resolve_training_method({"modelKey": "minibatch_kmeans"})
        with self.assertRaisesRegex(ValueError, "^method_not_executable:ml:eif$"):
            registry.resolve_training_method({"modelKey": "eif", "parameters": {"ntrees": 10}})
        with self.assertRaisesRegex(ValueError, "^method_parameters_invalid:ml:iforest$"):
            registry.resolve_training_method({"parameters": {"n_estimators": 8}})
        with self.assertRaisesRegex(ValueError, "^method_parameters_invalid:ml:iforest$"):
            registry.resolve_training_method({"parameters": "n_estimators=8"})

    def test_runner_keeps_contract_error_codes_and_hides_arbitrary_text(self):
        from trainer.runner import _terminal_error_code
        for code in ("unsupported_model", "method_not_executable:ml:eif", "method_parameters_invalid:ml:iforest",
                     "method_not_executable:ml:minibatch_kmeans"):
            self.assertEqual(_terminal_error_code(ValueError(code)), code)
        for text in ("could not convert string to float: 'x'", "Expected 2D array, got 1D", "Bad:Code", "a:", ":ml", ""):
            self.assertEqual(_terminal_error_code(ValueError(text)), "invalid_job_input")


class SoftRuleFieldEvaluationTests(unittest.TestCase):
    ROW = {"success_count": "9", "error_count": "1", "total_count": "10", "retry_count": 0,
           "start_time_raw": "2026-10-01T00:00:00+09:00", "end_time_raw": "2026-10-01T00:00:01.250+09:00"}

    def test_column_and_derived_values(self):
        fields = registry.SOFT_RULE_FIELDS_BY_ID
        self.assertEqual(fields["total_count"].evaluate(self.ROW), registry.FieldValue("available", Decimal("10")))
        self.assertEqual(fields["error_ratio"].evaluate(self.ROW).value, Decimal("0.1"))
        self.assertEqual(fields["elapsed_ms"].evaluate(self.ROW).value, Decimal("1250"))
        self.assertEqual(fields["elapsed_ms"].source_columns, ("END_TIME", "START_TIME"))

    def test_source_missing_policy_decides_not_applicable_vs_unavailable(self):
        fields = registry.SOFT_RULE_FIELDS_BY_ID
        no_end = fields["elapsed_ms"].evaluate({**self.ROW, "end_time_raw": None})
        self.assertEqual((no_end.status, no_end.value, no_end.reason), ("not_applicable", None, "source_missing:END_TIME"))
        no_start = fields["elapsed_ms"].evaluate({**self.ROW, "start_time_raw": None})
        self.assertEqual((no_start.status, no_start.reason), ("unavailable", "source_missing:START_TIME"))
        both = fields["elapsed_ms"].evaluate({**self.ROW, "start_time_raw": None, "end_time_raw": None})
        self.assertEqual(both.status, "not_applicable")
        bad_time = fields["elapsed_ms"].evaluate({**self.ROW, "start_time_raw": "yesterday"})
        self.assertEqual((bad_time.status, bad_time.reason), ("unavailable", "value_not_computable"))
        zero = fields["error_ratio"].evaluate({**self.ROW, "total_count": "0"})
        self.assertEqual((zero.status, zero.reason), ("unavailable", "value_not_computable"))
        absent = fields["total_count"].evaluate({})
        self.assertEqual((absent.status, absent.reason), ("unavailable", "source_missing:TOTAL_COUNT"))


if __name__ == "__main__":
    unittest.main()
