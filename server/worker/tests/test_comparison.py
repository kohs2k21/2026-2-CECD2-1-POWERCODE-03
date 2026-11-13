"""평가 세트·모델 버전 비교 v1의 순수 부분 시험(config-comparison-evaluation.md v1 구현 범위). DB 없음."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer import comparison, runner
from trainer.operational_state import DEFAULT_POLICY

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


def measured_ml(tp=0, fp=0, tn=0, fn=0, anomaly=0, normal=0, succeeded=10, selected=True, population=10):
    return {"selected": selected, "succeededCount": succeeded, "populationCount": population,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn}, "labelCounts": {"anomaly": anomaly, "normal": normal}}


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
               "applicability": "valid", "reasonCodes": ["labels_unavailable"], "metrics": {}}
    return {"summary": summary, "metrics": {}, "reasons": summary["reasonCodes"], "comparison": {},
            "executionCost": {}, "policyEvidence": {"detectionPolicy": {"mode": "ml_only"}}, "threshold": None,
            "ml": measured_ml(tp=1, anomaly=1, succeeded=2, population=2)}


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
        self.assertEqual(len(finished), 1)
        self.assertIsNotNone(finished[0][0])
        self.assertIsNone(finished[0][1])
        # 011 삽입 순서: 비교 → 평가 행 → 항목.
        tables = [next(name for name in ("run_config_comparisons", "run_evaluations", "run_config_comparison_items")
                       if f"INSERT INTO {name} " in sql) for sql in statements if sql.startswith("INSERT")]
        self.assertEqual(tables, ["run_config_comparisons", "run_evaluations"] + ["run_config_comparison_items"] * 3)

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
