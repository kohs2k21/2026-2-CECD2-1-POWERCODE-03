"""방법 레지스트리·카탈로그 생성(Issue #111, 11/28 두 틀): schema 통과, 계약 값 일치, 틀·결과 코드·틀별 추천 규칙,
파라미터 검증, 필드 값 계산."""
from __future__ import annotations

import copy
import json
import re
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

# method-catalog.md 소프트룰 등록 필드 표(v2부터 그대로, v3): fieldId → (valueType, unit, displayPrecision, mode, sourceMissingPolicy)
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
        self.assertEqual(catalog["catalogVersion"], "method-catalog-v3")
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


# 한 methodId의 frame은 바꾸지 않는다(method-catalog.md 틀 절, 바꾸려면 새 methodId). 새 methodId는 여기에 더하고,
# 있는 항목은 지우거나 값을 바꾸지 않는다(항목은 지우지 않고 deprecated로 둔다).
PINNED_FRAMES = {"ml:iforest": "row", "ml:minibatch_kmeans": "row", "ml:eif": "trend"}
EVIDENCE = {"reportRef": "server/docs/example-report.md", "evaluationSet": "validation-set", "metric": "recall",
            "decidedAt": "2026-12-01T00:00:00+09:00"}


def contract_result_code(method_id: str) -> str:
    """계약 문장 그대로 따로 적은 규칙: `ml:iforest`만 `IF_ANOMALY`, 나머지는 `ML_<key 대문자, '-'→'_'>`."""
    if method_id == "ml:iforest":
        return "IF_ANOMALY"
    return "ML_" + method_id[len("ml:"):].replace("-", "_").upper()


class FrameAndResultCodeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = registry.build_catalog()

    def rejected(self, catalog, code):
        with self.assertRaises(registry.CatalogRuleError) as caught:
            registry.validate_catalog(catalog)
        self.assertEqual(str(caught.exception), code)

    def with_method(self, method_id, unit, **changes):
        """실행할 수 없는 연구 항목(`ml:minibatch_kmeans`)을 본으로 방법 1개를 더한 카탈로그."""
        catalog = copy.deepcopy(self.catalog)
        item = copy.deepcopy(catalog["methods"][1])
        item.update(methodId=method_id, frame=registry.FRAME_BY_UNIT[unit], resultCode=contract_result_code(method_id))
        item["inputRequirements"] = {**item["inputRequirements"], "unit": unit}
        item.update(changes)
        catalog["methods"].append(item)
        return catalog

    def recommend(self, frame, method_id, parameters=None, evidence=EVIDENCE):
        catalog = copy.deepcopy(self.catalog)
        catalog["frames"][frame].update(recommendedMethodId=method_id, evidence=evidence,
                                        recommendedParameters=dict(registry.IFOREST_DEFAULTS) if parameters is None else parameters)
        return catalog

    def test_frame_is_one_to_one_with_input_unit(self):
        units = SCHEMA["$defs"]["method"]["properties"]["inputRequirements"]["properties"]["unit"]["enum"]
        self.assertEqual(registry.FRAME_BY_UNIT, {"process_row": "row", "aggregated_window": "trend"})
        self.assertEqual(set(registry.FRAME_BY_UNIT), set(units), "schema의 unit 값마다 틀이 정확히 하나")
        self.assertEqual(sorted(registry.FRAME_BY_UNIT.values()), sorted(registry.FRAMES), "틀마다 unit이 정확히 하나")
        for item in self.catalog["methods"]:
            self.assertEqual(item["frame"], registry.FRAME_BY_UNIT[item["inputRequirements"]["unit"]], item["methodId"])

    def test_method_frames_never_change(self):
        frames = {item["methodId"]: item["frame"] for item in self.catalog["methods"]}
        self.assertEqual({key: frames.get(key) for key in PINNED_FRAMES}, PINNED_FRAMES, "frame을 바꾸려면 새 methodId")
        self.assertEqual(set(frames) - set(PINNED_FRAMES), set(), "새 methodId는 PINNED_FRAMES에 더한다")
        committed = json.loads(registry.CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(registry.frame_changes(committed, self.catalog), [])

    def test_write_refuses_a_frame_change_against_the_previous_catalog(self):
        v2 = copy.deepcopy(self.catalog)
        for item in v2["methods"]:
            del item["frame"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog.json"
            previous = copy.deepcopy(self.catalog)
            previous["methods"][0]["frame"] = "trend"  # 직전 판에서 ml:iforest가 trend였다면
            text = json.dumps(previous)
            target.write_text(text, encoding="utf-8")
            self.assertEqual(registry.main(["--write", "--output", str(target)]), 1)
            self.assertEqual(target.read_text(encoding="utf-8"), text, "거절하면 파일을 바꾸지 않는다")
            # frame 키가 없는 이전 판(v2)은 같은 1:1 규칙으로 unit에서 읽으므로 다시 쓸 수 있다.
            target.write_text(json.dumps(v2), encoding="utf-8")
            self.assertEqual(registry.main(["--write", "--output", str(target)]), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), registry.render_catalog())
        self.assertEqual(registry.frame_changes(None, self.catalog), [])
        v2["methods"][2]["inputRequirements"]["unit"] = "process_row"
        self.assertEqual(registry.frame_changes(v2, self.catalog), ["ml:eif"])

    def test_write_refuses_an_unreadable_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "catalog.json"
            broken = '{"methods": [ broken'
            target.write_text(broken, encoding="utf-8")
            self.assertEqual(registry.main(["--write", "--output", str(target)]), 1)
            self.assertEqual(target.read_text(encoding="utf-8"), broken, "못 읽는 대상은 덮어쓰지 않는다")

    def test_result_codes_follow_the_machine_rule_and_are_unique(self):
        codes = {item["methodId"]: item["resultCode"] for item in self.catalog["methods"]}
        self.assertEqual(codes, {method_id: contract_result_code(method_id) for method_id in codes})
        self.assertEqual(codes, {"ml:iforest": "IF_ANOMALY", "ml:minibatch_kmeans": "ML_MINIBATCH_KMEANS", "ml:eif": "ML_EIF"})
        self.assertEqual(len(set(codes.values())), len(codes))
        self.assertEqual(registry.RESULT_CODE_EXCEPTIONS, {"ml:iforest": "IF_ANOMALY"}, "호환 예외는 하나뿐")
        self.assertEqual([method_id for method_id, code in codes.items() if not code.startswith("ML_")], ["ml:iforest"])
        for method_id in ("ml:lof", "ml:trend-iforest", "ml:trend-hgb-residual", "ml:trend-eif"):
            self.assertEqual(registry.result_code_for(method_id), contract_result_code(method_id))
        self.assertEqual(registry.result_code_for("ml:trend-iforest"), "ML_TREND_IFOREST")

    def test_trend_prefix_matches_frame(self):
        self.assertEqual(registry.TREND_PREFIX_LEGACY, frozenset({"ml:eif"}), "새 예외를 더하지 않는다")
        for item in self.catalog["methods"]:
            prefixed = item["methodId"].startswith("ml:trend-")
            if item["methodId"] in registry.TREND_PREFIX_LEGACY:
                self.assertEqual((prefixed, item["frame"], item["status"]), (False, "trend", "research"))
                self.assertFalse(any(item["executable"].values()), "예외는 실행할 수 없는 동안만")
            else:
                self.assertEqual(prefixed, item["frame"] == "trend", item["methodId"])

    def test_validator_rejects_frame_prefix_and_result_code_breaks(self):
        validator = jsonschema.Draft202012Validator(SCHEMA)
        for catalog in (self.catalog, self.with_method("ml:lof", "process_row"),
                        self.with_method("ml:trend-iforest", "aggregated_window")):
            registry.validate_catalog(catalog)
            validator.validate(catalog)
        broken = copy.deepcopy(self.catalog)
        broken["methods"][0]["frame"] = "trend"
        self.rejected(broken, "frame_unit_mismatch:ml:iforest")
        self.assertFalse(validator.is_valid(broken))
        self.rejected(self.with_method("ml:lof", "process_row", frame="trend"), "frame_unit_mismatch:ml:lof")
        self.rejected(self.with_method("ml:trend-lof", "process_row"), "trend_prefix_mismatch:ml:trend-lof")
        self.assertFalse(validator.is_valid(self.with_method("ml:trend-lof", "process_row")))
        self.rejected(self.with_method("ml:lof", "aggregated_window"), "trend_prefix_mismatch:ml:lof")
        for change in ({"status": "supported"}, {"executable": {"process": False, "window": True}}):
            legacy = copy.deepcopy(self.catalog)
            legacy["methods"][2].update(change)
            self.rejected(legacy, "trend_prefix_mismatch:ml:eif")
        self.rejected(self.with_method("ml:lof", "process_row", resultCode="LOF_ANOMALY"), "result_code_mismatch:ml:lof")
        self.rejected(self.with_method("ml:lof", "process_row", resultCode="IF_ANOMALY"), "result_code_mismatch:ml:lof")
        twins = self.with_method("ml:trend-hgb-residual", "aggregated_window")
        twins["methods"].append({**copy.deepcopy(twins["methods"][-1]), "methodId": "ml:trend-hgb_residual"})
        self.rejected(twins, "result_code_duplicate:ML_TREND_HGB_RESIDUAL")
        twice = self.with_method("ml:lof", "process_row")
        twice["methods"].append(copy.deepcopy(twice["methods"][-1]))
        self.rejected(twice, "method_duplicate:ml:lof")

    def test_frames_have_one_name_and_at_most_one_recommendation(self):
        frames = self.catalog["frames"]
        self.assertEqual(list(frames), ["row", "trend"])
        self.assertEqual({frame: entry["displayName"] for frame, entry in frames.items()},
                         {"row": "행 단위 모델", "trend": "시계열 모델"})
        for entry in frames.values():  # 실험 전: 추천 없음(운영 중인 IF를 추천으로 표시하지 않음)
            self.assertEqual((entry["recommendedMethodId"], entry["recommendedParameters"], entry["evidence"]), (None, None, None))
        validator = jsonschema.Draft202012Validator(SCHEMA)
        valid = self.recommend("row", "ml:iforest")
        registry.validate_catalog(valid)
        validator.validate(valid)
        self.rejected(self.recommend("row", "ml:nope"), "frame_recommendation_unknown:row")
        self.rejected(self.recommend("trend", "ml:iforest"), "frame_recommendation_frame_mismatch:trend")
        self.rejected(self.recommend("row", "ml:minibatch_kmeans", {"k": 6}), "frame_recommendation_not_executable:row")
        self.rejected(self.recommend("trend", "ml:eif", {"ntrees": 128}), "frame_recommendation_not_executable:trend")
        self.rejected(self.recommend("row", "ml:iforest", {"n_estimators": 8}), "frame_recommendation_parameters_invalid:row")
        self.rejected(self.recommend("row", "ml:iforest", evidence=None), "frame_recommendation_incomplete:row")
        self.assertFalse(validator.is_valid(self.recommend("row", "ml:iforest", evidence=None)))
        self.rejected(self.recommend("row", "ml:iforest", evidence={**EVIDENCE, "metric": " "}),
                      "frame_recommendation_evidence_invalid:row")
        half = copy.deepcopy(self.catalog)
        half["frames"]["row"]["evidence"] = dict(EVIDENCE)
        self.rejected(half, "frame_recommendation_incomplete:row")
        self.assertFalse(validator.is_valid(half))
        unnamed = copy.deepcopy(self.catalog)
        unnamed["frames"]["trend"]["displayName"] = ""
        self.rejected(unnamed, "frame_display_name_missing:trend")
        for frames_value in ({"row": frames["row"]}, {**frames, "window": frames["row"]}):
            changed = copy.deepcopy(self.catalog)
            changed["frames"] = copy.deepcopy(frames_value)
            self.rejected(changed, "frames_invalid")


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
