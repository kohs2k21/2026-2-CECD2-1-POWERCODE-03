"""`recommend_thresholds` 워커 경로 시험. 생성은 전부 fixture이고 검증기는 저장소의 실제 rule_policy를 쓴다."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generation_client
import model_adapters
import threshold_recommendation as recommendation
from generation_client import GenerationRejected, GenerationUnavailable, generate
from explanation import explain
from generation_service import DesignModel, GenerationGate, serve, stopped_by_time
from methods.registry import SOFT_RULE_FIELDS_BY_ID
from pg_queue import ClaimedMessage
from research_bundle import policy, sha
from research_design import digest, recommend_rules, rule_request
from threshold_values import (ThresholdSerializationError, display_threshold, exact_text, fixed_threshold,
                              threshold_valid)
from trainer import runner

REPOSITORY = Path(__file__).resolve().parents[3]
ADAPTER_SHA = "a" * 64
HASH = "b" * 64
SNAPSHOT = uuid.UUID("11111111-1111-4111-8111-111111111111")
EXCLUSION = "status=S, explicit anomaly labels excluded; unlabeled retained"
DECIMAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")
LLM_SPEC = {"evidence_version": "soft-rule-threshold-v1", "input_evidence_hash": "h", "model_version": "rule_v2",
            "prompt_version": "p", "output_schema_version": "threshold-recommendation-v1", "reuse_key": "r"}


def make_bundle(root: Path) -> Path:
    """연구 번들 중 검증기와 어댑터 hash만 담은 시험용 번들. 가중치는 쓰지 않는다."""
    source = REPOSITORY / "research/models/feature_rule_design_v0_2/implementation"
    target = root / "design/policy"
    target.mkdir(parents=True)
    for name in ("rule_policy.py", "v1_validator.py", "pilot_io.py"):
        shutil.copy2(source / name, target / name)
    (root / "design/manifest.json").write_text(json.dumps(
        {"adapters": {"rule_v2": {"sha256": ADAPTER_SHA}, "feature_v5": {"sha256": ADAPTER_SHA}}}))
    assets = {path.relative_to(root).as_posix(): sha(path) for path in root.rglob("*") if path.is_file()}
    (root / "bundle.json").write_text(json.dumps({"schemaVersion": 1, "assets": assets}))
    return root


class Fixture:
    """필드별로 응답을 바꿀 수 있는 생성 fixture. 기본은 검증기가 기대하는 출력이다."""

    def __init__(self, bundle: Path):
        self.validator = policy(bundle)
        self.calls: list[str] = []
        self.report_timeout: list[bool] = []
        self.overrides: dict[str, object] = {}
        self.on_call = None

    def generate(self, kind, request, report_timeout=False):
        field = request["feature"]["id"]
        self.calls.append(field)
        self.report_timeout.append(report_timeout)
        if self.on_call:
            self.on_call()
        expected = self.validator.expected_target(request)
        override = self.overrides.get(field)
        if isinstance(override, BaseException):
            raise override
        if callable(override):
            return override(expected)
        return {"output": expected, "truncated": False, "adapterSha": ADAPTER_SHA}


def receipt(output, truncated=False):
    return {"output": output, "truncated": truncated, "adapterSha": ADAPTER_SHA}


def raw(**values):
    return {"status": "S", **values}


def member(values, label=None):
    return (uuid.uuid4(), values, "train", {"label": label} if label else None)


def timed(index, milliseconds):
    start = datetime(2026, 10, 1) + timedelta(minutes=index)
    return {"start_time_raw": start.isoformat(), "end_time_raw": (start + timedelta(milliseconds=milliseconds)).isoformat()}


def sample_rows(count=40):
    rows = []
    for index in range(count):
        rows.append(member(raw(**timed(index, 100 + index), total_count=str(100000 + index * 10), error_count="1" if index >= 30 else "0",
                               retry_count="0", success_count=str(100000 + index))))
    return rows


def rule(field, threshold=5, suffix="00000000"):
    return {"id": f"soft:{field}-{suffix}", "name": f"{field} rule", "field": field, "operator": "gt",
            "threshold": threshold, "severity": "warning"}


def job_spec(*rules, **changes):
    spec = {"kind": "recommend_thresholds", "sourceId": str(uuid.uuid4()), "ruleSetRevision": 13,
            "ruleSetContentHash": HASH, "ruleSetContentHashVersion": 2, "scope": {"softRuleIds": None},
            "data": {"snapshotId": None, "from": "2026-08-01T00:00:00Z", "until": "2026-11-01T00:00:00Z",
                     "timeBasis": "persisted_at"},
            "mode": "data_p95_with_llm_rationale", "targets": list(rules)}
    spec.update(changes)
    return spec


def frontend_valid(item):
    """PR #120 ruleAiApi.ts validItem과 같은 조건."""
    evidence = item["evidence"]
    finite = lambda value: value is None or isinstance(value, (int, float)) and not isinstance(value, bool)
    if not isinstance(item["field"], str) or not isinstance(item["unit"], str) or \
            not all(isinstance(value, str) for value in item["softRuleIds"]) or \
            item["status"] not in recommendation.STATUSES or item["source"] not in ("data_p95", "ai_proposal") or \
            not (item["rationale"] is None or isinstance(item["rationale"], str)) or \
            not isinstance(item.get("rejectedReason", ""), str):
        return False
    if not isinstance(evidence["n"], int) or not finite(evidence["min"]) or not finite(evidence["max"]) or not finite(evidence["p95"]):
        return False
    if item["status"] == "proposed":
        return item["operator"] in ("gte", "gt", "lte", "lt") and bool(DECIMAL.fullmatch(item["threshold"])) and \
            bool(DECIMAL.fullmatch(item["displayThreshold"]))
    return item["operator"] is None and item["threshold"] is None and item["displayThreshold"] is None


