import copy
import json
import pickle
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector.decision import component_status, decide, decision_columns, ensure_executable, requested_mode
from detector.evidence_v4 import INTERIM_EVIDENCE_VERSION, PinnedRevisions, build_evidence, build_interim_payload
from detector.hard_rules import (HARD_RULES, NEGATIVE_DURATION, PROCESS_STATUS, evaluate_hard_rules,
                                 evaluate_negative_duration, evaluate_process_status)
from detector.method_results import (method_category, method_result, ml_method_result, unresolved_result,
                                     with_elapsed)
from detector.policy import Policy, PolicyV2, parse_policy, parse_policy_v2, parse_versioned_policy
from detector.soft_rules import evaluate_soft_rule, fixed_decimal

SOURCE = "11111111-1111-1111-1111-111111111111"
HASH = "a" * 64
START = "2026-01-01T00:00:00"
END = "2026-01-01T00:00:08.120"
BOTH_HARD = (PROCESS_STATUS, NEGATIVE_DURATION)
FIELDS = {
    "retry_count": {"fieldId": "retry_count", "valueType": "integer", "unit": "count",
                    "source": {"kind": "column", "column": "RETRY_COUNT", "ruleDslField": "P.retry_count"}},
    "elapsed_ms": {"fieldId": "elapsed_ms", "valueType": "number", "unit": "ms",
                   "source": {"kind": "derived", "ast": {"op": "datetime_diff", "unit": "ms", "args": [
                       {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "END_TIME"},
                       {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "START_TIME"}]}},
                   "sourceMissingPolicy": {"END_TIME": "not_applicable", "START_TIME": "unavailable"}},
    "error_ratio": {"fieldId": "error_ratio", "valueType": "number", "unit": "ratio",
                    "source": {"kind": "derived", "ast": {"op": "divide", "args": [
                        {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "ERROR_COUNT"},
                        {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"}]}}},
}


def policy(hard=1, soft=1, ml=1, deadline=3000):
    return parse_policy_v2({"version": 2, "policyKey": "category-k-of-n-or-v1",
                            "k": {"hard": hard, "soft": soft, "ml": ml}, "deadlineMs": deadline})


def soft_rule(field="retry_count", operator="gte", threshold=3, severity="warning", suffix="3f9a2c1d", **changes):
    units = {"retry_count": "count", "elapsed_ms": "ms", "error_ratio": "ratio"}
    return {"id": f"soft:{field}-{suffix}", "field": field, "operator": operator, "threshold": threshold,
            "unit": units.get(field, "count"), "severity": severity, "enabled": True, **changes}


def stamped(methods, start=1.0):
    return [with_elapsed(method, start + index) for index, method in enumerate(methods)]


def hard(raw, enabled=BOTH_HARD):
    return evaluate_hard_rules(enabled, raw, SOURCE)


def soft(raw, **rule):
    return evaluate_soft_rule(soft_rule(**rule), FIELDS, raw)


def model(key, anomalous):
    spec = {"direction": "higher_is_anomaly", "value": "0.61"}
    return ml_method_result(f"ml:{key}", "process-iforest-v1", f"version-{key}", .9 if anomalous else .3,
                            spec, "train-internal-p99-v1", code="IF_ANOMALY")


def timed_out_model():
    return unresolved_result("ml:iforest", "process-iforest-v1", "timeout",
                             modelVersionId="version-iforest", score=None, scoreDetail=None)


def raw(status="S", start=START, end=END, **values):
    return {"status": status, "start_time_raw": start, "end_time_raw": end, "retry_count": 0, **values}


class PolicyParsing(unittest.TestCase):
    def valid(self, **changes):
        return {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1},
                "deadlineMs": 3000, **changes}

    def test_version_key_selects_the_parser(self):
        self.assertIsInstance(parse_versioned_policy({"detectionPolicy": self.valid()}), PolicyV2)
        v1 = {"mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly"}
        parsed = parse_versioned_policy({"detectionPolicy": v1})
        self.assertIsInstance(parsed, Policy)
        self.assertEqual(parsed, parse_policy({"detectionPolicy": v1}))
        self.assertTrue(parse_versioned_policy({}).legacy)
        self.assertEqual(parse_versioned_policy({}), parse_policy({}))

    def test_version_one_and_mixed_keys_are_rejected(self):
        for value in ({"version": 1, "mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly"},
                      self.valid(version=1), self.valid(version=3), self.valid(version=True), self.valid(version=2.0),
                      self.valid(version="2"), {**self.valid(), "mode": "parallel"},
                      {"mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly", "k": {}},
                      {"mode": "parallel", "deadlineMs": 3000, "combination": "any_anomaly", "policyKey": "x"}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_detection_policy$"):
                parse_versioned_policy({"detectionPolicy": value})

    def test_v1_errors_are_unchanged(self):
        with self.assertRaisesRegex(ValueError, "^detection_policy_required$"):
            parse_versioned_policy({"detectionPolicy": {"mode": "parallel"}})
        with self.assertRaisesRegex(ValueError, "^detection_policy_required$"):
            parse_policy({"detectionPolicy": self.valid()})
        with self.assertRaisesRegex(ValueError, "^detection_policy_required$"):
            parse_versioned_policy({}, required=True)

    def test_shape_errors(self):
        values = [None, [], "x", {}, {k: v for k, v in self.valid().items() if k != "k"}, {**self.valid(), "extra": 1}]
        values += [self.valid(deadlineMs=item) for item in (99, 30001, 3000.0, "3000", True, None)]
        values += [self.valid(k=item) for item in (
            None, [], {"hard": 1, "soft": 1}, {"hard": 1, "soft": 1, "ml": 1, "sr": 1},
            {"hr": 1, "sr": 1, "ml": 1}, {"hard": 1.0, "soft": 1, "ml": 1}, {"hard": True, "soft": 1, "ml": 1},
            {"hard": "1", "soft": 1, "ml": 1})]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_detection_policy$"):
                parse_policy_v2(value)

    def test_deadline_bounds_are_inclusive(self):
        self.assertEqual(parse_policy_v2(self.valid(deadlineMs=100)).deadline_ms, 100)
        self.assertEqual(parse_policy_v2(self.valid(deadlineMs=30000)).deadline_ms, 30000)

    def test_unknown_policy_key(self):
        for key in ("other-v1", None, 3, []):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "^unsupported_detection_policy$"):
                parse_policy_v2(self.valid(policyKey=key))

    def test_all_one_is_a_save_rule_not_a_parse_rule(self):
        for k in (0, 2, -1):
            self.assertEqual(parse_policy_v2(self.valid(k={"hard": 1, "soft": 1, "ml": k})).k["ml"], k)

    def test_documents_come_from_one_object(self):
        parsed = parse_policy_v2(self.valid())
        self.assertEqual(parsed.document(), self.valid())
        self.assertEqual(parsed.combination(), {k: v for k, v in self.valid().items() if k != "deadlineMs"})
        parsed.document()["k"]["hard"] = 9
        parsed.combination()["k"]["hard"] = 9
        parsed.k["hard"] = 9
        self.assertEqual(parsed.k["hard"], 1)
        self.assertEqual(parsed.document(), self.valid())
        self.assertIsNot(parsed.document(), parsed.document())
        self.assertIsNot(parsed.document()["k"], parsed.document()["k"])

    def test_changing_the_source_after_parsing_has_no_effect(self):
        source = self.valid()
        parsed = parse_policy_v2(source)
        source["k"]["hard"] = 9
        source["deadlineMs"] = 100
        self.assertEqual(parsed.document(), self.valid())

    def test_policy_is_safe_to_copy_and_hash(self):
        parsed = parse_policy_v2(self.valid(k={"ml": 3, "hard": 1, "soft": 2}))
        for clone in (copy.copy(parsed), copy.deepcopy(parsed), pickle.loads(pickle.dumps(parsed))):
            self.assertEqual(clone, parsed)
            self.assertEqual(hash(clone), hash(parsed))
            self.assertEqual(clone.document(), parsed.document())
        self.assertEqual(parsed, parse_policy_v2(self.valid(k={"hard": 1, "soft": 2, "ml": 3})))
        self.assertEqual(len({parsed, copy.deepcopy(parsed)}), 1)
        with self.assertRaises(AttributeError):
            parsed.deadline_ms = 100


class BuiltInHardRules(unittest.TestCase):
    def test_list_matches_contract_values(self):
        self.assertEqual(list(HARD_RULES), [
            {"id": "hard:process-status", "methodVersion": "process-status-hr-v1",
             "displayName": "상태가 S가 아님", "conditionText": "STATUS ≠ S",
             "severity": {"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning",
                                     "PROCESS_STATUS_N": "warning"}}},
            {"id": "hard:negative-duration", "methodVersion": "negative-duration-v1",
             "displayName": "시작 시각이 종료 시각보다 늦음",
             "conditionText": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)", "severity": "critical"}])

    def test_process_status_keeps_status_rule_meaning(self):
        for status, level in (("F", "critical"), ("P", "warning"), ("N", "warning")):
            result = evaluate_process_status({"status": status}, SOURCE)
            self.assertEqual((result["status"], result["anomalous"], result["code"], result["severity"],
                              result["riskLevel"]), ("succeeded", True, "PROCESS_STATUS_" + status, level, level))
        result = evaluate_process_status({"status": "S"}, SOURCE)
        self.assertEqual((result["status"], result["anomalous"], result["code"], result["riskLevel"]),
                         ("succeeded", False, None, None))
        self.assertEqual(result["evidence"], [{"observedStatus": "S", "result": "no_hit"}])

    def test_process_status_undetermined_causes(self):
        for value, cause in ((None, "input_missing"), ("", "input_missing"), ("H", "input_invalid"),
                             ("Z", "input_invalid"), (1, "input_invalid")):
            result = evaluate_process_status({"status": value}, SOURCE)
            self.assertEqual((result["status"], result["anomalous"], result["cause"]), ("not_ready", None, cause))
        self.assertEqual(evaluate_process_status({}, SOURCE)["cause"], "input_missing")

    def assert_duration(self, raw_values, status, anomalous, cause=None, evidence=None):
        result = evaluate_negative_duration(raw_values)
        self.assertEqual((result["status"], result["anomalous"], result["cause"]), (status, anomalous, cause))
        if evidence is not None:
            self.assertEqual(result["evidence"], evidence)
        return result

    def test_negative_duration_decision_order(self):
        not_applicable = [{"result": "not_applicable", "reason": "end_time_absent"}]
        self.assert_duration({}, "succeeded", False, evidence=not_applicable)
        self.assert_duration({"start_time_raw": "bad"}, "succeeded", False, evidence=not_applicable)
        self.assert_duration({"start_time_raw": START, "end_time_raw": None}, "succeeded", False, evidence=not_applicable)
        self.assert_duration({"start_time_raw": START, "end_time_raw": "  "}, "succeeded", False, evidence=not_applicable)
        self.assert_duration({"end_time_raw": END}, "not_ready", None, "input_missing")
        self.assert_duration({"end_time_raw": "bad"}, "not_ready", None, "input_missing")
        self.assert_duration({"start_time_raw": "bad", "end_time_raw": END}, "not_ready", None, "input_invalid")
        self.assert_duration({"start_time_raw": START, "end_time_raw": "bad"}, "not_ready", None, "input_invalid")
        self.assert_duration({"start_time_raw": 7, "end_time_raw": END}, "not_ready", None, "input_invalid")
        self.assert_duration({"start_time_raw": START, "end_time_raw": "2026-01-01T00:00:08+09:00"},
                             "not_ready", None, "input_invalid")

    def test_negative_duration_has_no_tolerance(self):
        self.assert_duration({"start_time_raw": START, "end_time_raw": START}, "succeeded", False,
                             evidence=[{"result": "no_hit"}])
        result = self.assert_duration({"start_time_raw": "2026-01-01T00:00:00.001", "end_time_raw": START},
                                      "succeeded", True, evidence=[{"result": "hit"}])
        self.assertEqual((result["code"], result["severity"], result["riskLevel"]),
                         ("HARD_NEGATIVE_DURATION", "critical", "critical"))
        self.assert_duration({"start_time_raw": "2026-01-01 10:00:00", "end_time_raw": "2026-01-01 09:59:59"},
                             "succeeded", True)
        self.assert_duration({"start_time_raw": "2026-01-01T09:00:00+09:00", "end_time_raw": "2026-01-01T00:00:01+00:00"},
                             "succeeded", False)

    def test_only_enabled_rules_run_in_list_order(self):
        self.assertEqual([item["methodId"] for item in hard(raw(), (NEGATIVE_DURATION, PROCESS_STATUS))],
                         list(BOTH_HARD))
        self.assertEqual([item["methodId"] for item in hard(raw(), (NEGATIVE_DURATION,))], [NEGATIVE_DURATION])
        with self.assertRaisesRegex(ValueError, "^hard_rule_unknown:hard:final-missing-end$"):
            hard(raw(), ("hard:final-missing-end",))


class SoftRules(unittest.TestCase):
    def assert_outcome(self, result, status, anomalous, cause=None, failure_code=None):
        self.assertEqual((result["status"], result["anomalous"], result["cause"], result["failureCode"]),
                         (status, anomalous, cause, failure_code))

    def test_operators_at_and_around_the_threshold(self):
        cases = {"gte": (False, True, True), "gt": (False, False, True),
                 "lte": (True, True, False), "lt": (True, False, False)}
        for operator, expected in cases.items():
            for value, anomalous in zip((2, 3, 4), expected):
                with self.subTest(operator=operator, value=value):
                    self.assert_outcome(soft({"retry_count": value}, operator=operator), "succeeded", anomalous)

    def test_hit_records_contract_fields(self):
        result = soft(raw(end=END), field="elapsed_ms", operator="gt", threshold=5000, severity="warning")
        self.assertEqual({key: result[key] for key in ("methodId", "category", "methodVersion", "code", "severity",
                                                       "riskLevel", "riskLevelReason", "condition", "observedValue",
                                                       "evidence")},
                         {"methodId": "soft:elapsed_ms-3f9a2c1d", "category": "soft",
                          "methodVersion": "soft-threshold-v1", "code": "SOFT_ELAPSED_MS_3F9A2C1D",
                          "severity": "warning", "riskLevel": "warning", "riskLevelReason": None,
                          "condition": {"field": "elapsed_ms", "operator": "gt", "threshold": "5000", "unit": "ms"},
                          "observedValue": "8120", "evidence": []})

    def test_no_hit_has_no_code_or_level(self):
        result = soft({"retry_count": 1})
        self.assertEqual((result["code"], result["severity"], result["riskLevel"], result["observedValue"]),
                         (None, None, None, "1"))

    def test_fixed_decimal_conversion(self):
        for value, text in ((8000.0, "8000"), (5000, "5000"), (0.01, "0.01"), (0.0100, "0.01"), (1e-07, "0.0000001"),
                            (1e22, "10000000000000000000000"), (-2.5, "-2.5"), (0, "0"), (-0.0, "0"), (0.0, "0"),
                            (1200, "1200"), (1.5e3, "1500")):
            with self.subTest(value=value):
                self.assertEqual(fixed_decimal(value), text)
                self.assertNotIn("e", fixed_decimal(value).lower())

    def test_fixed_decimal_rejections(self):
        for value in (float("nan"), float("inf"), float("-inf"), True, None, "5000", Decimal("1"), [], 10 ** 41):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_threshold$"):
                fixed_decimal(value)
        self.assertEqual(len(fixed_decimal(10 ** 39)), 40)
        self.assertEqual(len(fixed_decimal(-(10 ** 38))), 40)
        for value in (10 ** 40, -(10 ** 39)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_threshold$"):
                fixed_decimal(value)
        self.assertEqual(fixed_decimal(3.0, integer=True), "3")
        with self.assertRaisesRegex(ValueError, "^invalid_threshold$"):
            fixed_decimal(3.5, integer=True)

    def test_threshold_in_evidence_is_fixed_decimal_and_compared_exactly(self):
        result = soft({"error_count": 1, "total_count": 10000000}, field="error_ratio", operator="gte", threshold=1e-07)
        self.assertEqual(result["condition"]["threshold"], "0.0000001")
        self.assert_outcome(result, "succeeded", True)
        self.assertEqual(result["observedValue"], "0.0000001")

    def test_observed_value_has_no_exponent(self):
        self.assertEqual(soft({"start_time_raw": START, "end_time_raw": "2026-01-01T00:00:10"}, field="elapsed_ms",
                              threshold=1)["observedValue"], "10000")

    def test_missing_input_is_undetermined(self):
        for values in ({}, {"retry_count": None}, {"retry_count": ""}):
            result = soft(values)
            self.assert_outcome(result, "not_ready", None, "input_missing")
            self.assertIsNone(result["observedValue"])
        self.assert_outcome(soft({"total_count": 3}, field="error_ratio"), "not_ready", None, "input_missing")

    def test_not_applicable_comes_before_other_missing_columns(self):
        expected = [{"result": "not_applicable", "reason": "end_time_absent"}]
        for values in ({"start_time_raw": START}, {}, {"start_time_raw": "bad"}, {"start_time_raw": START, "end_time_raw": ""}):
            with self.subTest(values=values):
                result = soft(values, field="elapsed_ms", threshold=1)
                self.assert_outcome(result, "succeeded", False)
                self.assertEqual(result["evidence"], expected)
                self.assertIsNone(result["code"])
                self.assertIsNone(result["observedValue"])

    def test_unavailable_policy_column_is_undetermined(self):
        self.assert_outcome(soft({"end_time_raw": END}, field="elapsed_ms", threshold=1), "not_ready", None, "input_missing")

    def test_derived_calculation_failure(self):
        for values, field in (({"start_time_raw": "bad", "end_time_raw": END}, "elapsed_ms"),
                              ({"start_time_raw": START, "end_time_raw": "bad"}, "elapsed_ms"),
                              ({"error_count": 1, "total_count": 0}, "error_ratio")):
            with self.subTest(values=values):
                self.assert_outcome(soft(values, field=field, threshold=1), "not_ready", None, "feature_unavailable")

    def test_non_numeric_and_non_finite_input_fail_only_this_rule(self):
        for value in ("abc", True, "NaN", "Infinity", float("inf"), [1]):
            with self.subTest(value=value):
                result = soft({"retry_count": value})
                self.assert_outcome(result, "failed", None, "input_invalid")
                self.assertIsNone(result["observedValue"])

    def test_numeric_text_is_a_number(self):
        self.assert_outcome(soft({"retry_count": "4"}), "succeeded", True)

    def test_invalid_definitions(self):
        definition = ("failed", None, "failure", "invalid_rule_definition")
        rules = [soft_rule(field="unknown_field"), soft_rule(operator="eq"), soft_rule(operator=["gte"]),
                 soft_rule(severity="high"), soft_rule(unit="ms"), soft_rule(field=None, id="soft:retry_count-3f9a2c1d")]
        for rule in rules:
            with self.subTest(rule=rule):
                self.assert_outcome(evaluate_soft_rule(rule, FIELDS, {"retry_count": 9}), *definition)
        broken = {"retry_count": {**FIELDS["retry_count"], "source": {"kind": "column", "column": "START_TIME",
                                                                      "ruleDslField": "P.start_time_raw"}}}
        self.assert_outcome(evaluate_soft_rule(soft_rule(), broken, {"start_time_raw": START}), *definition)
        broken = {"elapsed_ms": {**FIELDS["elapsed_ms"], "sourceMissingPolicy": {"END_TIME": "ignore"}}}
        self.assert_outcome(evaluate_soft_rule(soft_rule(field="elapsed_ms"), broken, raw()), *definition)
        broken = {"elapsed_ms": {**FIELDS["elapsed_ms"], "source": {"kind": "derived", "ast": {"op": "exec"}}}}
        self.assert_outcome(evaluate_soft_rule(soft_rule(field="elapsed_ms"), broken, raw()), *definition)

    def test_mismatched_dsl_field_and_unused_policy_column_are_invalid(self):
        definition = ("failed", None, "failure", "invalid_rule_definition")
        broken = {"retry_count": {**FIELDS["retry_count"], "source": {"kind": "column", "column": "RETRY_COUNT",
                                                                      "ruleDslField": "P.total_count"}}}
        self.assert_outcome(evaluate_soft_rule(soft_rule(), broken, {"retry_count": 9, "total_count": 0}), *definition)
        broken = {"retry_count": {**FIELDS["retry_count"], "sourceMissingPolicy": {"END_TIME": "not_applicable"}}}
        self.assert_outcome(evaluate_soft_rule(soft_rule(), broken, {"retry_count": 9}), *definition)

    def test_malformed_ast_fails_only_this_rule(self):
        column = {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "END_TIME"}
        asts = ({"op": ["column"], "table": "EC_PROCESS_MONITORING", "column": "END_TIME"},
                {"op": "column", "table": "EC_PROCESS_MONITORING", "column": ["END_TIME"]},
                {"op": "datetime_diff", "unit": ["ms"], "args": [column, column]},
                {"op": "convert_unit", "from_unit": ["s"], "to_unit": "ms", "args": [column]},
                {"op": {"nested": 1}, "args": []}, ["column"], None)
        for ast in asts:
            with self.subTest(ast=ast):
                broken = {"elapsed_ms": {**FIELDS["elapsed_ms"], "sourceMissingPolicy": {},
                                         "source": {"kind": "derived", "ast": ast}}}
                bad = evaluate_soft_rule(soft_rule(field="elapsed_ms", suffix="00000001"), broken, raw())
                self.assert_outcome(bad, "failed", None, "failure", "invalid_rule_definition")
                good = evaluate_soft_rule(soft_rule(suffix="00000002"), FIELDS, {"retry_count": 9})
                decided = decide(policy(), stamped(hard(raw("F")) + [bad, good]))
                self.assertEqual(decided["composite"]["basisMethods"], [PROCESS_STATUS, good["methodId"]])
                self.assertEqual(decided["composite"]["status"], "partial")

    def test_invalid_threshold_is_a_rule_failure(self):
        for value in (float("nan"), "5", None, 2.5):
            with self.subTest(value=value):
                self.assert_outcome(soft({"retry_count": 9}, threshold=value), "failed", None, "failure", "invalid_threshold")

    def test_non_soft_identifiers_fail_closed(self):
        for identifier in ("hard:process-status", "sr:r01", "soft:", None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                evaluate_soft_rule(soft_rule(id=identifier), FIELDS, {})


class TruthTable(unittest.TestCase):
    """detection-decision.md 14절 표. 위험도 칸은 (status, level, determinedBy, unratedMethods)."""
    SIGN = {"anomalous": "T", "normal": "N", "undetermined": "U", "skipped": "-"}
    NONE = ("not_applicable", None, [], [])

    def check(self, methods, hard_sign, soft_sign, ml_sign, status, anomalous, grade, risk, *, active_policy=None):
        decided = decide(active_policy or policy(), stamped(methods))
        categories = decided["categoryResults"]
        composite = decided["composite"]
        self.assertEqual(tuple(self.SIGN[categories[name]["result"]] for name in ("hard", "soft", "ml")),
                         (hard_sign, soft_sign, ml_sign))
        self.assertEqual((composite["status"], composite["anomalous"], composite["grade"]), (status, anomalous, grade))
        actual = composite["risk"]
        self.assertEqual((actual["status"], actual["level"], actual["determinedBy"], actual["unratedMethods"]), risk)
        self.assertEqual((actual["policy"], actual["levelTable"]), ("risk-max-v1", "method-defined-v2"))
        return decided

    def test_01_hard_only_status_s(self):
        self.check(hard(raw()), "N", "-", "-", "complete", False, "normal", self.NONE)

    def test_02_hard_only_status_missing(self):
        self.check(hard({}, (PROCESS_STATUS,)), "U", "-", "-", "unavailable", None, "undetermined", self.NONE)

    def test_03_status_f_and_if_timeout(self):
        self.check(hard(raw("F")) + [timed_out_model()], "T", "-", "U", "partial", True, "anomaly",
                   ("rated", "critical", [PROCESS_STATUS], []))

    def test_04_status_p_and_if_normal(self):
        self.check(hard(raw("P")) + [model("iforest", False)], "T", "-", "N", "complete", True, "anomaly",
                   ("rated", "warning", [PROCESS_STATUS], []))

    def test_05_soft_warning_and_if_normal(self):
        rule = soft({"retry_count": 5})
        self.check(hard(raw()) + [rule, model("iforest", False)], "N", "T", "N", "complete", True, "anomaly_sign",
                   ("rated", "warning", [rule["methodId"]], []))

    def test_06_soft_critical_and_if_anomalous(self):
        rule = soft({"retry_count": 5}, severity="critical")
        self.check(hard(raw()) + [rule, model("iforest", True)], "N", "T", "T", "complete", True, "anomaly_sign",
                   ("rated", "critical", [rule["methodId"]], ["ml:iforest"]))

    def test_07_three_models_t_n_n(self):
        self.check(hard(raw()) + [model("aa", True), model("bb", False), model("cc", False)],
                   "N", "-", "T", "complete", True, "anomaly_sign", ("unrated", None, [], ["ml:aa"]))

    def test_08_same_with_k_ml_two(self):
        decided = self.check(hard(raw()) + [model("aa", True), model("bb", False), model("cc", False)],
                             "N", "-", "N", "complete", False, "normal", self.NONE, active_policy=policy(ml=2))
        self.assertEqual(decided["composite"]["basisMethods"], [])
        self.assertIsNone(decided["composite"]["firstAnomalyElapsedMs"])

    def test_09_three_models_n_u_n(self):
        self.check(hard(raw()) + [model("aa", False), unresolved_result("ml:bb", "v", "timeout"), model("cc", False)],
                   "N", "-", "U", "partial", None, "undetermined", self.NONE)

    def test_10_soft_t_and_u_with_if_normal(self):
        hit = soft({"retry_count": 5}, severity="info", suffix="00000001")
        missing = soft({}, suffix="00000002")
        self.check(hard(raw()) + [hit, missing, model("iforest", False)], "N", "T", "N", "partial", True,
                   "anomaly_sign", ("rated", "info", [hit["methodId"]], []))

    def test_11_soft_n_and_u_without_ml(self):
        self.check(hard(raw()) + [soft({"retry_count": 1}, suffix="00000001"), soft({}, suffix="00000002")],
                   "N", "U", "-", "partial", None, "undetermined", self.NONE)

    def test_12_status_f_and_non_numeric_soft(self):
        methods = hard(raw("F")) + [soft({"retry_count": "abc"})]
        self.check(methods, "T", "U", "-", "partial", True, "anomaly", ("rated", "critical", [PROCESS_STATUS], []))
        self.assertEqual(methods[-1]["status"], "failed")

    def test_13_status_missing_and_soft_hit(self):
        rule = soft({"retry_count": 5})
        self.check(hard({"start_time_raw": START, "end_time_raw": END}) + [rule], "U", "T", "-", "partial", True,
                   "anomaly_sign", ("rated", "warning", [rule["methodId"]], []))

    def test_14_hard_and_soft_off_if_anomalous(self):
        decided = self.check([model("iforest", True)], "-", "-", "T", "complete", True, "anomaly_sign",
                             ("unrated", None, [], ["ml:iforest"]))
        self.assertEqual(requested_mode(decided["methodResults"]), "ml_only")

    def test_15_negative_duration_hit(self):
        self.check(hard(raw(start=END, end=START)), "T", "-", "-", "complete", True, "anomaly",
                   ("rated", "critical", [NEGATIVE_DURATION], []))

    def test_16_end_time_absent(self):
        self.check(hard(raw(end=None)), "N", "-", "-", "complete", False, "normal", self.NONE)

    def test_17_no_methods_or_k_above_n_makes_no_decision(self):
        with self.assertRaisesRegex(ValueError, "^detection_methods_empty$"):
            decide(policy(), [])
        with self.assertRaisesRegex(ValueError, "^combination_k_out_of_range:ml$"):
            decide(policy(ml=2), stamped([model("iforest", True)]))


class Combination(unittest.TestCase):
    def test_default_hard_rules_with_missing_status_are_partial(self):
        # 하드룰 2개가 켜져 있으면 시각 규칙의 succeeded가 있어 14절 2행과 달리 unavailable이 아니다.
        decided = decide(policy(), stamped(hard({"start_time_raw": START, "end_time_raw": END})))
        self.assertEqual((decided["composite"]["status"], decided["composite"]["anomalous"]), ("partial", None))

    def test_all_hard_rules_undetermined_is_unavailable(self):
        decided = decide(policy(), stamped(hard({"end_time_raw": END})))
        self.assertEqual((decided["composite"]["status"], decided["composite"]["grade"]), ("unavailable", "undetermined"))

    def test_runtime_fail_closed(self):
        for k, methods, code in (
                (dict(hard=2), BOTH_HARD[:1], "combination_k_out_of_range:hard"),
                (dict(soft=3), ["soft:aa-00000001", "soft:aa-00000002"], "combination_k_out_of_range:soft"),
                (dict(ml=2), ["ml:iforest"], "combination_k_out_of_range:ml"),
                (dict(ml=0), ["ml:iforest"], "combination_k_out_of_range:ml"),
                (dict(hard=-1), BOTH_HARD, "combination_k_out_of_range:hard"),
                (dict(), [], "detection_methods_empty"),
                (dict(), ["sr:r01"], "method_kind_not_allowed:sr:r01"),
                (dict(), ["hr:process-status"], "method_kind_not_allowed:hr:process-status"),
                (dict(), ["rule:x1"], "method_kind_not_allowed:rule:x1"),
                (dict(), ["ml:iforest", "ml:iforest"], "duplicate_method_id")):
            with self.subTest(k=k, methods=methods), self.assertRaisesRegex(ValueError, f"^{code}$"):
                ensure_executable(policy(**k), methods)

    def test_k_of_inactive_category_is_ignored(self):
        ensure_executable(policy(soft=9, ml=0), list(BOTH_HARD))
        decided = decide(policy(soft=9, ml=0), stamped(hard(raw())))
        self.assertEqual(decided["categoryResults"]["soft"],
                         {"n": 0, "k": 9, "result": "skipped", "status": "skipped", "anomalous": None})
        self.assertEqual(decided["categoryResults"]["ml"]["k"], 0)

    def test_k_is_not_clamped_to_n(self):
        with self.assertRaisesRegex(ValueError, "^combination_k_out_of_range:ml$"):
            decide(policy(ml=3), stamped([model("aa", True), model("bb", True)]))

    def test_no_early_normal_while_a_method_is_unresolved(self):
        # 남은 방법이 T여도 k에 못 미치는 상황이지만 조기 정상을 쓰지 않는다.
        decided = decide(policy(ml=3), stamped([model("aa", False), model("bb", False),
                                                unresolved_result("ml:cc", "v", "timeout")]))
        self.assertEqual(decided["categoryResults"]["ml"]["result"], "undetermined")
        self.assertIsNone(decided["composite"]["anomalous"])

    def test_category_status_mapping(self):
        ok, waiting = model("aa", False), unresolved_result("ml:bb", "v", "timeout")
        failed = unresolved_result("ml:cc", "v", "failure", status="failed", failure_code="exception")
        self.assertEqual(component_status([]), "skipped")
        self.assertEqual(component_status([ok]), "succeeded")
        self.assertEqual(component_status([ok, waiting]), "not_ready")
        self.assertEqual(component_status([waiting, failed, ok]), "failed")

    def test_anomalous_category_keeps_incomplete_status(self):
        decided = decide(policy(), stamped([model("aa", True), unresolved_result("ml:bb", "v", "timeout")]))
        self.assertEqual(decided["categoryResults"]["ml"],
                         {"n": 2, "k": 1, "result": "anomalous", "status": "not_ready", "anomalous": True})

    def test_results_are_put_in_contract_order(self):
        first, second = soft({"retry_count": 5}, suffix="00000002"), soft({"retry_count": 5}, suffix="00000001")
        methods = [model("zz", True), model("aa", False), first, second] + list(reversed(hard(raw())))
        decided = decide(policy(), stamped(methods))
        self.assertEqual([item["methodId"] for item in decided["methodResults"]],
                         list(BOTH_HARD) + [first["methodId"], second["methodId"], "ml:zz", "ml:aa"])

    def test_confirmed_anomaly_counts_even_if_the_method_is_incomplete(self):
        partial = method_result("ml:aa", "v", "not_ready", True, cause="timeout", code="IF_ANOMALY")
        decided = decide(policy(), stamped([partial]))
        self.assertEqual((decided["composite"]["status"], decided["composite"]["anomalous"]), ("partial", True))
        self.assertEqual(decided["categoryResults"]["ml"]["result"], "anomalous")

    def test_requested_mode(self):
        self.assertEqual(requested_mode(hard(raw())), "rule_only")
        self.assertEqual(requested_mode([soft({})]), "rule_only")
        self.assertEqual(requested_mode([model("iforest", False)]), "ml_only")
        self.assertEqual(requested_mode([soft({}), model("iforest", False)]), "parallel")

    def test_rejects_methods_without_measured_time(self):
        with self.assertRaisesRegex(ValueError, "^method_elapsed_required$"):
            decide(policy(), hard(raw()))
        pending = with_elapsed(unresolved_result("ml:iforest", "v", "pending"), 1.0)
        with self.assertRaisesRegex(ValueError, "^pending_method_has_elapsed$"):
            decide(policy(), [pending])

    def test_unknown_builtin_and_mismatched_category(self):
        with self.assertRaisesRegex(ValueError, "^hard_rule_unknown:hard:other-rule$"):
            decide(policy(), [with_elapsed(method_result("hard:other-rule", "v1", "succeeded", False), 1.0)])
        forged = {**with_elapsed(model("iforest", False), 1.0), "category": "hard"}
        with self.assertRaisesRegex(ValueError, "^method_category_mismatch:ml:iforest$"):
            decide(policy(), [forged])


class GradeAndRisk(unittest.TestCase):
    def composite(self, methods, active_policy=None):
        return decide(active_policy or policy(), stamped(methods))["composite"]

    def test_hard_undetermined_does_not_hide_a_sign(self):
        self.assertEqual(self.composite(hard({}, (PROCESS_STATUS,)) + [model("iforest", True)])["grade"], "anomaly_sign")

    def test_hard_hit_wins_over_sign(self):
        self.assertEqual(self.composite(hard(raw("P")) + [soft({"retry_count": 5}, severity="critical")])["grade"], "anomaly")

    def test_level_is_the_highest_without_escalation(self):
        info = soft({"retry_count": 5}, severity="info", suffix="00000001")
        other = soft({"retry_count": 5}, severity="info", suffix="00000002")
        risk = self.composite(hard(raw()) + [info, other])["risk"]
        self.assertEqual((risk["status"], risk["level"]), ("rated", "info"))
        self.assertEqual(risk["determinedBy"], [info["methodId"], other["methodId"]])
        worse = soft({"retry_count": 5}, severity="critical", suffix="00000003")
        risk = self.composite(hard(raw("P")) + [info, worse])["risk"]
        self.assertEqual((risk["level"], risk["determinedBy"]), ("critical", [worse["methodId"]]))

    def test_method_below_k_is_not_basis(self):
        rule = soft({"retry_count": 5})
        composite = self.composite(hard(raw()) + [rule, model("aa", True), model("bb", False)], policy(ml=2))
        self.assertEqual(composite["basisMethods"], [rule["methodId"]])
        self.assertEqual((composite["risk"]["status"], composite["risk"]["level"], composite["risk"]["unratedMethods"]),
                         ("rated", "warning", []))

    def test_basis_only_from_anomalous_categories_for_hard_below_k(self):
        methods = hard(raw("P")) + [model("aa", True)]
        composite = self.composite(methods, policy(hard=2))
        self.assertEqual(composite["grade"], "anomaly_sign")
        self.assertEqual(composite["basisMethods"], ["ml:aa"])
        self.assertEqual((composite["risk"]["status"], composite["risk"]["unratedMethods"]), ("unrated", ["ml:aa"]))

    def test_unresolved_and_normal_methods_have_no_level(self):
        methods = decide(policy(), stamped(hard(raw("F")) + [soft({}), model("iforest", False), model("aa", True)]))
        by_id = {item["methodId"]: item for item in methods["methodResults"]}
        self.assertEqual((by_id[PROCESS_STATUS]["riskLevel"], by_id[PROCESS_STATUS]["riskLevelReason"]), ("critical", None))
        self.assertEqual((by_id[NEGATIVE_DURATION]["riskLevel"], by_id[NEGATIVE_DURATION]["riskLevelReason"]), (None, None))
        self.assertEqual((by_id["soft:retry_count-3f9a2c1d"]["riskLevel"],
                          by_id["soft:retry_count-3f9a2c1d"]["riskLevelReason"]), (None, None))
        self.assertEqual((by_id["ml:iforest"]["riskLevel"], by_id["ml:iforest"]["riskLevelReason"]), (None, None))
        self.assertEqual((by_id["ml:aa"]["riskLevel"], by_id["ml:aa"]["riskLevelReason"]), (None, "level_undefined"))

    def test_first_anomaly_time(self):
        late_hard = with_elapsed(evaluate_process_status({"status": "F"}, SOURCE), 30.0)
        early_soft = with_elapsed(soft({"retry_count": 5}), 10.0)
        self.assertEqual(decide(policy(), [late_hard, early_soft])["composite"]["firstAnomalyElapsedMs"], 10.0)
        models = [with_elapsed(model("aa", True), 5.0), with_elapsed(model("bb", True), 20.0),
                  with_elapsed(model("cc", False), 1.0)]
        self.assertEqual(decide(policy(ml=2), models)["composite"]["firstAnomalyElapsedMs"], 20.0)
        self.assertEqual(decide(policy(ml=2), models + [early_soft])["composite"]["firstAnomalyElapsedMs"], 10.0)


class Columns(unittest.TestCase):
    def columns(self, methods, active_policy=None):
        return decision_columns(decide(active_policy or policy(), stamped(methods)))

    def test_first_hit_order_uses_status_rule_first(self):
        methods = [model("iforest", True), soft({"retry_count": 5})] + hard(raw("P", start=END, end=START))
        self.assertEqual(self.columns(methods)["decision_code"], "PROCESS_STATUS_P")
        methods = [model("iforest", True), soft({"retry_count": 5})] + hard(raw(start=END, end=START))
        self.assertEqual(self.columns(methods)["decision_code"], "HARD_NEGATIVE_DURATION")
        methods = [model("iforest", True), soft({"retry_count": 5})] + hard(raw())
        self.assertEqual(self.columns(methods)["decision_code"], "SOFT_RETRY_COUNT_3F9A2C1D")
        self.assertEqual(self.columns(hard(raw()) + [model("iforest", True)])["decision_code"], "IF_ANOMALY")
        self.assertIsNone(self.columns(hard(raw()))["decision_code"])

    def test_statuses_follow_hard_plus_soft_and_ml(self):
        self.assertEqual(self.columns(hard(raw()) + [soft({"retry_count": "abc"})]),
                         {"status": "partial", "rule_status": "failed", "ml_status": "skipped",
                          "decision_code": None, "anomaly_score": None})
        self.assertEqual(self.columns([model("iforest", False)])["rule_status"], "skipped")
        columns = self.columns(hard({}, (PROCESS_STATUS,)) + [timed_out_model()])
        self.assertEqual((columns["rule_status"], columns["ml_status"], columns["status"]),
                         ("not_ready", "not_ready", "unavailable"))

    def test_score_only_for_exactly_one_active_model(self):
        self.assertEqual(self.columns([model("iforest", True)])["anomaly_score"], .9)
        self.assertIsNone(self.columns([model("aa", True), model("bb", False)])["anomaly_score"])
        self.assertIsNone(self.columns([timed_out_model()])["anomaly_score"])
        self.assertIsNone(self.columns(hard(raw()))["anomaly_score"])


class MachineLearningResults(unittest.TestCase):
    SPEC = {"direction": "higher_is_anomaly", "value": "0.61"}

    def result(self, score=.53, spec=None, policy_name="train-internal-p99-v1"):
        return ml_method_result("ml:iforest", "process-iforest-v1", "version", score,
                                self.SPEC if spec is None else spec, policy_name, code="IF_ANOMALY")

    def test_threshold_direction(self):
        self.assertFalse(self.result(.53)["anomalous"])
        self.assertTrue(self.result(.61)["anomalous"])
        low = {"direction": "lower_is_anomaly", "value": "0.5"}
        self.assertTrue(self.result(.5, low)["anomalous"])
        self.assertFalse(self.result(.6, low)["anomalous"])

    def test_score_detail_ratio(self):
        detail = self.result(.53)["scoreDetail"]
        self.assertEqual({key: detail[key] for key in ("raw", "threshold", "direction", "thresholdPolicy")},
                         {"raw": .53, "threshold": "0.61", "direction": "higher_is_anomaly",
                          "thresholdPolicy": "train-internal-p99-v1"})
        self.assertEqual(detail["normalized"]["kind"], "threshold_ratio")
        self.assertAlmostEqual(detail["normalized"]["value"], .53 / .61)
        low = self.result(.25, {"direction": "lower_is_anomaly", "value": "0.5"})["scoreDetail"]["normalized"]
        self.assertAlmostEqual(low["value"], 2.0)

    def test_ratio_is_null_when_denominator_is_not_positive(self):
        self.assertIsNone(self.result(.5, {"direction": "higher_is_anomaly", "value": 0})["scoreDetail"]["normalized"])
        self.assertIsNone(self.result(0, {"direction": "lower_is_anomaly", "value": "0.5"})["scoreDetail"]["normalized"])

    def test_ratio_is_null_when_denominator_is_negative(self):
        self.assertIsNone(self.result(.5, {"direction": "higher_is_anomaly", "value": -.5})["scoreDetail"]["normalized"])
        self.assertIsNone(self.result(-.5, {"direction": "lower_is_anomaly", "value": "0.5"})["scoreDetail"]["normalized"])

    def test_numeric_threshold_is_recorded_as_text(self):
        self.assertEqual(self.result(.5, {"direction": "higher_is_anomaly", "value": .61})["scoreDetail"]["threshold"], "0.61")
        self.assertEqual(self.result(.5, {"direction": "higher_is_anomaly", "value": 1})["scoreDetail"]["threshold"], "1")

    def test_code_is_required(self):
        with self.assertRaises(TypeError):
            ml_method_result("ml:iforest", "v", "version", .5, self.SPEC, "p")
        for code in (None, ""):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "^ml_result_code_required$"):
                ml_method_result("ml:iforest", "v", "version", .5, self.SPEC, "p", code=code)

    def test_bool_threshold_is_not_a_number(self):
        for value in (True, False):
            with self.subTest(value=value):
                result = self.result(.5, {"direction": "higher_is_anomaly", "value": value})
                self.assertEqual((result["status"], result["failureCode"], result["scoreDetail"]),
                                 ("failed", "invalid_threshold", None))

    def test_extreme_values_stay_strict_json(self):
        high, low = {"direction": "higher_is_anomaly", "value": 1e-308}, {"direction": "lower_is_anomaly", "value": 1e308}
        for score, spec in ((1e308, high), (1e-308, low)):
            with self.subTest(score=score):
                result = self.result(score, spec)
                self.assertEqual(result["status"], "succeeded")
                self.assertIsNone(result["scoreDetail"]["normalized"])
                json.dumps(result, allow_nan=False)
        result = self.result(10 ** 400)
        self.assertEqual((result["status"], result["failureCode"], result["score"], result["scoreDetail"]),
                         ("failed", "invalid_score", None, None))
        json.dumps(result, allow_nan=False)
        result = self.result(.5, {"direction": "higher_is_anomaly", "value": 10 ** 400})
        self.assertEqual((result["status"], result["failureCode"], result["scoreDetail"]),
                         ("failed", "invalid_threshold", None))
        json.dumps(result, allow_nan=False)

    def test_missing_preparation_and_invalid_values(self):
        for args, cause, code in (
                ((.5, {}), "not_configured", None), ((.5, {"direction": "sideways", "value": 1}), "not_configured", None),
                ((.5, self.SPEC, None), "not_configured", None),
                ((.5, {"direction": "higher_is_anomaly", "value": "x"}), "failure", "invalid_threshold"),
                ((.5, {"direction": "higher_is_anomaly", "value": "inf"}), "failure", "invalid_threshold"),
                ((float("nan"),), "failure", "invalid_score"), ((None,), "failure", "invalid_score"),
                ((True,), "failure", "invalid_score")):
            with self.subTest(args=args):
                result = self.result(*args)
                self.assertEqual((result["cause"], result["failureCode"], result["anomalous"], result["score"]),
                                 (cause, code, None, None))
                self.assertEqual(result["status"], "failed" if code else "not_ready")


class MethodResultShape(unittest.TestCase):
    def test_invalid_combinations_are_rejected(self):
        for args, kwargs, code in (
                (("ml:iforest", "v", "skipped", None), {"cause": "timeout"}, "invalid_method_status"),
                (("ml:iforest", "v", "succeeded", None), {}, "invalid_method_result"),
                (("ml:iforest", "v", "succeeded", False), {"cause": "timeout"}, "invalid_method_result"),
                (("ml:iforest", "v", "not_ready", False), {"cause": "timeout"}, "invalid_method_result"),
                (("ml:iforest", "v", "not_ready", None), {}, "invalid_method_cause"),
                (("ml:iforest", "v", "not_ready", None), {"cause": "other"}, "invalid_method_cause"),
                (("ml:iforest", "v", "failed", None), {"cause": "failure"}, "invalid_failure_code"),
                (("ml:iforest", "v", "failed", None), {"cause": "failure", "failure_code": "other"}, "invalid_failure_code"),
                (("ml:iforest", "v", "not_ready", None), {"cause": "timeout", "failure_code": "exception"}, "invalid_failure_code"),
                (("ml:iforest", "v", "not_ready", None), {"cause": "failure", "failure_code": "exception"}, "invalid_failure_code"),
                (("ml:iforest", "v", "not_ready", True), {"cause": "pending"}, "invalid_method_result"),
                (("ml:iforest", "v", "failed", None), {"cause": "pending"}, "invalid_method_result")):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaisesRegex(ValueError, f"^{code}$"):
                method_result(*args, **kwargs)

    def test_failure_cause_needs_failed_status(self):
        with self.assertRaisesRegex(ValueError, "^invalid_failure_code$"):
            unresolved_result("ml:iforest", "v", "failure", failure_code="exception")
        failed = unresolved_result("ml:iforest", "v", "failure", status="failed", failure_code="exception")
        self.assertEqual((failed["status"], failed["failureCode"]), ("failed", "exception"))

    def test_identifier_format(self):
        self.assertEqual(method_category("soft:elapsed_ms-3f9a2c1d"), "soft")
        for value in ("sr:r00", "hard:A", "hard:x", "ml:", None, 3, "ml:" + "a" * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                method_category(value)


class EvidenceV4(unittest.TestCase):
    REVISIONS = PinnedRevisions(12, HASH, 0, 3)
    TELEMETRY = {"kind": "cost"}
    OBSERVATION = {"id": "22222222-2222-2222-2222-222222222222", "revisionNo": 1, "inputAsOf": "2026-11-04T00:00:00Z"}

    def evidence(self, methods, active_policy=None, revisions=None, **options):
        return build_evidence(active_policy or policy(), methods, revisions or self.REVISIONS, source_id=SOURCE,
                              telemetry=self.TELEMETRY, observation=self.OBSERVATION,
                              input_manifest_hash="manifest", **options)

    def example_methods(self):
        status, duration = hard(raw(end=END))
        rule = evaluate_soft_rule(soft_rule(field="elapsed_ms", operator="gt", threshold=5000), FIELDS, raw(end=END))
        forest = ml_method_result("ml:iforest", "process-iforest-v1", "<#94 모델 버전 ID>", .53,
                                  {"direction": "higher_is_anomaly", "value": "0.61"}, "train-internal-p99-v1",
                                  code="IF_ANOMALY")
        return [with_elapsed(status, 9.1), with_elapsed(duration, 9.2), with_elapsed(rule, 9.3),
                with_elapsed(forest, 21.7)]

    def test_section_11_2_example(self):
        status_rule = {"ruleKey": "process-status", "version": "process-status-hr-v1", "sourceId": SOURCE,
                       "observedStatus": "S", "result": "no_hit", "severity": None, "reason": None}
        policy_document = {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1},
                           "deadlineMs": 3000}
        expected = {
            "schemaVersion": 4,
            "rules": [status_rule, {"ruleKey": "negative-duration", "version": "negative-duration-v1", "result": "no_hit"},
                      {"ruleKey": "elapsed_ms-3f9a2c1d", "version": 12, "result": "hit"}],
            "models": [{"modelKey": "iforest", "anomalous": False}],
            "checkers": {"hr": {"status": "succeeded", "anomalous": False, "evidence": [status_rule]},
                         "sr": {"status": "skipped", "anomalous": None, "evidence": []},
                         "ml": {"status": "succeeded", "anomalous": False,
                                "evidence": [{"modelKey": "iforest", "anomalous": False}]}},
            "anomalous": True, "requestedMode": "parallel", "actualPaths": ["rule", "ml"],
            "detectionPolicy": policy_document, "detectionPolicyRevision": 0, "activeModelSetRevision": 3,
            "ruleSeverities": ["warning"], "ruleSetRevision": 12, "ruleSetHash": HASH,
            "methodResults": [
                {"methodId": "hard:process-status", "category": "hard", "methodVersion": "process-status-hr-v1",
                 "status": "succeeded", "anomalous": False, "code": None, "severity": None,
                 "riskLevel": None, "riskLevelReason": None, "cause": None, "failureCode": None, "elapsedMs": 9.1,
                 "evidence": [{"observedStatus": "S", "result": "no_hit"}]},
                {"methodId": "hard:negative-duration", "category": "hard", "methodVersion": "negative-duration-v1",
                 "status": "succeeded", "anomalous": False, "code": None, "severity": None,
                 "riskLevel": None, "riskLevelReason": None, "cause": None, "failureCode": None, "elapsedMs": 9.2,
                 "evidence": [{"result": "no_hit"}]},
                {"methodId": "soft:elapsed_ms-3f9a2c1d", "category": "soft", "methodVersion": "soft-threshold-v1",
                 "status": "succeeded", "anomalous": True, "code": "SOFT_ELAPSED_MS_3F9A2C1D", "severity": "warning",
                 "riskLevel": "warning", "riskLevelReason": None, "cause": None, "failureCode": None, "elapsedMs": 9.3,
                 "condition": {"field": "elapsed_ms", "operator": "gt", "threshold": "5000", "unit": "ms"},
                 "observedValue": "8120", "evidence": []},
                {"methodId": "ml:iforest", "category": "ml", "methodVersion": "process-iforest-v1",
                 "modelVersionId": "<#94 모델 버전 ID>", "status": "succeeded", "anomalous": False, "code": None,
                 "severity": None, "riskLevel": None, "riskLevelReason": None, "cause": None, "failureCode": None,
                 "elapsedMs": 21.7, "score": .53,
                 "scoreDetail": {"raw": .53, "threshold": "0.61", "direction": "higher_is_anomaly",
                                 "thresholdPolicy": "train-internal-p99-v1",
                                 "normalized": {"kind": "threshold_ratio", "value": .869}},
                 "evidence": []}],
            "categoryResults": {
                "hard": {"n": 2, "k": 1, "result": "normal", "status": "succeeded", "anomalous": False},
                "soft": {"n": 1, "k": 1, "result": "anomalous", "status": "succeeded", "anomalous": True},
                "ml": {"n": 1, "k": 1, "result": "normal", "status": "succeeded", "anomalous": False}},
            "composite": {
                "policy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}},
                "status": "complete", "anomalous": True, "grade": "anomaly_sign",
                "basisMethods": ["soft:elapsed_ms-3f9a2c1d"], "firstAnomalyElapsedMs": 9.3,
                "risk": {"policy": "risk-max-v1", "levelTable": "method-defined-v2", "status": "rated",
                         "level": "warning", "determinedBy": ["soft:elapsed_ms-3f9a2c1d"], "unratedMethods": []}},
            "outcomes": {"classificationVersion": "detector-outcomes-v1"},
            "telemetry": self.TELEMETRY,
            "statusRulePolicy": {"version": "process-status-hr-v1", "sourceId": SOURCE,
                                 "mapping": {"S": "no_hit", "F": "critical", "P": "warning", "N": "warning"}},
            "observation": self.OBSERVATION, "inputManifestHash": "manifest"}
        actual = self.evidence(self.example_methods())
        ratio = actual["methodResults"][3]["scoreDetail"]["normalized"]
        self.assertAlmostEqual(ratio["value"], .869, places=3)
        ratio["value"] = .869
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), list(expected))

    def test_serializes_as_strict_json(self):
        methods = self.example_methods() + stamped([soft(raw(end=None), field="elapsed_ms", suffix="00000001")])
        json.dumps(self.evidence(methods), allow_nan=False)

    def test_composite_policy_is_detection_policy_without_deadline(self):
        methods = stamped(hard(raw("F", start=END, end=START)))
        for active in (policy(), policy(deadline=100), policy(hard=2, soft=7, ml=9, deadline=30000)):
            evidence = self.evidence(methods, active)
            self.assertEqual(evidence["detectionPolicy"], active.document())
            payload = build_interim_payload(active, methods, self.REVISIONS, 9.4)
            for document in (evidence, payload):
                self.assertEqual(document["composite"]["policy"],
                                 {key: value for key, value in document["detectionPolicy"].items() if key != "deadlineMs"})

    def test_status_rule_off(self):
        evidence = self.evidence(stamped(hard(raw(), (NEGATIVE_DURATION,))))
        self.assertEqual(evidence["checkers"]["hr"], {"status": "skipped", "anomalous": None, "evidence": []})
        self.assertIsNone(evidence["statusRulePolicy"])
        self.assertEqual([item["ruleKey"] for item in evidence["rules"]], ["negative-duration"])

    def test_status_rule_keeps_v3_entry_and_reason(self):
        evidence = self.evidence(stamped(hard({"status": "H"}, (PROCESS_STATUS,))))
        self.assertEqual(evidence["rules"], [{"ruleKey": "process-status", "version": "process-status-hr-v1",
                                              "sourceId": SOURCE, "observedStatus": "H", "result": "unavailable",
                                              "severity": None, "reason": "status_unknown"}])
        self.assertEqual(evidence["checkers"]["hr"]["evidence"], evidence["rules"])
        self.assertEqual(evidence["checkers"]["hr"]["status"], "not_ready")
        hit = self.evidence(stamped(hard({"status": "F"}, (PROCESS_STATUS,))))["rules"][0]
        self.assertEqual((hit["result"], hit["severity"], hit["reason"]), ("hit", "critical", None))

    def test_status_rule_timeout_keeps_v3_entry(self):
        late = unresolved_result(PROCESS_STATUS, "process-status-hr-v1", "timeout")
        evidence = self.evidence(stamped([late]))
        self.assertEqual((evidence["rules"][0]["result"], evidence["rules"][0]["reason"]), ("unavailable", "status_unknown"))
        self.assertEqual(evidence["checkers"]["hr"]["status"], "not_ready")

    def test_reason_scope_of_other_rules(self):
        methods = hard({"status": "S"}) + [soft({}, field="elapsed_ms", suffix="00000001"),
                                           soft({"retry_count": 9}, suffix="00000002"),
                                           soft({}, suffix="00000003"), soft({"retry_count": "x"}, suffix="00000004")]
        evidence = self.evidence(stamped(methods))
        self.assertEqual(evidence["rules"][1:], [
            {"ruleKey": "negative-duration", "version": "negative-duration-v1", "result": "not_applicable",
             "reason": "end_time_absent"},
            {"ruleKey": "elapsed_ms-00000001", "version": 12, "result": "not_applicable", "reason": "end_time_absent"},
            {"ruleKey": "retry_count-00000002", "version": 12, "result": "hit"},
            {"ruleKey": "retry_count-00000003", "version": 12, "result": "unavailable"},
            {"ruleKey": "retry_count-00000004", "version": 12, "result": "unavailable"}])
        self.assertEqual(evidence["methodResults"][1]["evidence"],
                         [{"result": "not_applicable", "reason": "end_time_absent"}])
        self.assertEqual(evidence["methodResults"][2]["evidence"],
                         [{"result": "not_applicable", "reason": "end_time_absent"}])

    def test_rule_severities_and_paths(self):
        evidence = self.evidence(stamped(hard(raw("P", start=END, end=START)) + [soft({"retry_count": 5}, severity="info"),
                                                                                  timed_out_model()]))
        self.assertEqual(evidence["ruleSeverities"], ["warning", "critical", "info"])
        self.assertEqual(evidence["actualPaths"], ["rule"])
        self.assertEqual(evidence["models"], [{"modelKey": "iforest", "anomalous": None}])
        self.assertEqual(evidence["checkers"]["ml"], {"status": "not_ready", "anomalous": None, "evidence": evidence["models"]})

    def test_ml_only_and_no_ml_checkers(self):
        evidence = self.evidence(stamped([model("iforest", True)]))
        self.assertEqual(evidence["rules"], [])
        self.assertEqual(evidence["actualPaths"], ["ml"])
        self.assertEqual(evidence["requestedMode"], "ml_only")
        evidence = self.evidence(stamped(hard(raw())))
        self.assertEqual(evidence["checkers"]["ml"], {"status": "skipped", "anomalous": None, "evidence": []})
        self.assertEqual(evidence["requestedMode"], "rule_only")

    def test_optional_keys_are_carried_only_when_given(self):
        evidence = self.evidence(stamped(hard(raw())))
        self.assertNotIn("demo", evidence)
        self.assertNotIn("input", evidence)
        evidence = self.evidence(stamped(hard(raw())), demo={"historicalReplay": True}, input_origin={"mode": "csv"})
        self.assertEqual((evidence["demo"], evidence["input"]), ({"historicalReplay": True}, {"mode": "csv"}))

    def test_columns_read_from_evidence(self):
        evidence = self.evidence(self.example_methods())
        self.assertEqual(decision_columns(evidence), {"status": "complete", "rule_status": "succeeded",
                                                      "ml_status": "succeeded", "decision_code": "SOFT_ELAPSED_MS_3F9A2C1D",
                                                      "anomaly_score": .53})

    def test_final_rejects_pending_and_bad_identity(self):
        pending = unresolved_result("ml:iforest", "v", "pending")
        with self.assertRaisesRegex(ValueError, "^final_decision_has_pending_method$"):
            self.evidence(stamped(hard(raw())) + [pending])
        for values in ((1, "sha256:" + HASH, 0, 3), (1, HASH.upper(), 0, 3), (1, "a" * 63, 0, 3), (1, HASH + "\n", 0, 3),
                       (-1, HASH, 0, 3),
                       (True, HASH, 0, 3), (1, HASH, "0", 3), (1, HASH, 0, None)):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "^invalid_pinned_revisions$"):
                PinnedRevisions(*values)

    def test_inputs_are_not_mutated(self):
        methods = self.example_methods()
        before = copy.deepcopy(methods)
        self.evidence(methods)
        self.assertEqual(methods, before)


class InterimPayload(unittest.TestCase):
    REVISIONS = EvidenceV4.REVISIONS

    def test_section_13_3_snapshot(self):
        status, duration = hard(raw("F"))
        methods = [with_elapsed(status, 9.1), unresolved_result(NEGATIVE_DURATION, "negative-duration-v1", "pending"),
                   unresolved_result("ml:iforest", "process-iforest-v1", "pending", modelVersionId="version",
                                     score=None, scoreDetail=None)]
        payload = build_interim_payload(policy(), methods, self.REVISIONS, 9.4)
        self.assertEqual(INTERIM_EVIDENCE_VERSION, "detector-evidence-v4")
        self.assertEqual(sorted(payload), sorted(["schemaVersion", "ruleSetRevision", "ruleSetHash", "detectionPolicy",
                                                  "detectionPolicyRevision", "activeModelSetRevision", "methodResults",
                                                  "categoryResults", "composite", "elapsedMs"]))
        composite = payload["composite"]
        self.assertEqual((composite["status"], composite["anomalous"], composite["grade"], composite["basisMethods"],
                          composite["firstAnomalyElapsedMs"]), ("partial", True, "anomaly", [PROCESS_STATUS], 9.1))
        self.assertEqual((payload["schemaVersion"], payload["ruleSetRevision"], payload["elapsedMs"]), (4, 12, 9.4))
        self.assertEqual(payload["categoryResults"]["hard"]["n"], 2)
        pending = payload["methodResults"][1]
        self.assertEqual((pending["status"], pending["cause"], pending["elapsedMs"]), ("not_ready", "pending", None))
        self.assertNotIn("rules", payload)
        json.dumps(payload, allow_nan=False)

    def test_requires_an_anomaly_and_a_time(self):
        with self.assertRaisesRegex(ValueError, "^interim_requires_anomaly$"):
            build_interim_payload(policy(), stamped(hard(raw())), self.REVISIONS, 9.4)
        for elapsed in (None, True, float("nan")):
            with self.subTest(elapsed=elapsed), self.assertRaisesRegex(ValueError, "^interim_elapsed_required$"):
                build_interim_payload(policy(), stamped(hard(raw("F"))), self.REVISIONS, elapsed)

    def test_arrivals_only_raise_grade_and_risk(self):
        forest = with_elapsed(model("iforest", True), 5.0)
        rule = with_elapsed(soft({"retry_count": 5}, severity="info"), 6.0)
        status = with_elapsed(evaluate_process_status({"status": "F"}, SOURCE), 7.0)
        pending_hard = unresolved_result(PROCESS_STATUS, "process-status-hr-v1", "pending")
        pending_soft = unresolved_result(rule["methodId"], "soft-threshold-v1", "pending")
        steps = [[pending_hard, pending_soft, forest], [pending_hard, rule, forest], [status, rule, forest]]
        composites = [build_interim_payload(policy(), step, self.REVISIONS, 9.0)["composite"] for step in steps]
        self.assertEqual([item["grade"] for item in composites], ["anomaly_sign", "anomaly_sign", "anomaly"])
        self.assertEqual([item["risk"]["status"] for item in composites], ["unrated", "rated", "rated"])
        self.assertEqual([item["risk"]["level"] for item in composites], [None, "info", "critical"])
        self.assertEqual([item["firstAnomalyElapsedMs"] for item in composites], [5.0, 5.0, 5.0])
        self.assertEqual([len(item["basisMethods"]) for item in composites], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
