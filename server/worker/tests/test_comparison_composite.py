"""비교 항목 룰+모델 종합(`composite`) v1 집계 시험(config-comparison-evaluation.md "종합(`composite`) v1"). DB 없음."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector.attempt_v04 import _Model
from detector.hard_rules import HARD_RULES, V3_HARD_RULE_IDS
from detector.operational_state import ActiveModel, rule_set_content_hash
from trainer import comparison_composite as composite
from trainer.operational_state import DEFAULT_POLICY

# (종합 status, 종합 anomalous, 라벨) — complete N/T, partial+T, partial+None, unavailable × normal/anomaly/없음.
ROWS = [("complete", False, "normal"), ("complete", True, "anomaly"), ("complete", True, "normal"),
        ("complete", False, "anomaly"), ("partial", True, "anomaly"), ("partial", None, "normal"),
        ("unavailable", None, "anomaly"), ("complete", True, None), ("partial", True, None), ("complete", False, None)]


def measures(rows, relation="not_used"):
    return composite.composite_measures(rows, rule_set_revision=3, policy_revision=1, relation_inputs=relation)


class CompositeMeasureTests(unittest.TestCase):
    def test_two_bases_cells_hits_coverage(self):
        result = measures(ROWS)
        self.assertEqual({key: result[key] for key in ("metricVersion", "ruleSetRevision", "detectionPolicyRevision",
                                                        "populationCount", "relationInputs")},
                         {"metricVersion": "comparison-composite-v1", "ruleSetRevision": 3, "detectionPolicyRevision": 1,
                          "populationCount": 10, "relationInputs": "not_used"})
        self.assertEqual(result["labelCounts"], {"anomalyLabelCount": 4, "normalLabelCount": 3, "unlabeledCount": 3})
        complete, decidable = result["completeOnly"], result["decidable"]
        # 완료 기준: complete 6행(tn·tp·fp·fn 각 1 + 라벨 없음 2), T 3행.
        self.assertEqual((complete["basis"], complete["confusion"], complete["coverage"], complete["unavailableCount"],
                          complete["hitCount"]),
                         ("complete_only", {"tp": 1, "fp": 1, "tn": 1, "fn": 1}, .6, 4, 3))
        self.assertEqual(complete["recall"], {"value": .5, "unit": "ratio", "numerator": 1, "denominator": 2,
                                              "coverage": .5, "reason": None})
        self.assertEqual(complete["precision"], {"value": .5, "unit": "ratio", "numerator": 1, "denominator": 2,
                                                 "coverage": None, "reason": None})
        self.assertEqual(complete["falsePositiveRate"], {"value": .5, "unit": "ratio", "numerator": 1, "denominator": 2,
                                                         "coverage": 2 / 3, "reason": None})
        self.assertNotIn("biasNote", complete)
        # 판정 가능 기준: None 2행만 뺀다(partial+T 2행 포함) → tp 2, T 5행.
        self.assertEqual((decidable["basis"], decidable["confusion"], decidable["coverage"], decidable["unavailableCount"],
                          decidable["hitCount"], decidable["biasNote"]),
                         ("decidable", {"tp": 2, "fp": 1, "tn": 1, "fn": 1}, .8, 2, 5,
                          "partial_true_included_none_excluded"))
        self.assertEqual((decidable["recall"]["value"], decidable["recall"]["coverage"]), (2 / 3, .75))
        self.assertEqual(decidable["precision"]["value"], 2 / 3)
        self.assertEqual(set(decidable) - {"biasNote"}, set(complete))

    def test_invariants(self):
        for rows in (ROWS, ROWS[:4], [("partial", True, None)], [("unavailable", None, "normal")] * 3):
            with self.subTest(rows=rows):
                result = measures(rows)
                population = result["populationCount"]
                self.assertEqual(population, len(rows))
                self.assertEqual(sum(result["labelCounts"].values()), population)
                complete, decidable = result["completeOnly"], result["decidable"]
                for basis in (complete, decidable):
                    self.assertEqual(basis["coverage"], (population - basis["unavailableCount"]) / population)
                # complete ⊆ decidable
                self.assertLessEqual(decidable["unavailableCount"], complete["unavailableCount"])
                self.assertGreaterEqual(decidable["hitCount"], complete["hitCount"])

    def test_label_null_rules(self):
        unlabeled = measures([("complete", True, None), ("partial", True, None), ("unavailable", None, None)])
        for basis in ("completeOnly", "decidable"):
            part = unlabeled[basis]
            self.assertEqual(part["confusion"], {"tp": None, "fp": None, "tn": None, "fn": None})
            self.assertEqual(part["recall"]["reason"], "verified_anomaly_labels_unavailable")
            self.assertEqual((part["precision"]["value"], part["precision"]["reason"]),
                             (None, "verified_normal_labels_unavailable"))
            self.assertEqual(part["falsePositiveRate"]["reason"], "verified_normal_labels_unavailable")
            self.assertIsNone(part["recall"]["numerator"])
        # 라벨이 없어도 값이 나오는 것: hitCount·coverage·unavailableCount.
        self.assertEqual((unlabeled["completeOnly"]["hitCount"], unlabeled["decidable"]["hitCount"]), (1, 2))
        self.assertEqual((unlabeled["completeOnly"]["coverage"], unlabeled["decidable"]["coverage"]), (1 / 3, 2 / 3))
        anomaly_only = measures([("complete", True, "anomaly"), ("partial", None, "anomaly")])["completeOnly"]
        self.assertEqual(anomaly_only["confusion"], {"tp": 1, "fp": None, "tn": None, "fn": 0})
        self.assertEqual((anomaly_only["recall"]["value"], anomaly_only["recall"]["coverage"]), (1.0, .5))
        self.assertEqual(anomaly_only["precision"]["reason"], "verified_normal_labels_unavailable")
        normal_only = measures([("complete", False, "normal")])["completeOnly"]
        self.assertEqual(normal_only["confusion"], {"tp": None, "fp": 0, "tn": 1, "fn": None})
        self.assertEqual(normal_only["precision"]["reason"], "verified_anomaly_labels_unavailable")
        self.assertEqual(normal_only["falsePositiveRate"]["value"], 0.0)

    def test_empty_denominators(self):
        # 라벨은 있으나 기준 행에 그 라벨 행이 없음 → value null + 분모 0 사유(coverage 0).
        result = measures([("unavailable", None, "anomaly"), ("partial", None, "normal"), ("complete", False, None)])
        complete = result["completeOnly"]
        self.assertEqual(complete["recall"], {"value": None, "unit": "ratio", "numerator": 0, "denominator": 0,
                                              "coverage": 0.0, "reason": "no_scored_anomaly_rows"})
        self.assertEqual(complete["precision"]["reason"], "no_predicted_anomalies")
        self.assertEqual(complete["falsePositiveRate"]["reason"], "no_scored_normal_rows")

    def test_zero_rows_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "^all_rows_excluded$"):
            measures([])


class Conn:
    """pin_fixed_state가 읽는 룰 세트 리비전 행만 돌려준다. 다른 문장은 시험 실패."""

    def __init__(self, snapshot, version=2):
        self.row = (rule_set_content_hash(snapshot, version), version, snapshot)
        self.statements = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        assert "ops_rule_set_revisions" in sql, sql
        self.statements.append(sql)

    def fetchone(self):
        return self.row

    def commit(self):
        pass

    def rollback(self):
        pass


RAW = {"status": "S", "start_time_raw": "2026-01-01 00:00:00", "end_time_raw": "2026-01-01 00:00:01",
       "total_count": "10", "success_count": "10", "error_count": "0", "retry_count": "0"}


class JudgeItemTests(unittest.TestCase):
    config = uuid.uuid4()

    def setUp(self):
        self.snapshot = {"hardRules": [{**rule, "enabled": True} for rule in HARD_RULES
                                       if rule["id"] not in V3_HARD_RULE_IDS], "softRules": []}
        self.conn = Conn(self.snapshot)
        self.plan = {"detectionPolicy": DEFAULT_POLICY, "ruleSetRevision": 1, "ruleSetContentHash": self.conn.row[0],
                     "detectionPolicyRevision": 0, "activeModelSetRevision": 4}

    def judge(self, rows, *, load_error=None, plan=None):
        model = _Model(ActiveModel(self.config, "iforest"), None, load_error)
        with patch("detector.attempt_v04.load_models", return_value=[model]):
            return composite.judge_item(self.conn, plan=plan or self.plan, policy_value=None, config_id=self.config,
                                        model_key="iforest", source_id=uuid.uuid4(), rows=rows, root=None)

    def test_detector_judgment_partial_true_counts_only_in_decidable(self):
        """실제 Detector 판정(고정 룰 세트 4개 + 모델 1개, 모델 산출물 없음 → ML not_ready): 상태 F는 하드룰 T(partial),
        상태 S는 None(partial) — 완료 기준은 0행, 판정 가능 기준은 T 1행."""
        rows = [(uuid.UUID(int=1), {**RAW, "status": "F"}, "validation", {"label": "anomaly"}),
                (uuid.UUID(int=2), RAW, "validation", None)]
        value, reason = self.judge(rows)
        self.assertIsNone(reason)
        self.assertEqual((value["populationCount"], value["relationInputs"], value["labelCounts"]),
                         (2, "not_used", {"anomalyLabelCount": 1, "normalLabelCount": 0, "unlabeledCount": 1}))
        self.assertEqual((value["completeOnly"]["unavailableCount"], value["completeOnly"]["hitCount"]), (2, 0))
        self.assertEqual((value["decidable"]["unavailableCount"], value["decidable"]["hitCount"]), (1, 1))
        self.assertEqual(value["decidable"]["confusion"], {"tp": 1, "fp": None, "tn": None, "fn": 0})
        self.assertEqual(len(self.conn.statements), 1, "고정 리비전 1회 판독 뒤 행 판정은 DB를 읽지 않는다(관계 규칙 없음)")

    def test_check_final_runs_per_row_with_sorted_method_results(self):
        """행마다 운영 build_evidence와 같은 인자로 check_final을 부르고, 정상 행은 통과해 결과가 그대로다."""
        from detector import decision, evidence_v4
        rows = [(uuid.UUID(int=1), {**RAW, "status": "F"}, "validation", {"label": "anomaly"}),
                (uuid.UUID(int=2), RAW, "validation", None)]
        expected = self.judge(rows)
        with patch("detector.evidence_v4.check_final", wraps=evidence_v4.check_final) as spy:
            self.assertEqual(self.judge(rows), expected)
        self.assertEqual(spy.call_count, 2)
        for call in spy.call_args_list:
            methods, hard_rules, hard_ids = call.args
            # decide가 정렬한 methodResults(정렬 전 execution.methods가 아님) · 하드룰 선택 · 이 판정의 hard_ids.
            self.assertEqual(methods, sorted(methods, key=decision._rank))
            self.assertIn("ml:iforest", [method["methodId"] for method in methods])
            self.assertTrue(isinstance(hard_ids, tuple) and set(hard_ids) <= set(hard_rules.enabled_ids))

    def test_check_final_failure_on_one_row_nulls_whole_item(self):
        rows = [(uuid.UUID(int=1), RAW, "validation", {"label": "normal"}),
                (uuid.UUID(int=2), {**RAW, "status": "F"}, "validation", {"label": "anomaly"})]
        with patch("detector.evidence_v4.check_final",
                   side_effect=[None, ValueError("hard_rule_selection_mismatch")]) as fake:
            self.assertEqual(self.judge(rows), (None, "hard_rule_selection_mismatch"))
        self.assertEqual(fake.call_count, 2)
        with patch("detector.evidence_v4.check_final", side_effect=ValueError("final_decision_has_pending_method")):
            self.assertEqual(self.judge(rows), (None, "final_decision_has_pending_method"))

    def test_null_branches(self):
        self.assertEqual(self.judge([], plan={**self.plan, "detectionPolicy": {**DEFAULT_POLICY, "k": {
            "hard": 1, "soft": 1, "ml": 2}}}), (None, "policy_k_exceeds_single_model"))
        self.assertEqual(self.judge([]), (None, "all_rows_excluded"))
        rows = [(uuid.UUID(int=1), RAW, "validation", None)]
        self.assertEqual(self.judge(rows, load_error="model_unavailable"), (None, "model_unavailable"))
        # Detector 고정 판독 실패(정책 리비전·값 불일치)는 종합만 null + Detector 코드.
        self.assertEqual(self.judge(rows, plan={**self.plan, "detectionPolicyRevision": 2}),
                         (None, "detection_policy_unavailable"))
        self.assertEqual(composite.unavailable_code(ValueError("문구 That Is Not A Code")), "composite_preparation_failed")


if __name__ == "__main__":
    unittest.main()