class ThresholdValueTests(unittest.TestCase):
    def test_webui_examples_survive_number_round_trip(self):
        for value, expected in ((956.4999999999995, "956.5"), (0.3333333333333333, "0.333333333333333")):
            text = fixed_threshold(value)
            self.assertEqual(text, expected)
            self.assertTrue(threshold_valid(text))
            self.assertEqual(fixed_threshold(float(text)), text)
            self.assertEqual(exact_text(float(text)), text)

    def test_small_and_large_values_never_use_exponent(self):
        for value, expected in ((3.125e-05, "0.00003125"), (1e-05, "0.00001"), (0.0100, "0.01"), (8000.0, "8000"),
                                (1e30, "1000000000000000000000000000000"), (-0.0, "0"), (-2.5, "-2.5")):
            self.assertEqual(fixed_threshold(value), expected)
            self.assertNotIn("e", fixed_threshold(value).lower())

    def test_negative_zero_is_never_written(self):
        self.assertEqual(exact_text(-0.0), "0")
        self.assertEqual(fixed_threshold(-0.0), "0")
        self.assertEqual(fixed_threshold(-0.4, "integer"), "0")
        self.assertEqual(display_threshold(-0.0), "0")
        self.assertEqual(display_threshold(-0.4), "0")
        self.assertEqual(display_threshold(-0.0, 4, "significant"), "0")
        self.assertFalse(threshold_valid("-0"))

    def test_shared_examples_are_what_the_worker_writes(self):
        """백엔드 시험은 같은 파일의 값을 룰 세트 저장 검증에 넣는다(tests/rule-set-contract.test.mjs, jobs.integration.mjs)."""
        path = REPOSITORY / "server/contracts/threshold-values.example.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        self.assertEqual({case["field"] for case in cases}, {"elapsed_ms", "error_ratio", "retry_count"})
        for case in cases:
            field = SOFT_RULE_FIELDS_BY_ID[case["field"]]
            options = (case["p95"], field.value_type, field.display_precision, field.display_precision_mode)
            with self.subTest(case["name"]):
                if case["threshold"] is None:
                    with self.assertRaises(ThresholdSerializationError):
                        fixed_threshold(*options)
                else:
                    self.assertEqual(fixed_threshold(*options), case["threshold"])

    def test_fifteen_digit_rounding_is_half_even(self):
        self.assertEqual(fixed_threshold("1.000000000000005"), "1")
        self.assertEqual(fixed_threshold("1.000000000000015"), "1.00000000000002")

    def test_integer_field_gets_integer_text(self):
        self.assertEqual(fixed_threshold(12.0, "integer"), "12")
        self.assertEqual(fixed_threshold(12.5, "integer"), "13")
        self.assertEqual(fixed_threshold(13.5, "integer"), "14")

    def test_length_retry_then_failure(self):
        self.assertEqual(fixed_threshold(1.25e-35, "number", 4, "significant"), "0.0000000000000000000000000000000000125")
        for value, precision, mode in ((1.5e-39, 0, "fixed"), (1.23456789e-39, 4, "significant")):
            with self.assertRaises(ThresholdSerializationError):
                fixed_threshold(value, "number", precision, mode)
        with self.assertRaises(ThresholdSerializationError):
            fixed_threshold(1e45)

    def test_non_numbers_fail_with_contract_code(self):
        for value in (None, True, float("nan"), float("inf"), "abc"):
            with self.assertRaisesRegex(ValueError, "^threshold_serialization_failed$"):
                fixed_threshold(value)

    def test_saved_value_rule(self):
        for text, value_type, expected in (("12.5", "number", True), ("12.5", "integer", False), ("1e-5", "number", False),
                                           ("01", "number", False), ("1234567890123456", "number", False),
                                           ("0." + "0" * 38 + "1", "number", False), ("-3", "integer", True)):
            self.assertEqual(threshold_valid(text, value_type), expected, text)

    def test_display_follows_catalog_precision(self):
        self.assertEqual(display_threshold(7210.5, 0, "fixed"), "7211")
        self.assertEqual(display_threshold(3.125e-05, 4, "significant"), "0.00003125")
        self.assertEqual(display_threshold(0.12501, 4, "significant"), "0.125")
        self.assertEqual(display_threshold(0.0, 4, "significant"), "0")

    def test_value_exact_format_is_chosen_by_the_caller(self):
        rows = [{"features": {"ratio": value}, "label": None} for value in [0.00003125] * 30]
        definition = {"name": "ratio", "expression": {"op": "derived", "ast": {"op": "literal", "value": 1},
                                                      "outputType": "number", "unit": "ratio"}}
        self.assertEqual(rule_request(rows, definition, "set")["baseline"]["value_exact"], "3.125e-05")
        self.assertEqual(rule_request(rows, definition, "set", exact=exact_text)["baseline"]["value_exact"], "0.00003125")


class ReferenceSampleTests(unittest.TestCase):
    field = SOFT_RULE_FIELDS_BY_ID["elapsed_ms"]

    def test_sample_keeps_success_unlabeled_and_normal_rows_only(self):
        rows = [member(raw(**timed(0, 10))), member(raw(**timed(1, 20)), "normal"),
                member(raw(**timed(2, 30)), "anomaly"), member({"status": "F", **timed(3, 40)}),
                member({**timed(4, 50)})]
        sample, _ = recommendation.reference_sample(rows, [self.field])
        self.assertEqual([float(row["features"]["elapsed_ms"]) for row in sample], [10, 20])

    def test_absent_end_time_is_not_applicable(self):
        for value in (None, "", "   ", "\\N", " \\N "):
            start = "2026-10-01T00:00:00"
            sample, excluded = recommendation.reference_sample(
                [member(raw(start_time_raw=start, end_time_raw=value))], [self.field])
            self.assertIsNone(sample[0]["features"]["elapsed_ms"])
            self.assertEqual(excluded["elapsed_ms"], {"not_applicable": 1, "unavailable": 0}, repr(value))

    def test_absent_start_and_unparsable_time_are_unavailable(self):
        end = "2026-10-01T00:00:01"
        for values in ({"start_time_raw": "", "end_time_raw": end}, {"start_time_raw": "\\N", "end_time_raw": end},
                       {"start_time_raw": "not-a-time", "end_time_raw": end}):
            _, excluded = recommendation.reference_sample([member(raw(**values))], [self.field])
            self.assertEqual(excluded["elapsed_ms"], {"not_applicable": 0, "unavailable": 1}, values)

    def test_both_times_absent_prefers_end_time_policy(self):
        _, excluded = recommendation.reference_sample([member(raw(start_time_raw="", end_time_raw=" "))], [self.field])
        self.assertEqual(excluded["elapsed_ms"], {"not_applicable": 1, "unavailable": 0})

    def test_zero_total_and_absent_count_are_unavailable(self):
        field = SOFT_RULE_FIELDS_BY_ID["error_ratio"]
        rows = [member(raw(error_count="1", total_count="0")), member(raw(error_count="\\N", total_count="5"))]
        _, excluded = recommendation.reference_sample(rows, [field])
        self.assertEqual(excluded["error_ratio"], {"not_applicable": 0, "unavailable": 2})


