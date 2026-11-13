"""평가 세트·모델 버전 비교 v1의 순수 부분 시험(config-comparison-evaluation.md v1 구현 범위). DB 없음."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer import comparison, runner
from trainer.operational_state import DEFAULT_POLICY
from detector.policy import Policy

SOURCE = "11111111-1111-4111-8111-111111111111"
SET = "22222222-2222-4222-8222-222222222222"
ACTIVE = "99999999-9999-4999-8999-999999999999"
CANDIDATE_A = "33333333-3333-4333-8333-333333333333"
CANDIDATE_B = "44444444-4444-4444-8444-444444444444"
HASH = "a" * 64


def spec(**overrides):
    value = {"kind": "compare", "sourceId": SOURCE, "evaluationSetId": SET,
             "configVersionIds": [CANDIDATE_B, ACTIVE, CANDIDATE_A],
             "baselines": [{"modelKey": "iforest", "configVersionId": ACTIVE}],
             "activeModelSetRevision": 4, "ruleSetRevision": 2, "ruleSetContentHash": HASH,
             "detectionPolicyRevision": 0, "detectionPolicy": None}
    value.update(overrides)
    return value


def members(labels):
    return [(str(uuid.UUID(int=index + 1)), str(uuid.UUID(int=1000 + index)), 1, f"c{index}", label)
            for index, label in enumerate(labels)]


class EvaluationSetHashTests(unittest.TestCase):
    def test_generation_counts_and_hash_are_order_independent(self):
        rows = members(["anomaly", "normal", None, "anomaly"])
        generation, digest = comparison.set_generation(rows, snapshot_id="s", purpose="validation")
        again = comparison.set_generation(list(reversed(rows)), snapshot_id="s", purpose="validation")
        self.assertEqual((generation, digest), again)
        self.assertRegex(digest, "^[0-9a-f]{64}$")
        self.assertEqual({key: generation[key] for key in ("rowCount", "originalRowCount", "syntheticRowCount",
                                                           "truthCount", "normalLabelCount", "unlabeledCount")},
                         {"rowCount": 4, "originalRowCount": 4, "syntheticRowCount": 0, "truthCount": 2,
                          "normalLabelCount": 1, "unlabeledCount": 1})

    def test_label_change_moves_content_hash_but_not_member_hash(self):
        rows = members(["anomaly", None])
        first, first_hash = comparison.set_generation(rows, snapshot_id="s", purpose="validation")
        relabeled = [rows[0], (*rows[1][:4], "normal")]
        second, second_hash = comparison.set_generation(relabeled, snapshot_id="s", purpose="validation")
        self.assertNotEqual(first_hash, second_hash)
        self.assertEqual(first["memberHash"], second["memberHash"])
        self.assertNotEqual(first_hash, comparison.set_generation(rows, snapshot_id="s", purpose="test")[1])


class CompareSpecTests(unittest.TestCase):
    def test_active_first_then_sorted_candidates(self):
        plan = comparison.parse_compare_spec(spec())
        self.assertEqual([str(value) for value in plan["order"]], [ACTIVE, CANDIDATE_A, CANDIDATE_B])
        self.assertEqual(str(plan["baseline"]), ACTIVE)
        # 리비전 0의 NULL 정책만 내장 기본값으로 읽는다(운영 뷰 판독과 같음).
        self.assertEqual(plan["detectionPolicy"], DEFAULT_POLICY)
        self.assertEqual(plan["pinned"].deadline_ms, DEFAULT_POLICY["deadlineMs"])

    def test_no_active_baseline_keeps_sorted_order(self):
        plan = comparison.parse_compare_spec(spec(baselines=[], configVersionIds=[CANDIDATE_B, CANDIDATE_A]))
        self.assertIsNone(plan["baseline"])
        self.assertEqual([str(value) for value in plan["order"]], [CANDIDATE_A, CANDIDATE_B])

    def test_rejections(self):
        cases = {"comparison_configs_required": spec(configVersionIds=[], baselines=[]),
                 "comparison_too_many_configs": spec(configVersionIds=[str(uuid.uuid4()) for _ in range(9)], baselines=[]),
                 "comparison_model_key_mixed": spec(baselines=[{"modelKey": "iforest", "configVersionId": ACTIVE},
                                                              {"modelKey": "eif", "configVersionId": CANDIDATE_A}]),
                 "invalid_job_input": spec(baselines=[{"modelKey": "iforest", "configVersionId": str(uuid.uuid4())}])}
        for code, value in cases.items():
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, f"^{code}$"):
                comparison.parse_compare_spec(value)
        for value in (spec(ruleSetContentHash="x"), spec(configVersionIds=[ACTIVE, ACTIVE]),
                      spec(activeModelSetRevision=-1)):
            with self.assertRaisesRegex(ValueError, "^invalid_job_input$"):
                comparison.parse_compare_spec(value)
        with self.assertRaisesRegex(ValueError, "^detection_policy_unavailable$"):
            comparison.parse_compare_spec(spec(detectionPolicyRevision=3, detectionPolicy=None))


def measured_ml(tp=0, fp=0, tn=0, fn=0, anomaly=0, normal=0, succeeded=10, selected=True, population=10,
                predictions=None, scores=None):
    if predictions is None:
        predictions = {str(index): index < tp + fp for index in range(succeeded)}
    return {"selected": selected, "succeededCount": succeeded, "populationCount": population,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn}, "labelCounts": {"anomaly": anomaly, "normal": normal},
            "predictions": predictions, "scores": [float(index) for index in range(succeeded)] if scores is None else scores}


class MlCategoryNullRuleTests(unittest.TestCase):
    def test_without_normal_labels_fp_tn_precision_fpr_are_null_not_zero(self):
        result = comparison.ml_category(measured_ml(tp=3, fn=1, anomaly=4, succeeded=9))
        self.assertEqual(result["confusion"], {"tp": 3, "fp": None, "tn": None, "fn": 1})
        self.assertEqual(result["unavailableCount"], 1)
        complete = result["completeOnly"]
        self.assertEqual(complete["recall"], {"value": .75, "numerator": 3, "denominator": 4, "reason": None})
        for name in ("precision", "falsePositiveRate"):
            self.assertEqual(complete[name], {"value": None, "numerator": None, "denominator": None,
                                              "reason": "verified_normal_labels_unavailable"})

    def test_without_anomaly_labels_recall_and_precision_are_null(self):
        result = comparison.ml_category(measured_ml(fp=1, tn=4, normal=5))
        self.assertEqual(result["confusion"], {"tp": None, "fp": 1, "tn": 4, "fn": None})
        self.assertEqual(result["completeOnly"]["recall"]["reason"], "verified_anomaly_labels_unavailable")
        self.assertEqual(result["completeOnly"]["precision"]["reason"], "verified_anomaly_labels_unavailable")
        self.assertEqual(result["completeOnly"]["falsePositiveRate"]["value"], .2)

    def test_both_labels_and_zero_predictions(self):
        result = comparison.ml_category(measured_ml(tn=4, fn=2, anomaly=2, normal=4))
        self.assertEqual(result["completeOnly"]["precision"],
                         {"value": None, "numerator": 0, "denominator": 0, "reason": "no_predicted_anomalies"})
        self.assertEqual(result["completeOnly"]["recall"]["value"], 0)

    def test_skipped_and_unavailable_paths(self):
        self.assertEqual(comparison.ml_category(measured_ml(selected=False))["measurementStatus"], "skipped")
        missing = comparison.ml_category(measured_ml(succeeded=0, anomaly=2))
        self.assertEqual((missing["measurementStatus"], missing["confusion"], missing["unavailableCount"]),
                         ("unavailable", None, 10))
        self.assertEqual(missing["completeOnly"]["recall"]["reason"], "ml_results_unavailable")

    def test_unlabeled_measures_null_rules(self):
        skipped = comparison.ml_category(measured_ml(selected=False))
        self.assertIsNone(skipped["predictedAnomalyCount"])
        self.assertEqual((skipped["predictedAnomalyRate"]["value"], skipped["predictedAnomalyRate"]["reason"]),
                         (None, "ml_path_not_selected"))
        self.assertEqual(skipped["scoreQuantiles"],
                         {"p50": None, "p95": None, "p99": None, "count": 0, "reason": "ml_path_not_selected"})
        missing = comparison.ml_category(measured_ml(succeeded=0, predictions={}, scores=[]))
        self.assertIsNone(missing["predictedAnomalyCount"])
        self.assertEqual(missing["predictedAnomalyRate"]["reason"], "ml_results_unavailable")
        self.assertEqual(missing["scoreQuantiles"]["reason"], "ml_results_unavailable")
        # ML succeeded 행은 있지만 bool 판정 행이 없으면(방어 분기) 분모 0 → null + no_scored_rows.
        empty = comparison.ml_category(measured_ml(succeeded=3, predictions={}, scores=[]))
        self.assertEqual((empty["predictedAnomalyCount"], empty["predictedAnomalyRate"]["value"],
                          empty["predictedAnomalyRate"]["reason"], empty["scoreQuantiles"]["reason"]),
                         (0, None, "no_scored_rows", "no_scored_rows"))

    def test_unlabeled_measures_values(self):
        result = comparison.ml_category(measured_ml(tp=1, fp=1, succeeded=4, population=5,
                                                    scores=[0.0, 1.0, 2.0, 3.0]))
        self.assertEqual(result["predictedAnomalyCount"], 2)
        self.assertEqual(result["predictedAnomalyRate"], {"value": .5, "unit": "ratio", "numerator": 2, "denominator": 4,
                                                          "coverage": .8, "reason": None})
        # telemetry.percentile: (n-1)·q 위치의 선형 보간.
        quantiles = result["scoreQuantiles"]
        self.assertEqual((quantiles["p50"], quantiles["count"], quantiles["reason"]), (1.5, 4, None))
        self.assertAlmostEqual(quantiles["p95"], 2.85)
        self.assertAlmostEqual(quantiles["p99"], 2.97)


RATIO = {"name": "error_ratio", "expression": {"op": "ratio", "left": {"op": "field", "field": "P.error_count"},
                                               "right": {"op": "field", "field": "P.total_count"}}}


def process_rows(values):
    """(error_count, total_count) → 평가 행. 라벨 없음."""
    return [(uuid.UUID(int=index + 1), {"status": "S", "error_count": str(errors), "total_count": str(total)},
             "validation", None) for index, (errors, total) in enumerate(values)]


def measure(rows, *, score=None, mode="ml_only"):
    """실제 `_measure_version`·`compute`(ML 경로). 산출물 검증·IF 점수만 고정한다: 점수 = error_ratio, 임계 .5."""
    policy = Policy(mode, 30000, "selected_path")
    context = {"policy": policy, "rules": [], "statusRulePolicy": None, "definitions": [RATIO], "featureSetId": "fixture",
               "models": [("fixture", "m", "hash", "f", "hash", {"featureNames": ["error_ratio"]},
                           {"value": .5, "direction": "higher_is_anomaly"}, "iforest")]}
    with patch("detector.computation.model_reference", return_value={}), \
         patch("detector.computation.verified_artifact_path", return_value=Path("fixture")), \
         patch("model_adapters.score_iforest", side_effect=score or (lambda _ref, values: float(values["error_ratio"]))):
        return runner._measure_version(context, None, rows, Path("."), provenance={}, started=time.monotonic(),
                                       queue_wait=None)


class MeasureExclusionTests(unittest.TestCase):
    """모델 입력 미정의 행(TOTAL_COUNT 0 → error_ratio 0 나누기)은 평가 전에 빠지고 따로 센다."""
    values = [(0, 10), (1, 2), (0, 0), (3, 4), (0, 5)]

    def test_zero_total_row_is_excluded_not_unavailable(self):
        rows = process_rows(self.values)
        measured = measure(rows)
        summary = measured["summary"]
        self.assertEqual((summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
                          summary["unlabeledCount"], summary["excludedCount"]), (4, 4, 0, 4, 1))
        self.assertEqual(summary["excludedReasons"], {"model_inputs_undefined": 1})
        self.assertEqual(summary["populationCount"] + summary["excludedCount"], len(rows))
        self.assertEqual(summary["applicability"], "valid")
        self.assertNotIn("selected_paths_unavailable", summary["reasonCodes"])
        self.assertEqual((measured["metrics"]["excludedCount"], measured["metrics"]["excludedReasons"]),
                         (1, {"model_inputs_undefined": 1}))
        # 제외 행은 판정·점수·행 처리 시간·무라벨 비교 분모 어디에도 없다.
        self.assertNotIn(str(rows[2][0]), measured["ml"]["predictions"])
        self.assertEqual(len(measured["ml"]["predictions"]), 4)
        self.assertEqual(measured["executionCost"]["details"]["itemSampleCount"], 4)
        self.assertEqual(measured["executionCost"]["details"]["batchSize"], 4)
        self.assertEqual(measured["comparison"]["metrics"]["hrTotal"]["value"], 4)
        ml = comparison.ml_category({**measured["ml"], "populationCount": summary["populationCount"]})
        self.assertEqual((ml["measurementStatus"], ml["unavailableCount"], ml["predictedAnomalyCount"]), ("measured", 0, 2))
        self.assertEqual(ml["predictedAnomalyRate"]["coverage"], 1.0)
        self.assertEqual(ml["scoreQuantiles"]["count"], 4)
        self.assertEqual(ml["scoreQuantiles"]["p50"], .25)

    def test_runtime_failure_stays_unavailable_and_unmeasured(self):
        def score(_reference, values):
            if values["error_ratio"] == "0.5":
                raise ValueError("invalid_model_score")
            return float(values["error_ratio"])
        summary = measure(process_rows(self.values), score=score)["summary"]
        self.assertEqual((summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
                          summary["excludedCount"]), (4, 3, 1, 1))
        self.assertEqual(summary["applicability"], "unmeasured")
        self.assertIn("selected_paths_unavailable", summary["reasonCodes"])

    def test_arithmetic_error_row_is_not_excluded_but_unscored(self):
        """행 계산이 decimal 예외(Overflow)를 내면 제외 판정에서 새지 않고, compute에서 component_failed 미판정이 된다."""
        rows = process_rows(self.values + [("9E+999999", "1E-999999")])
        measured = measure(rows)  # 예외 없이 끝나야 항목·작업이 계속된다
        summary = measured["summary"]
        self.assertEqual((summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
                          summary["excludedCount"]), (5, 4, 1, 1))
        self.assertEqual(summary["applicability"], "unmeasured")
        self.assertIn("selected_paths_unavailable", summary["reasonCodes"])
        self.assertNotIn(str(rows[-1][0]), measured["ml"]["predictions"])

    def test_all_rows_excluded_is_not_valid(self):
        measured = measure(process_rows([(0, 0), (1, 0)]))
        summary = measured["summary"]
        self.assertEqual((summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
                          summary["excludedCount"]), (0, 0, 0, 2))
        self.assertEqual(summary["applicability"], "unmeasured")
        self.assertIn("all_rows_excluded", summary["reasonCodes"])
        ml = comparison.ml_category({**measured["ml"], "populationCount": 0})
        self.assertEqual((ml["measurementStatus"], ml["unavailableCount"], ml["predictedAnomalyCount"]),
                         ("unavailable", 0, None))

    def test_unseen_fit_group_stays_unscored_but_missing_group_column_is_excluded(self):
        """학습에 없던 그룹 = 모델 적용 범위 밖(미판정 → unmeasured), 그룹 열 자체가 없는 행 = 행 값 결손(제외)."""
        from feature_fit import fit_features, transform
        centered = {"name": "count_centered", "expression": {"op": "group_centered", "groupBy": "P.adapter_type",
                                                             "value": {"op": "field", "field": "P.total_count"}}}
        state = fit_features([centered], [{"raw": {"adapter_type": "A", "total_count": "10"}, "label": None}],
                             snapshot_id="fixture")
        context = {"policy": Policy("ml_only", 30000, "selected_path"), "rules": [], "statusRulePolicy": None,
                   "definitions": [centered], "featureSetId": "fixture",
                   "models": [("fixture", "m", "hash", "f", "hash", {"featureNames": ["count_centered"]},
                               {"value": 5, "direction": "higher_is_anomaly"}, "iforest")]}
        rows = [(uuid.UUID(int=index + 1), raw, "validation", None) for index, raw in enumerate([
            {"adapter_type": "A", "total_count": "12"}, {"adapter_type": "UNSEEN", "total_count": "12"},
            {"total_count": "12"}, {"adapter_type": "A", "total_count": "x"}])]
        with patch("detector.computation.model_reference", return_value={}), \
             patch("detector.computation.verified_artifact_path", return_value=Path("fixture")), \
             patch("model_adapters.transform_iforest_features", side_effect=lambda _ref, definitions, raw: transform(definitions, raw, state)), \
             patch("model_adapters.score_iforest", side_effect=lambda _ref, values: float(values["count_centered"])):
            summary = runner._measure_version(context, None, rows, Path("."), provenance={}, started=time.monotonic(),
                                              queue_wait=None)["summary"]
        # 그룹 열 없음·숫자 아님 2행 제외, UNSEEN 1행은 모집단에 남아 미판정.
        self.assertEqual((summary["populationCount"], summary["scoredCount"], summary["unavailableCount"],
                          summary["excludedCount"]), (2, 1, 1, 2))
        self.assertEqual(summary["applicability"], "unmeasured")
        self.assertIn("selected_paths_unavailable", summary["reasonCodes"])

    def test_rule_only_configuration_excludes_nothing(self):
        summary = measure(process_rows(self.values), mode="rule_only")["summary"]
        self.assertEqual((summary["populationCount"], summary["excludedCount"], summary["excludedReasons"]), (5, 0, {}))


class PairwiseAgreementTests(unittest.TestCase):
    def test_intersection_of_rows_each_item_judged(self):
        # a는 4행을 제외, b는 1행을 제외했다 — 같은 행(2·3)끼리만 센다.
        a = {"1": True, "2": False, "3": True}
        b = {"2": False, "3": False, "4": True}
        [pair] = comparison.pairwise_agreement([("A", a), ("B", b)], 5)
        self.assertEqual({key: pair[key] for key in ("a", "b", "comparableCount", "both", "aOnly", "bOnly", "neither")},
                         {"a": "A", "b": "B", "comparableCount": 2, "both": 0, "aOnly": 1, "bOnly": 0, "neither": 1})
        self.assertEqual(pair["agreementRate"], {"value": .5, "unit": "ratio", "numerator": 1, "denominator": 2,
                                                 "coverage": .4, "reason": None})
        self.assertEqual(pair["interpretation"], "checker_agreement_not_accuracy")

    def test_no_comparable_rows_and_pair_order(self):
        pairs = comparison.pairwise_agreement([("A", {"1": True}), ("B", {"2": True}), ("C", {})], 2)
        self.assertEqual([(pair["a"], pair["b"]) for pair in pairs], [("A", "B"), ("A", "C"), ("B", "C")])
        self.assertEqual(pairs[0]["agreementRate"]["value"], None)
        self.assertEqual(pairs[0]["agreementRate"]["reason"], "no_comparable_rows")
        self.assertEqual(comparison.pairwise_agreement([("A", {"1": True})], 2), [])


class Connection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return self

    def transaction(self):
        return nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, args=None):
        self.statements.append(" ".join(sql.split()))

    def commit(self):
        pass

    def rollback(self):
        pass


def fake_measure(context, operational, rows, root, **_):
    if context == "broken":
        raise ValueError("model_feature_schema_mismatch")
    summary = {"populationCount": 2, "scoredCount": 2, "unavailableCount": 0, "unlabeledCount": 1,
               "excludedCount": 0, "excludedReasons": {},
               "applicability": "valid", "reasonCodes": ["labels_unavailable"], "metrics": {}}
    return {"summary": summary, "metrics": {}, "reasons": summary["reasonCodes"], "comparison": {},
            "executionCost": {"metricVersion": "execution-cost-v1", "context": context},
            "policyEvidence": {"detectionPolicy": {"mode": "ml_only"}}, "threshold": None,
            "ml": measured_ml(tp=1, anomaly=1, succeeded=2, population=2,
                              predictions={str(uuid.UUID(int=1)): True, str(uuid.UUID(int=2)): False})}


class CompareExecutionTests(unittest.TestCase):
    def run_compare(self, checks, broken=()):
        conn, finished = Connection(), []
        rows = [(uuid.UUID(int=1), {}, "validation", None), (uuid.UUID(int=2), {}, "validation", None)]
        set_rows = [(str(row[0]), None, None, "c", None) for row in rows]
        _, content_hash = comparison.set_generation(set_rows, snapshot_id="snap", purpose="validation")
        loaded = {"id": uuid.UUID(SET), "snapshotId": "snap", "purpose": "validation", "contentHash": content_hash,
                  "generation": {"rowCount": 2, "originalRowCount": 2, "syntheticRowCount": 0}}
        order = []

        def check(conn, config_id, *_):
            order.append(str(config_id))
            return checks[str(config_id)]

        def context(conn, config_id, source_id, pinned=None):
            return ("broken" if str(config_id) in broken else "ok"), None

        message = type("Message", (), {"created_at": None})()
        with patch.object(comparison, "_load_set", return_value=loaded), patch.object(comparison, "_check_rule_set"), \
             patch.object(comparison, "set_members", return_value=set_rows), patch.object(comparison, "check_item", check), \
             patch.object(runner, "_snapshot_rows", return_value=rows), patch.object(runner, "queue_wait_ms", return_value=0.0), \
             patch.object(runner, "_version_context", context), patch.object(runner, "_measure_version", fake_measure), \
             patch.object(comparison, "lock_training_ownership", return_value=True), \
             patch.object(runner, "_finish", lambda *args, **kwargs: finished.append((args[5], args[6], kwargs))):
            result = comparison.execute_compare(conn, {"id": uuid.uuid4(), "input": spec()}, None, message,
                                                uuid.uuid4(), uuid.uuid4())
        return result, finished, order, conn.statements

    def test_one_failing_candidate_does_not_stop_the_others(self):
        checks = {ACTIVE: ("iforest", None), CANDIDATE_A: ("iforest", "training_evaluation_overlap"),
                  CANDIDATE_B: ("iforest", None)}
        result, finished, order, statements = self.run_compare(checks, broken={CANDIDATE_B})
        self.assertEqual(order, [ACTIVE, CANDIDATE_A, CANDIDATE_B])
        self.assertEqual([(item["configVersionId"], item["role"], item["status"], item["errorCode"]) for item in result["items"]],
                         [(ACTIVE, "active", "succeeded", None),
                          (CANDIDATE_A, "candidate", "failed", "training_evaluation_overlap"),
                          (CANDIDATE_B, "candidate", "failed", "evaluation_failed:model_feature_schema_mismatch")])
        self.assertEqual(result["replacementRecommendations"], None)
        self.assertEqual(result["replacementRecommendationsReason"], "not_implemented_v1")
        self.assertEqual(result["ruleSet"], {"revision": 2, "contentHash": HASH, "fixedAt": "accepted"})
        self.assertEqual(result["evaluationSet"]["synthetic"], False)
        self.assertEqual(result["items"][0]["categoryResults"]["ml"]["confusion"], {"tp": 1, "fp": None, "tn": None, "fn": 0})
        self.assertEqual(result["items"][0]["evaluationPolicy"],
                         {"source": "configuration", "detectionPolicy": {"mode": "ml_only"}, "ruleCount": None})
        self.assertEqual(result["items"][0]["counts"]["excludedCount"], 0)
        self.assertEqual(result["items"][0]["counts"]["excludedReasons"], {})
        # 실행 비용은 성공 항목에만, `_measure_version` 값 그대로.
        self.assertEqual(result["items"][0]["executionCost"], {"metricVersion": "execution-cost-v1", "context": "ok"})
        self.assertTrue(all("executionCost" not in item for item in result["items"][1:]))
        # 성공 항목이 1개라 쌍이 없다.
        self.assertEqual(result["pairwiseAgreement"], [])
        self.assertEqual(len(finished), 1)
        self.assertIsNotNone(finished[0][0])
        self.assertIsNone(finished[0][1])
        # 011 삽입 순서: 비교 → 평가 행 → 항목.
        tables = [next(name for name in ("run_config_comparisons", "run_evaluations", "run_config_comparison_items")
                       if f"INSERT INTO {name} " in sql) for sql in statements if sql.startswith("INSERT")]
        self.assertEqual(tables, ["run_config_comparisons", "run_evaluations"] + ["run_config_comparison_items"] * 3)

    def test_pairwise_agreement_follows_item_order(self):
        checks = {ACTIVE: ("iforest", None), CANDIDATE_A: ("iforest", None), CANDIDATE_B: ("iforest", None)}
        result, _, _, _ = self.run_compare(checks)
        pairs = result["pairwiseAgreement"]
        self.assertEqual([(pair["a"], pair["b"]) for pair in pairs],
                         [(ACTIVE, CANDIDATE_A), (ACTIVE, CANDIDATE_B), (CANDIDATE_A, CANDIDATE_B)])
        self.assertEqual([(pair["comparableCount"], pair["both"], pair["neither"], pair["agreementRate"]["coverage"])
                          for pair in pairs], [(2, 1, 1, 1.0)] * 3)

    def test_all_items_failing_fails_the_job_with_the_summary(self):
        checks = {ACTIVE: ("iforest", "prepared_not_comparable"), CANDIDATE_A: (None, "config_not_found"),
                  CANDIDATE_B: ("iforest", "method_not_executable:ml:iforest")}
        result, finished, _, statements = self.run_compare(checks)
        self.assertEqual(finished[0][0:2], (None, "comparison_all_items_failed"))
        self.assertEqual(finished[0][2]["failure_summary"]["failedCount"], 3)
        self.assertEqual(result["succeededCount"], 0)
        # 없는 버전은 FK 때문에 항목 표에 넣지 않고 summary에만 남긴다.
        self.assertEqual(sum("run_config_comparison_items" in sql for sql in statements), 2)
        self.assertFalse(any("INSERT INTO run_evaluations" in sql for sql in statements))

    def test_missing_active_baseline_fails_the_job(self):
        checks = {ACTIVE: (None, "config_not_found"), CANDIDATE_A: ("iforest", None), CANDIDATE_B: ("iforest", None)}
        with self.assertRaisesRegex(ValueError, "^config_not_found$"):
            self.run_compare(checks)

    def test_a_second_model_key_fails_the_job(self):
        checks = {ACTIVE: ("iforest", None), CANDIDATE_A: ("eif", None), CANDIDATE_B: ("iforest", None)}
        with self.assertRaisesRegex(ValueError, "^comparison_model_key_mixed$"):
            self.run_compare(checks)


class ItemConnection(Connection):
    """check_item이 읽는 표만 흉내 낸다. 응답은 SQL에 들어 있는 표 이름으로 고른다."""

    def __init__(self, *, source, keys, spec, training_schema="process-v1", overlap=False):
        super().__init__()
        self.answers = {"ml_config_versions": (source, spec), "data_snapshots": (training_schema,), "EXISTS": (overlap,)}
        self.keys = keys

    def fetchone(self):
        last = self.statements[-1]
        return next(value for name, value in self.answers.items() if name in last)

    def fetchall(self):
        return [(key,) for key in self.keys]


class CheckItemOrderTests(unittest.TestCase):
    """두 실패 조건이 함께 성립하면 표의 앞 코드가 남는다(계약 v1 항목 실패 표 순서)."""
    config, snapshot = uuid.UUID(CANDIDATE_A), uuid.uuid4()
    spec = {"schemaVersion": 1, "trainingSnapshotId": str(uuid.uuid4())}

    def check(self, **options):
        return comparison.check_item(ItemConnection(**options), self.config, uuid.UUID(SOURCE), self.snapshot, "validation")

    def test_source_mismatch_wins_over_model_row_count(self):
        self.assertEqual(self.check(source=uuid.uuid4(), keys=["iforest", "eif"], spec=self.spec),
                         (None, "config_source_mismatch"))

    def test_prepared_wins_over_unexecutable_method_and_overlap(self):
        result = self.check(source=uuid.UUID(SOURCE), keys=["eif"], spec=self.spec,
                            training_schema="prepared-features-v1", overlap=True)
        self.assertEqual(result, ("eif", "prepared_not_comparable"))

    def test_unexecutable_method_wins_over_overlap(self):
        self.assertEqual(self.check(source=uuid.UUID(SOURCE), keys=["eif"], spec=self.spec, overlap=True),
                         ("eif", "method_not_executable:ml:eif"))

    def test_overlap_is_reported_last(self):
        self.assertEqual(self.check(source=uuid.UUID(SOURCE), keys=["iforest"], spec=self.spec, overlap=True),
                         ("iforest", "training_evaluation_overlap"))
        self.assertEqual(self.check(source=uuid.UUID(SOURCE), keys=["iforest"], spec=self.spec), ("iforest", None))


class ClaimTests(unittest.TestCase):
    def test_trainer_claims_both_new_kinds(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        claim = source[source.index('message = claim(conn, "training", lease_seconds=60, job_kinds=("train"'):]
        claim = claim[:claim.index(")\n")]
        self.assertIn('"evaluation_set"', claim)
        self.assertIn('"compare"', claim)


if __name__ == "__main__":
    unittest.main()
