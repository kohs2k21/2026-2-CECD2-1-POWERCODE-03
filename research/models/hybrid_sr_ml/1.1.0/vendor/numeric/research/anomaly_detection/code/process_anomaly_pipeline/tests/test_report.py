"""Reader-facing counts, thresholds and evidence must preserve detector semantics."""
import unittest
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from process_anomaly_pipeline import ProcessAnomalyPipeline
from process_anomaly_pipeline.data import prepare
from process_anomaly_pipeline.analysis import experiment_split, set_style
from process_anomaly_pipeline.rules import RULE_NAMES
from process_anomaly_pipeline.report import (
    detection_counts, flow_table, partition_table, rule_removal_table,
    group_filter_counts, training_filter_table, adapter_training_counts,
    full_numeric_summary, statistical_threshold_table, route_threshold_table,
    select_case_indices, case_evidence_table, case_feature_table, case_context_text,
    plot_flow, plot_rule_removals, plot_partitions, plot_full_inputs, plot_full_score_margins,
)
from .test_pipeline import sample_data, test_config, row


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_style()
        cls.p = prepare(sample_data())
        cls.pipeline = ProcessAnomalyPipeline(test_config()).fit(cls.p)
        cls.result = cls.pipeline.predict(cls.p)
        cls.split = experiment_split(cls.p, cls.pipeline)

    def tearDown(self):
        plt.close("all")

    def test_five_way_partition_and_filter_flow_reconcile(self):
        p = prepare([row(status="F"), row(transactionId="n", status="N", endTime=None),
                     row(transactionId="s")])
        r = self.pipeline.predict(p)
        c = detection_counts(r)
        self.assertEqual(c["after_rules_unassessed"], 1)
        self.assertEqual(partition_table(r)["행 수"].sum(), len(p))
        self.assertEqual(flow_table(r).iloc[-1]["단계 후 잔여 행"], c["no_candidate_assessed"])
        self.assertNotIn("정상", " ".join(partition_table(r)["구분"]))
        self.assertEqual(c["candidates"], c["rules"] + c["if_only"])

    def test_sequential_rule_counts_deduplicate_and_keep_na_unknown(self):
        p = prepare([row(status="F", endTime=None), row(transactionId="s", errorCount=2),
                     row(transactionId="n", status="N", endTime=None)])
        r = self.pipeline.predict(p)
        table = rule_removal_table(r)
        self.assertEqual(table["이번 단계 추가 분리"].sum(), r.rule_candidate.sum())
        self.assertGreater(table["앞 단계와 중복"].sum(), 0)
        self.assertEqual(table.iloc[-1]["단계 후 잔여 행"], (~r.rule_candidate).sum())
        self.assertEqual(table.iloc[1]["단계 전 미평가 행"], table.iloc[1]["단계 전 행"])
        self.assertEqual(table.iloc[1]["이번 단계 추가 분리"], 0)

    def test_group_counts_and_training_counts_match_saved_fit(self):
        r, p, s, m = self.result, self.p, self.split, self.pipeline
        group = group_filter_counts(r)
        self.assertEqual(group["원본 행"].sum(), len(p))
        self.assertEqual(group["규칙 후 잔여"].sum(), (~r.rule_candidate).sum())
        training = training_filter_table(p, r, s, m)
        self.assertEqual(training.iloc[-1]["남은 학습 후보"], m.global_model.support["train_rows"])
        adapters = adapter_training_counts(p, r, s, m)
        self.assertEqual(adapters["세 기간 원본"].sum(), len(p))
        self.assertEqual(adapters["최종 학습 후보"].sum(), m.global_model.support["train_rows"])

    def test_full_numeric_valid_denominators_do_not_impute_missing_as_zero(self):
        p = prepare([row(), row(transactionId="n", status="N", endTime=None, totalCount=None),
                     row(transactionId="bad", retryCount=1.5)])
        r = self.pipeline.predict(p)
        table = full_numeric_summary(p, r)
        before = table.loc[table["집단"].eq("규칙 적용 전 전체")].set_index("변수")
        self.assertEqual(before.loc["처리시간", "유효값 행"], 2)
        self.assertEqual(before.loc["처리건수", "결측·무효 행"], 1)
        self.assertEqual(before.loc["재시도", "유효값 행"], 2)

    def test_actual_thresholds_and_global_reference_population(self):
        table = statistical_threshold_table(self.result, self.pipeline)
        reader = table.loc[table["기준집단"].eq("DB.READER") & table["변수"].eq("처리시간") & table["분위수"].eq("P99")].iloc[0]
        self.assertAlmostEqual(reader.threshold, self.pipeline.quantile_book.thresholds["DB.READER"][("duration_ms", .99)] / 1000)
        route = route_threshold_table(self.result, self.pipeline).set_index("판정 경로")
        self.assertEqual(route.loc["GLOBAL", "판정선 산정 S 행"], self.pipeline.global_model.support["threshold_rows"])
        self.assertEqual(route.loc["GLOBAL", "현재 IF 평가 행"], 0)
        self.assertGreater(route.loc["GLOBAL", "판정선 산정 S 행"], 0)

    def test_every_new_plot_is_filled_linear_and_can_render(self):
        p, r, m = self.p, self.result, self.pipeline
        for fig in (plot_flow(r), plot_rule_removals(r), plot_partitions(r),
                    plot_full_inputs(p, r, m), plot_full_score_margins(r)):
            fig.canvas.draw()
            for ax in fig.axes:
                self.assertEqual(ax.get_xscale(), "linear")
                self.assertEqual(ax.get_yscale(), "linear")
                self.assertTrue(all(patch.get_fill() for patch in ax.patches))
                self.assertFalse(ax.images)
        fig = plot_full_inputs(p, r, m)
        for ax in fig.axes:
            self.assertAlmostEqual(sum(bar.get_height() for bar in ax.patches), 100)
        fig = plot_full_score_margins(r)
        for ax in fig.axes:
            np.testing.assert_allclose(ax.lines[0].get_xdata(), 0)
            self.assertAlmostEqual(sum(bar.get_height() for bar in ax.containers[0]), 100)

    def test_case_evidence_is_exact_predicate_not_json_or_causal_attribution(self):
        p = prepare([row(transactionId="failure", status="F"), row(transactionId="error", errorCount=2)])
        r = self.pipeline.predict(p)
        cases = self.pipeline.explain(p, result=r)
        evidence = case_evidence_table(cases[0])
        self.assertIn("STATUS = F", evidence["판정 조건"].tolist())
        self.assertIn("F", evidence["실측값"].tolist())
        evidence = case_evidence_table(cases[1])
        self.assertTrue(any("errorCount>0" in text for text in evidence["판정 조건"]))
        self.assertFalse(case_feature_table(cases[1]).empty)
        self.assertFalse(cases[1]["feature_evidence_is_attribution"])

    def test_context_text_explains_small_values_without_claiming_causality(self):
        item = {"feature_evidence": [{"feature": "totalCount", "value": 3,
                "training_p01": 10000, "training_p99": 10485760}]}
        text = case_context_text(item)
        self.assertIn("3건 < 과거 P01 10,000건", text)
        self.assertIn("원인 기여도나 실제 장애를 확정하지 않는다", text)
        item["feature_evidence"][0]["value"] = 524288000
        self.assertIn("524,288,000건 > 과거 P99 10,485,760건", case_context_text(item))

    def test_case_selection_is_if_only_and_existing_data_is_unchanged(self):
        before = self.result.copy(deep=True)
        rules, models = select_case_indices(self.result, self.split)
        self.assertLessEqual(len(models), 2)
        self.assertTrue(self.result.loc[models, "model_candidate"].all())
        self.assertFalse(self.result.loc[models, "rule_candidate"].any())
        self.assertEqual(len(set(rules)), len(rules))
        rule_removal_table(self.result)
        partition_table(self.result)
        statistical_threshold_table(self.result, self.pipeline)
        pd.testing.assert_frame_equal(before, self.result)

    def test_score_margin_zero_ties_are_not_counted_as_candidates(self):
        r = self.result.iloc[:3].copy()
        r["score_margin"] = [-.1, 0., .1]
        r["model_candidate"] = r.score_margin.gt(0)
        r["rule_candidate"] = False
        r["anomaly_candidate"] = r.model_candidate
        self.assertEqual(detection_counts(r)["if_only"], 1)
        fig = plot_full_score_margins(r)
        self.assertIn("후보 1행", fig.axes[0].get_title())


if __name__ == "__main__":
    unittest.main()