class ParseJobTests(unittest.TestCase):
    def test_missing_mode_uses_contract_default(self):
        spec = job_spec(rule("elapsed_ms"))
        del spec["mode"]
        self.assertEqual(recommendation.parse_job(spec, LLM_SPEC).mode, "data_p95_with_llm_rationale")
        self.assertEqual(recommendation.parse_job({**spec, "mode": None}, LLM_SPEC).mode, "data_p95_with_llm_rationale")

    def test_ai_proposal_is_reserved(self):
        with self.assertRaisesRegex(ValueError, "^recommendation_mode_not_available$"):
            recommendation.parse_job(job_spec(rule("elapsed_ms"), mode="ai_proposal"), LLM_SPEC)

    def test_mode_and_llm_spec_are_checked(self):
        with self.assertRaisesRegex(ValueError, "^recommendation_mode_invalid$"):
            recommendation.parse_job(job_spec(rule("elapsed_ms"), mode="other"), LLM_SPEC)
        for key, value in (("evidence_version", "source-period-request-v1"), ("output_schema_version", "x"), ("model_version", "x")):
            with self.assertRaisesRegex(ValueError, "^recommendation_llm_spec_invalid$"):
                recommendation.parse_job(job_spec(rule("elapsed_ms")), {**LLM_SPEC, key: value})

    def test_targets_group_by_field_in_id_order(self):
        first, second = rule("elapsed_ms", 9000, "bbbbbbbb"), rule("elapsed_ms", 5000, "aaaaaaaa")
        plan = recommendation.parse_job(job_spec(first, second, rule("total_count")), LLM_SPEC)
        self.assertEqual([item["id"] for item in plan.targets["elapsed_ms"]], [second["id"], first["id"]])
        self.assertEqual(plan.rule_set, {"revision": 13, "contentHash": HASH, "contentHashVersion": 2})

    def test_scope_limits_targets(self):
        keep = rule("elapsed_ms")
        spec = job_spec(keep, rule("total_count"), scope={"softRuleIds": [keep["id"]]})
        self.assertEqual(list(recommendation.parse_job(spec, LLM_SPEC).targets), ["elapsed_ms"])
        with self.assertRaisesRegex(ValueError, "^soft_rules_empty$"):
            recommendation.parse_job(job_spec(keep, scope={"softRuleIds": ["soft:other-00000000"]}), LLM_SPEC)

    def test_invalid_targets(self):
        with self.assertRaisesRegex(ValueError, "^soft_rules_empty$"):
            recommendation.parse_job(job_spec(), LLM_SPEC)
        with self.assertRaisesRegex(ValueError, "^soft_rule_field_unknown:nope$"):
            recommendation.parse_job(job_spec(rule("nope")), LLM_SPEC)
        with self.assertRaisesRegex(ValueError, "^invalid_recommendation_targets$"):
            recommendation.parse_job(job_spec(rule("elapsed_ms"), rule("elapsed_ms")), LLM_SPEC)
        with self.assertRaisesRegex(ValueError, "^soft_rule_limit_exceeded$"):
            recommendation.parse_job(job_spec(*[rule("elapsed_ms", 1, f"{index:08x}") for index in range(33)]), LLM_SPEC)

    def test_rule_set_reference_is_required(self):
        for changes in ({"ruleSetContentHash": "9c40"}, {"ruleSetContentHashVersion": 3}, {"ruleSetRevision": True}):
            with self.assertRaisesRegex(ValueError, "^invalid_rule_set_reference$"):
                recommendation.parse_job(job_spec(rule("elapsed_ms"), **changes), LLM_SPEC)

    def test_period_requires_persisted_time_basis(self):
        data = {"snapshotId": None, "from": "a", "until": "b", "timeBasis": "esb_occurred_at"}
        with self.assertRaisesRegex(ValueError, "^recommendation_time_basis_unsupported$"):
            recommendation.parse_job(job_spec(rule("elapsed_ms"), data=data), LLM_SPEC)
        recommendation.parse_job(job_spec(rule("elapsed_ms"), data={**data, "snapshotId": str(SNAPSHOT)}), LLM_SPEC)
        with self.assertRaisesRegex(ValueError, "^recommendation_data_range_missing$"):
            recommendation.parse_job(job_spec(rule("elapsed_ms"), data={"snapshotId": None, "from": None, "until": None}), LLM_SPEC)


class ApiExampleTests(unittest.TestCase):
    """API 시험이 그대로 저장되는지 확인하는 예시를 워커가 받는다(tests/threshold-recommendation-contract.test.mjs)."""

    def test_api_examples_pass_parse_job(self):
        path = REPOSITORY / "server/contracts/llm-recommendation-job.example.json"
        examples = json.loads(path.read_text(encoding="utf-8"))["examples"]
        self.assertEqual([example["name"] for example in examples], ["period", "snapshot"])
        for example in examples:
            spec, llm_spec = example["input_spec"], example["llm_spec"]
            plan = recommendation.parse_job(spec, llm_spec)
            self.assertEqual(plan.mode, recommendation.MODE_DATA_P95)
            self.assertEqual(plan.rule_set, {"revision": spec["ruleSetRevision"], "contentHash": spec["ruleSetContentHash"],
                                             "contentHashVersion": spec["ruleSetContentHashVersion"]})
            self.assertEqual(sorted(plan.targets), ["elapsed_ms", "error_ratio"])
            self.assertEqual(plan.targets["elapsed_ms"][0]["threshold"], 5000)
            self.assertEqual(set(llm_spec), set(LLM_SPEC))


class BundleCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.bundle = make_bundle(Path(cls.directory.name).resolve())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.model = Fixture(self.bundle)
        generation_client.set_local_model(self.model)
        environment = patch.dict("os.environ", {"RESEARCH_BUNDLE_ROOT": str(self.bundle)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(generation_client.set_local_model, None)

    def run_job(self, *rules, rows=None):
        plan = recommendation.parse_job(job_spec(*rules), LLM_SPEC)
        return model_adapters.recommend_thresholds(plan, sample_rows() if rows is None else rows, SNAPSHOT, "persisted_at")

    def one(self, field="elapsed_ms", rows=None):
        return self.run_job(rule(field), rows=rows)["items"][0]


class RecommendationResultTests(BundleCase):
    def test_result_document(self):
        soft = [rule("elapsed_ms", 5000, "bbbbbbbb"), rule("elapsed_ms", 9000, "aaaaaaaa"), rule("error_ratio", 0.01),
                rule("retry_count", 3), rule("total_count", 10000)]
        self.model.overrides["total_count"] = lambda _: receipt({"state": "proposed"})
        rows = sample_rows() + [member({"status": "F", **timed(90, 999999)}), member(raw(**timed(91, 999999)), "anomaly")]
        result = self.run_job(*soft, rows=rows)
        self.assertEqual(result["recommendationContractVersion"], "threshold-recommendation-v1")
        self.assertEqual(result["mode"], "data_p95_with_llm_rationale")
        self.assertEqual(result["ruleSet"], {"revision": 13, "contentHash": HASH, "contentHashVersion": 2})
        self.assertEqual(result["data"], {"snapshotId": str(SNAPSHOT), "split": "train", "timeBasis": "persisted_at",
                                          "exclusionRule": EXCLUSION, "rowCount": 42})
        self.assertEqual((result["provider"], result["adapterSha"]), ("rule_v2", ADAPTER_SHA))
        self.assertEqual(result["counts"], {"targets": 4, "proposed": 2, "requestEvidence": 0, "deferred": 0,
                                            "noApplicable": 1, "rejected": 1})
        items = {item["field"]: item for item in result["items"]}
        self.assertEqual(list(items), ["elapsed_ms", "error_ratio", "retry_count", "total_count"])
        self.assertTrue(all(frontend_valid(item) for item in result["items"]))
        self.assertFalse(recommendation.all_rejected(result))
        json.dumps(result, allow_nan=False)
        elapsed = items["elapsed_ms"]
        expected = float(np.quantile([100.0 + index for index in range(40)], .95))
        self.assertEqual(elapsed["evidence"]["p95"], expected)
        self.assertEqual((elapsed["evidence"]["n"], elapsed["evidence"]["min"], elapsed["evidence"]["max"]), (40, 100.0, 139.0))
        self.assertEqual(elapsed["threshold"], exact_text(expected))
        self.assertEqual(elapsed["softRuleIds"], ["soft:elapsed_ms-aaaaaaaa", "soft:elapsed_ms-bbbbbbbb"])
        self.assertEqual(elapsed["currentThreshold"], 9000)
        self.assertEqual((elapsed["operator"], elapsed["source"], elapsed["unit"], elapsed["aiProposal"]), ("gt", "data_p95", "ms", None))
        self.assertEqual(elapsed["reviews"], {"schema": True, "facts": True, "expectedTarget": True})
        self.assertTrue(elapsed["evidence"]["measurementId"].startswith("m_") and elapsed["evidence"]["baselineId"].startswith("b_"))
        self.assertEqual(elapsed["evidence"]["exclusionRule"], EXCLUSION)
        self.assertNotIn("severity", elapsed)
        self.assertNotIn("rejectedReason", elapsed)
        self.assertEqual(items["retry_count"]["status"], "no_applicable")
        self.assertEqual(items["total_count"]["rejectedReason"], "invalid_generation_output")
        self.assertIsNone(items["total_count"]["rationale"])

    def test_integer_field_threshold_is_integer(self):
        rows = [member(raw(total_count=str(index))) for index in range(31)]  # p95 = 28.5
        item = self.one("total_count", rows)
        self.assertEqual(item["evidence"]["p95"], 28.5)
        self.assertEqual((item["threshold"], item["displayThreshold"]), ("29", "29"))

    def test_small_ratio_keeps_value_with_original_in_evidence(self):
        rows = [member(raw(error_count="1", total_count=str(32000 + index))) for index in range(30)]
        item = self.one("error_ratio", rows)
        p95 = item["evidence"]["p95"]
        self.assertLess(p95, 1e-4)
        self.assertEqual(item["status"], "proposed")
        self.assertNotIn("e", item["threshold"].lower())
        self.assertEqual(item["threshold"], fixed_threshold(p95, "number", 4, "significant"))
        self.assertTrue(threshold_valid(item["threshold"]))
        self.assertEqual(len(item["displayThreshold"].lstrip("0.")), 4)

    def test_generation_input_keeps_trained_format_and_aggregates_only(self):
        original = self.model.generate
        requests = []
        self.model.generate = lambda kind, request, **options: requests.append((kind, request)) or original(kind, request, **options)
        self.run_job(rule("elapsed_ms", 5000))
        kind, request = requests[0]
        self.assertEqual(kind, "rule")
        self.assertEqual(self.model.report_timeout, [True])
        expected = rule_request([{"features": {}, "label": None}], {"name": "x", "expression": {"op": "field"}}, "set")
        self.assertEqual(set(request), set(expected))
        self.assertEqual(request["baseline"]["selection"], EXCLUSION)
        self.assertNotIn("2026-10-01", json.dumps(request))

    def test_no_applicable_cases_do_not_call_generation(self):
        cases = {"few": sample_rows(19), "none": [member({"status": "F", **timed(0, 1)})],
                 "absent": [member(raw(start_time_raw="2026-10-01T00:00:00", end_time_raw="")) for _ in range(30)],
                 "constant": [member(raw(**timed(index, 50))) for index in range(30)]}
        for name, rows in cases.items():
            with self.subTest(name):
                result = self.run_job(rule("elapsed_ms"), rows=rows)
                item = result["items"][0]
                self.assertEqual(item["status"], "no_applicable")
                self.assertTrue(frontend_valid(item))
                self.assertIsNone(item["evidence"]["baselineId"])
                self.assertTrue(item["rationale"])
                self.assertFalse(recommendation.all_rejected(result))
        self.assertEqual(self.model.calls, [])

    def test_absent_rows_are_named_in_rationale_and_not_counted(self):
        item = self.one(rows=[member(raw(start_time_raw="2026-10-01T00:00:00", end_time_raw="\\N")) for _ in range(30)])
        self.assertEqual(item["evidence"]["n"], 0)
        self.assertIsNone(item["evidence"]["p95"])
        self.assertIn("30 rows are not applicable", item["rationale"])


class RejectionTests(BundleCase):
    def rejected(self, override, reason, reviews=None):
        self.model.overrides["elapsed_ms"] = override
        result = self.run_job(rule("elapsed_ms"))
        item = result["items"][0]
        self.assertEqual((item["status"], item["rejectedReason"]), ("rejected", reason))
        self.assertTrue(frontend_valid(item))
        self.assertIsNone(item["rationale"])
        self.assertEqual(item["evidence"]["n"], 40)
        if reviews:
            self.assertEqual(item["reviews"], reviews)
        return result

    def test_model_number_is_rejected(self):
        def number(expected):
            return receipt({**expected, "condition": {"op": "gt", "value": 7000}})
        self.rejected(number, "expected_target_mismatch", {"schema": True, "facts": True, "expectedTarget": False})

    def test_state_contradicting_verified_baseline_is_rejected(self):
        def deferred(expected):
            return receipt({**expected, "state": "request_evidence", "condition": None})
        self.rejected(deferred, "expected_target_mismatch")

    def test_schema_violation_and_incomplete_outputs(self):
        for override in (lambda _: receipt({"state": "proposed"}), lambda _: receipt(["not", "object"]),
                         lambda expected: receipt(expected, truncated=True),
                         json.JSONDecodeError("bad", "{", 0), ValueError("invalid_generation_response")):
            self.rejected(override, "invalid_generation_output", {"schema": False, "facts": False, "expectedTarget": False})

    def test_factual_review_failure(self):
        def contradiction(expected):
            return receipt({**expected, "reason": "This boolean feature exceeded its reference."})
        self.rejected(contradiction, "factual_review_failed", {"schema": True, "facts": False, "expectedTarget": True})

    def test_rationale_limit(self):
        self.rejected(lambda expected: receipt({**expected, "reason": "x" * 501}), "invalid_generation_output")
        self.model.overrides["elapsed_ms"] = lambda expected: receipt({**expected, "reason": "x" * 500})
        self.assertEqual(self.one()["status"], "proposed")

    def test_field_scoped_call_failures(self):
        self.rejected(ValueError("generation_token_budget_exceeded"), "generation_failed:generation_token_budget_exceeded")
        self.rejected(GenerationRejected(), "generation_failed:generation_contract_rejected")
        self.rejected(GenerationRejected("invalid_generation_output"), "invalid_generation_output")
        self.rejected(ValueError("Free text from a library."), "generation_failed:generation_call_rejected")

    def test_one_bad_field_keeps_the_others(self):
        self.model.overrides["elapsed_ms"] = lambda _: receipt({"state": "proposed"})
        result = self.run_job(rule("elapsed_ms"), rule("total_count"))
        self.assertEqual([item["status"] for item in result["items"]], ["rejected", "proposed"])
        self.assertFalse(recommendation.all_rejected(result))

    def test_adapter_mismatch_fails_the_job(self):
        self.model.overrides["elapsed_ms"] = lambda expected: {**receipt(expected), "adapterSha": "c" * 64}
        with self.assertRaisesRegex(ValueError, "^generation_adapter_version_mismatch$"):
            self.run_job(rule("elapsed_ms"))

    def test_serialization_failure_is_a_data_rejection(self):
        rows = [member(raw(error_count="1", total_count=str(10 ** 39 + index * 10 ** 34))) for index in range(30)]
        result = self.run_job(rule("error_ratio"), rows=rows)
        item = result["items"][0]
        self.assertEqual((item["status"], item["rejectedReason"]), ("rejected", "threshold_serialization_failed"))
        self.assertEqual(item["reviews"], {"schema": True, "facts": True, "expectedTarget": True})
        self.assertTrue(frontend_valid(item))
        self.assertFalse(recommendation.all_rejected(result))

    def test_non_finite_p95_rejects_only_that_field(self):
        rows = [member(raw(**timed(index, 100 + index), total_count="1.7e308" if index >= 28 else "-1.7e308")) for index in range(30)]
        result = self.run_job(rule("total_count"), rule("elapsed_ms"), rows=rows)
        items = {item["field"]: item for item in result["items"]}
        rejected = items["total_count"]
        self.assertEqual((rejected["status"], rejected["rejectedReason"]), ("rejected", "threshold_serialization_failed"))
        self.assertEqual((rejected["evidence"]["n"], rejected["evidence"]["p95"]), (30, None))
        self.assertEqual((rejected["evidence"]["measurementId"], rejected["evidence"]["baselineId"]), (None, None))
        self.assertEqual((rejected["evidence"]["min"], rejected["evidence"]["max"]), (-1.7e308, 1.7e308))
        self.assertTrue(frontend_valid(rejected))
        self.assertEqual(items["elapsed_ms"]["status"], "proposed")
        self.assertEqual(self.model.calls, ["elapsed_ms"])
        self.assertFalse(recommendation.all_rejected(result))
        json.dumps(result, allow_nan=False)

    def test_all_rejected_rule(self):
        reject = lambda reason: {"status": "rejected", "rejectedReason": reason}
        generated = [reject("invalid_generation_output"), reject("expected_target_mismatch"),
                     reject("factual_review_failed"), reject("generation_failed:x")]
        self.assertTrue(recommendation.all_rejected({"items": generated}))
        self.assertFalse(recommendation.all_rejected({"items": [*generated, reject("threshold_serialization_failed")]}))
        self.assertFalse(recommendation.all_rejected({"items": [*generated, {"status": "no_applicable"}]}))
        self.assertFalse(recommendation.all_rejected({"items": [*generated, {"status": "deferred"}]}))


class HttpModeTests(BundleCase):
    def test_cut_json_from_the_service_is_an_item_rejection(self):
        class Cut:
            def generate(self, kind, value, report_timeout=False):
                json.loads('{"state"')
        token = "t" * 32
        server = serve(Cut(), token, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        generation_client.set_local_model(None)
        with patch.dict("os.environ", {"DESIGN_MODEL_URL": f"http://127.0.0.1:{server.server_port}", "DESIGN_MODEL_TOKEN": token}):
            result = self.run_job(rule("elapsed_ms"), rule("total_count"))
        self.assertEqual({item["rejectedReason"] for item in result["items"]}, {"invalid_generation_output"})
        self.assertTrue(recommendation.all_rejected(result))


class LlmStateTests(BundleCase):
    def test_validated_non_proposal_states_keep_closed_fields(self):
        accepting = SimpleNamespace(validate_rule=lambda *_: {"schema_passed": True, "passed": True},
                                    factual_review=lambda *_: {"passed": True})
        for state, count in (("request_evidence", "requestEvidence"), ("deferred", "deferred")):
            self.model.overrides["elapsed_ms"] = lambda _, state=state: receipt({"state": state, "reason": "More evidence is needed."})
            with patch.object(recommendation, "policy", return_value=accepting):
                result = self.run_job(rule("elapsed_ms"))
            item = result["items"][0]
            self.assertEqual((item["status"], item["rationale"]), (state, "More evidence is needed."))
            self.assertTrue(frontend_valid(item))
            self.assertEqual(result["counts"][count], 1)


class GenerationFailureTests(BundleCase):
    def test_timeout_after_first_field_fails_without_partial_result(self):
        self.model.overrides["total_count"] = TimeoutError()
        with self.assertRaisesRegex(model_adapters.ModelNotConfigured, "^generation_timeout$"):
            self.run_job(rule("elapsed_ms"), rule("total_count"))
        self.assertEqual(self.model.calls, ["elapsed_ms", "total_count"])

    def test_service_loss_fails_the_job(self):
        self.model.overrides["elapsed_ms"] = GenerationUnavailable("generation_service_unavailable")
        with self.assertRaisesRegex(model_adapters.ModelNotConfigured, "^generation_service_unavailable$"):
            self.run_job(rule("elapsed_ms"))

    def test_missing_bundle_is_not_configured(self):
        with patch.dict("os.environ", {"RESEARCH_BUNDLE_ROOT": ""}):
            with self.assertRaisesRegex(model_adapters.ModelNotConfigured, "^research_rule_context_not_configured$"):
                self.run_job(rule("elapsed_ms"))

    def test_legacy_rule_value_and_key_are_unchanged(self):
        definition = {"name": "f", "expression": {"op": "derived", "ast": {
            "op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"},
            "outputType": "number", "unit": "count"}}
        rows = [{"features": {"f": float(index)}, "label": None} for index in range(21)]
        self.assertEqual(rule_request(rows, definition, "set")["baseline"]["value_exact"], "19.0")
        suggestion = recommend_rules(rows, "set", [definition])["suggestions"][0]
        self.assertEqual(suggestion["ruleKey"], "sr_bd2a82903a05a7e00bd23723")
        self.assertEqual(suggestion["condition"]["value"], "19.0")
        self.assertEqual(self.model.report_timeout, [False])

    def test_legacy_rule_value_keeps_sixteen_digits(self):
        definition = {"name": "f", "expression": {"op": "derived", "ast": {
            "op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"},
            "outputType": "number", "unit": "count"}}
        rows = [{"features": {"f": 956.4999999999995 + index * .25}, "label": None} for index in range(21)]
        value = rule_request(rows, definition, "set")["baseline"]["value"]
        self.assertGreater(len(str(value).replace(".", "")), 15)
        self.assertEqual(recommend_rules(rows, "set", [definition])["suggestions"][0]["condition"]["value"], str(value))

    def test_legacy_rule_recommendation_only_fixes_exponent_text(self):
        rows = [{"features": {"ratio": 0.00003 + index * 1e-7}, "label": None} for index in range(30)]
        definition = {"name": "ratio", "expression": {"op": "derived", "ast": {"op": "divide", "args": [
            {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "ERROR_COUNT"},
            {"op": "column", "table": "EC_PROCESS_MONITORING", "column": "TOTAL_COUNT"}]},
            "outputType": "number", "unit": "ratio"}}
        request = rule_request(rows, definition, "set")
        suggestion = recommend_rules(rows, "set", [definition])["suggestions"][0]
        self.assertIn("e", request["baseline"]["value_exact"])
        self.assertTrue(DECIMAL.fullmatch(suggestion["condition"]["value"]))
        self.assertEqual(float(suggestion["condition"]["value"]), request["baseline"]["value"])


class GenerationClientTimeoutTests(unittest.TestCase):
    environment = {"DESIGN_MODEL_URL": "http://127.0.0.1:9", "DESIGN_MODEL_TOKEN": "x" * 32}

    def failing(self, error, **options):
        with patch.dict("os.environ", self.environment, clear=True), patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            generate("rule", {}, **options)

    def http_error(self, code):
        return urllib.error.HTTPError("http://127.0.0.1:9/generate", code, "x", {}, None)

    def test_timeouts_are_separate_from_connection_failures(self):
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError()), self.http_error(504)):
            with self.assertRaisesRegex(GenerationUnavailable, "^generation_timeout$"):
                self.failing(error, report_timeout=True)
        for error in (ConnectionRefusedError(), urllib.error.URLError("refused"), self.http_error(503)):
            with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
                self.failing(error, report_timeout=True)

    def test_cut_rejection_body_stays_a_generation_failure(self):
        class Cut(io.BytesIO):
            def read(self, *_):
                raise http.client.IncompleteRead(b"")
        error = urllib.error.HTTPError("http://127.0.0.1:9/generate", 422, "x", {}, Cut())
        with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$") as caught:
            self.failing(error)
        self.assertEqual(caught.exception.code, "generation_contract_rejected")

    def test_rejection_body_without_a_text_code_is_a_contract_rejection(self):
        for body in (b"[]", b"1", b'"code"', b"null", b'{"code": 1}', b'{"code": null}', b'{"code": ["x"]}', b"{}", b"not json"):
            error = urllib.error.HTTPError("http://127.0.0.1:9/generate", 422, "x", {}, io.BytesIO(body))
            with self.subTest(body), self.assertRaises(GenerationRejected) as caught:
                self.failing(error, report_timeout=True)
            self.assertEqual(caught.exception.code, "generation_contract_rejected")

    def test_service_answers_non_object_body_with_422(self):
        class Model:
            def generate(self, kind, value):
                raise AssertionError("not reached")
        token = "t" * 32
        server = serve(Model(), token, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/generate", data=b"[]",
                                         headers={"Authorization": "Bearer " + token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10)
        self.assertEqual(caught.exception.code, 422)
        self.assertEqual(json.loads(caught.exception.read())["code"], "generation_contract_rejected")

    def test_callers_without_the_option_keep_the_previous_code(self):
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError()), self.http_error(504), self.http_error(503)):
            with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
                self.failing(error)

    def test_rejected_call_looks_like_the_previous_failure_to_old_callers(self):
        with self.assertRaisesRegex(GenerationRejected, "^generation_service_unavailable$") as caught:
            self.failing(self.http_error(422))
        self.assertIsInstance(caught.exception, GenerationUnavailable)
        self.assertNotIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.code, "generation_contract_rejected")

    def test_explain_keeps_retryable_classification(self):
        evidence = {"decisionId": "d"}
        text = json.dumps(evidence)
        job = {"input": {"savedEvidenceJson": text, "savedEvidence": evidence, "decisionId": "d"},
               "llmSpec": {"input_evidence_hash": hashlib.sha256(text.encode()).hexdigest()}}
        for error in (TimeoutError(), self.http_error(504), self.http_error(422)):
            with patch.dict("os.environ", self.environment, clear=True), patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
                    explain(job)
        # run()은 RuntimeError를 재시도, ValueError를 종료로 처리한다.
        self.assertTrue(issubclass(GenerationRejected, RuntimeError))

    def test_stopped_by_time_requires_a_cut_output_past_the_limit(self):
        self.assertTrue(stopped_by_time(100, 512, False, 240.1, 240))
        self.assertFalse(stopped_by_time(100, 512, True, 240.1, 240))
        self.assertFalse(stopped_by_time(512, 512, False, 240.1, 240))
        self.assertFalse(stopped_by_time(100, 512, False, 10, 240))

    def test_service_reports_timeout_and_rejection_to_the_client(self):
        class Model:
            def generate(self, kind, value, report_timeout=False):
                if value.get("cut"):
                    json.loads('{"state"')
                raise TimeoutError() if report_timeout else ValueError("bad")
        token = "t" * 32
        server = serve(Model(), token, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        environment = {"DESIGN_MODEL_URL": f"http://127.0.0.1:{server.server_port}", "DESIGN_MODEL_TOKEN": token}
        with patch.dict("os.environ", environment):
            with self.assertRaisesRegex(GenerationUnavailable, "^generation_timeout$"):
                generate("rule", {}, report_timeout=True)
            with self.assertRaises(GenerationRejected) as rejected:
                generate("rule", {})
            self.assertEqual(rejected.exception.code, "generation_contract_rejected")
            with self.assertRaises(GenerationRejected) as cut:
                generate("rule", {"cut": True})
            self.assertEqual(cut.exception.code, "invalid_generation_output")


class DesignModelTimeoutTests(unittest.TestCase):
    """GPU 없이 `DesignModel.generate`의 판정만 돌린다. max_time으로 끊긴 출력을 흉내 낸다."""

    def model(self, root):
        model = DesignModel.__new__(DesignModel)
        (root / "design").mkdir()
        (root / "design/rule_system.txt").write_text("system")
        model.root = root
        model.manifest = {"generation": {"maximum_input_tokens": 100, "rule_output_tokens": 512}}
        model.gate = GenerationGate()
        model.tokenizer = _Tokenizer()
        model.torch = SimpleNamespace(no_grad=nullcontext, manual_seed=lambda _: None,
                                      tensor=lambda value, device: np.array(value), ones_like=np.ones_like)
        model.model = SimpleNamespace(set_adapter=lambda _: None, disable_adapter=nullcontext,
                                      generate=lambda input_ids, **_: np.array([[*input_ids[0], 5, 6, 7]]))
        return model

    def test_only_the_new_path_sees_timeout(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LLM_RECOMMENDATION_SECONDS": "0", "LLM_EXPLANATION_SECONDS": "0"}):
            model = self.model(Path(directory))
            with self.assertRaises(TimeoutError):
                model.generate("rule", {}, report_timeout=True)
            with self.assertRaises(json.JSONDecodeError):
                model.generate("rule", {})
            with self.assertRaises(json.JSONDecodeError):
                model.generate("explain", {})


class _Tokenizer:
    eos_token_id = 0

    def __call__(self, *_args, **_kwargs):
        return {"input_ids": [1, 2]}

    def apply_chat_template(self, *_args, **_kwargs):
        return "text"

    def decode(self, *_args, **_kwargs):
        return '{"state"'


class Cursor:
    rowcount = 1

    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, parameters=()):
        self.connection.statements.append((sql, parameters))
        self.result = next((reply for key, reply in self.connection.replies.items() if key in sql), None)

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result if self.result is not None else []


class Connection:
    def __init__(self, replies):
        self.replies = replies
        self.statements = []
        self.commits = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    @contextmanager
    def transaction(self):
        yield

    def find(self, text):
        return [item for item in self.statements if text in item[0]]


class RunnerTests(BundleCase):
    def setUp(self):
        super().setUp()
        self.message = ClaimedMessage(queue="llm_recommendation", id=uuid.uuid4(), payload={}, attempts=1,
                                      lease_token=uuid.uuid4(), created_at=None)
        for target, value in (("lock_training_ownership", True), ("acknowledge", True)):
            patcher = patch.object(runner, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def connection(self, rows, fixed=True):
        return Connection({"extract(epoch": (0.0,), "FROM run_job_snapshots": (SNAPSHOT,) if fixed else None,
                           "SELECT split::text,count": [("train", len(rows))], "SELECT manifest_hash": ("m",),
                           "data_snapshot_members AS m": rows, "selection_spec->>'timeBasis'": ("persisted_at",)})

    def job(self, spec):
        return {"id": uuid.uuid4(), "kind": "recommend_thresholds", "input": spec, "actor": "admin-1",
                "contractVersion": "v03", "llmSpec": LLM_SPEC}

    def execute(self, connection, job):
        return runner._execute(connection, job, uuid.uuid4(), None, self.message, uuid.uuid4())

    def test_success_result_is_stored_by_finish(self):
        connection = self.connection(sample_rows())
        job = self.job(job_spec(rule("elapsed_ms")))
        commits = []
        self.model.on_call = lambda: commits.append(connection.commits)
        result = self.execute(connection, job)
        self.assertEqual(result["items"][0]["status"], "proposed")
        self.assertGreater(commits[0], 0, "no transaction may stay open during generation")
        runner._finish(connection, self.message, job, uuid.uuid4(), uuid.uuid4(), result, None, True)
        inserted = connection.find("INSERT INTO run_llm_results")
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0][1][2], "recommend_thresholds")
        self.assertEqual(inserted[0][1][5], "soft-rule-threshold-v1")
        self.assertEqual(json.loads(connection.find("status = 'succeeded', result_summary")[0][1][0])["recommendationContractVersion"],
                         "threshold-recommendation-v1")

    def test_all_rejected_fails_with_item_reasons_kept(self):
        connection = self.connection(sample_rows())
        self.model.overrides["elapsed_ms"] = lambda _: receipt({"state": "proposed"})
        result = self.execute(connection, self.job(job_spec(rule("elapsed_ms"))))
        self.assertEqual(result, {"_finalized": True})
        update = connection.find("status = 'failed', error_code = %s, result_summary")[0][1]
        self.assertEqual(update[0], "recommendation_all_rejected")
        self.assertEqual(json.loads(update[1])["items"][0]["rejectedReason"], "invalid_generation_output")
        self.assertEqual(connection.find("INSERT INTO run_llm_results"), [])

    def test_other_failures_store_no_summary(self):
        connection = self.connection([])
        job = self.job(job_spec(rule("elapsed_ms")))
        runner._finish(connection, self.message, job, uuid.uuid4(), uuid.uuid4(), None, "generation_timeout", True)
        update = connection.find("status = 'failed', error_code = %s, result_summary")[0][1]
        self.assertEqual(update[:2], ("generation_timeout", None))

    def test_only_no_applicable_succeeds(self):
        rows = [member(raw(**timed(index, 50))) for index in range(30)]
        result = self.execute(self.connection(rows), self.job(job_spec(rule("elapsed_ms"))))
        self.assertEqual(result["counts"]["noApplicable"], 1)
        self.assertNotIn("_finalized", result)

    def test_reserved_mode_stops_before_any_snapshot(self):
        connection = self.connection(sample_rows(), fixed=False)
        with patch.object(runner, "build_snapshot") as build:
            with self.assertRaisesRegex(ValueError, "^recommendation_mode_not_available$"):
                self.execute(connection, self.job(job_spec(rule("elapsed_ms"), mode="ai_proposal")))
        build.assert_not_called()
        self.assertEqual(self.model.calls, [])
        self.assertEqual(runner._terminal_error_code(ValueError("recommendation_mode_not_available")), "recommendation_mode_not_available")

    def test_empty_train_split_and_short_period_are_input_empty(self):
        with self.assertRaisesRegex(ValueError, "^recommendation_input_empty$"):
            self.execute(self.connection([]), self.job(job_spec(rule("elapsed_ms"))))
        with patch.object(runner, "build_snapshot", side_effect=ValueError("snapshot_insufficient_process_rows")):
            with self.assertRaisesRegex(ValueError, "^recommendation_input_empty$"):
                self.execute(self.connection([], fixed=False), self.job(job_spec(rule("elapsed_ms"))))

    def test_period_builds_snapshot_from_nested_data(self):
        connection = self.connection(sample_rows(), fixed=False)
        spec = job_spec(rule("elapsed_ms"))
        built = SimpleNamespace(id=SNAPSHOT, manifest_hash="m", counts={"train": 40})
        with patch.object(runner, "build_snapshot", return_value=built) as build:
            result = self.execute(connection, self.job(spec))
        self.assertEqual((build.call_args.kwargs["from_time"], build.call_args.kwargs["until_time"]),
                         (spec["data"]["from"], spec["data"]["until"]))
        self.assertEqual(len(connection.find("INSERT INTO run_job_snapshots")), 1)
        self.assertEqual(result["data"]["snapshotId"], str(SNAPSHOT))

    def test_snapshot_id_alone_selects_ready_process_snapshot(self):
        rows = sample_rows()
        connection = self.connection(rows, fixed=False)
        connection.replies["WHERE id=%s AND source_id=%s AND status='ready'"] = (SNAPSHOT,)
        data = {"snapshotId": str(SNAPSHOT), "from": None, "until": None, "timeBasis": "esb_occurred_at"}
        result = self.execute(connection, self.job(job_spec(rule("elapsed_ms"), data=data)))
        self.assertIn("schema_version='process-v1'", connection.find("status='ready'")[0][0])
        self.assertEqual(result["data"]["timeBasis"], "persisted_at")
        connection = self.connection(rows, fixed=False)
        with self.assertRaisesRegex(ValueError, "^snapshot_conditions_mismatch$"):
            self.execute(connection, self.job(job_spec(rule("elapsed_ms"), data=data)))

    def test_claim_requires_admin(self):
        identifier = uuid.uuid4()
        message = ClaimedMessage(queue="llm_recommendation", id=uuid.uuid4(), payload={"schemaVersion": 1, "jobId": str(identifier)},
                                 attempts=1, lease_token=uuid.uuid4(), created_at=None)
        connection = Connection({"FROM run_jobs WHERE id": (identifier, "recommend_thresholds", "queued", {}, "user-1", 0, 3, None, "user", "v03", LLM_SPEC),
                                 "status='leased'": (1,)})
        with self.assertRaisesRegex(ValueError, "^job_role_not_authorized$"):
            runner._claim_job(connection, message, "worker")


if __name__ == "__main__":
    unittest.main()
