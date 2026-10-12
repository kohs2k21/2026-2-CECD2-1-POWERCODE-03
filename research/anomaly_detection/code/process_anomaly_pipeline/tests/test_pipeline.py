"""Small, deterministic tests; no production data or model files are modified."""
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd

from process_anomaly_pipeline import PipelineConfig, ProcessAnomalyPipeline
from process_anomaly_pipeline.data import prepare, chronological_split, load_workspace
from process_anomaly_pipeline.rules import evaluate_rules
from process_anomaly_pipeline.quantiles import QuantileBook


def row(**changes):
    value = {"transactionId": "t1", "processId": "p1", "processHubId": "HUB", "retryCount": 0,
             "adapterType": "DB.READER", "status": "S", "startTime": "2026-01-01 12:00:00",
             "endTime": "2026-01-01 12:00:01", "totalCount": 10, "successCount": 10, "errorCount": 0,
             "source_period": "a"}
    value.update(changes)
    return value


def sample_data():
    rng = np.random.default_rng(12)
    items = []
    for day in range(20):
        for execution in range(40):
            for step, adapter in enumerate(("DB.READER", "DB.WRITER")):
                start = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day, minutes=execution * 20 + step)
                duration = float(rng.lognormal(5 + step, .25))
                count = int(rng.integers(1, 80))
                items.append(row(transactionId=f"d{day}e{execution}", processId=f"p{step}", adapterType=adapter,
                                 startTime=start, endTime=start + pd.Timedelta(milliseconds=duration),
                                 totalCount=count, successCount=count))
    return pd.DataFrame(items)


def test_config(**overrides):
    value = dict(n_estimators=12, max_samples=64, max_train_rows=500, max_calibration_rows=500,
                 score_batch_rows=31, min_global_train=30, min_global_calibration=20,
                 min_global_threshold=20, min_adapter_train=30, min_adapter_calibration=20,
                 min_adapter_threshold=20, min_adapter_days=2, min_p99_support=30, min_p999_support=100,
                 exclude_statistical_from_training=False, n_jobs=1)
    value.update(overrides)
    return PipelineConfig(**value)


class RuleTests(unittest.TestCase):
    def evaluate(self, records, **kwargs):
        return evaluate_rules(prepare(records), test_config(), **kwargs)[0]

    def test_f_only_and_h_not_failed(self):
        f = self.evaluate([row(status="F"), row(transactionId="t2", status="H")])
        self.assertTrue(f.status_failed.iloc[0])
        self.assertFalse(f.status_failed.iloc[1])

    def test_success_error_and_count_mismatch(self):
        f = self.evaluate([row(errorCount=2), row(transactionId="t2", status="N", totalCount=1, successCount=9)])
        self.assertTrue(f.success_with_error.iloc[0])
        self.assertTrue(f.count_conservation.iloc[0])
        self.assertTrue(pd.isna(f.count_conservation.iloc[1]))

    def test_missing_required_and_counts(self):
        f = self.evaluate([row(processId=None), row(transactionId="t2", retryCount=-1),
                           row(transactionId="t3", totalCount=1.5)])
        self.assertTrue(f.missing_sql_required.iloc[0])
        self.assertTrue(f.invalid_count_domain.iloc[1:].all())

    def test_missing_end_only_terminal(self):
        f = self.evaluate([row(endTime=None), row(transactionId="t2", status="N", endTime=None)])
        self.assertTrue(f.final_missing_end.iloc[0])
        self.assertTrue(pd.isna(f.final_missing_end.iloc[1]))

    def test_reversed_clock_and_timezone(self):
        f = self.evaluate([row(endTime="2026-01-01 11:59:59")])
        self.assertTrue(f.negative_duration.iloc[0])
        p = prepare([row(startTime="2026-01-01T03:00:00Z", endTime="2026-01-01T12:00:01+09:00")])
        self.assertAlmostEqual(p.duration_ms.iloc[0], 1000)

    def test_invalid_values_are_not_silently_imputed(self):
        f = self.evaluate([row(totalCount="bad"), row(transactionId="t2", status="X"),
                           row(transactionId="t3", retryCount=float("inf")), row(transactionId="t4", totalCount=True)])
        self.assertTrue(f.invalid_input.all())

    def test_null_words_and_aliases(self):
        p = prepare([{"TRANSACTION_ID": "t", "PROCESS_ID": "NULL", "PROCESS_HUB_ID": "HUB", "RETRY_COUNT": "0"}])
        f = evaluate_rules(p, test_config())[0]
        self.assertTrue(f.missing_sql_required.iloc[0])
        self.assertFalse(f.invalid_count_domain.iloc[0])

    def test_duplicate_sql_key(self):
        f = self.evaluate([row(), row()])
        self.assertTrue(f.invalid_input.all())

    def test_optional_transaction_context_is_unknown(self):
        f = self.evaluate([row()])
        self.assertTrue(pd.isna(f.process_count_mismatch.iloc[0]))
        self.assertTrue(pd.isna(f.tx_success_all_process_failed.iloc[0]))

    def test_complete_context_all_failed(self):
        p = [row(status="F"), row(processId="p2", status="F")]
        t = [{"transactionId": "t1", "processHubId": "HUB", "retryCount": 0, "status": "S", "processCount": 2, "source_period": "a"}]
        f = self.evaluate(p, transactions=t, complete_context=True)
        self.assertTrue(f.tx_success_all_process_failed.all())
        self.assertFalse(f.process_count_mismatch.any())
        f = self.evaluate(p, transactions=t)
        self.assertTrue(f.process_count_mismatch.isna().all())

    def test_incomplete_stages_not_all_failed(self):
        t = [{"transactionId": "t1", "processHubId": "HUB", "retryCount": 0, "status": "S", "processCount": 2, "source_period": "a"}]
        f = self.evaluate([row(status="F")], transactions=t, complete_context=True)
        self.assertTrue(f.process_count_mismatch.iloc[0])
        self.assertTrue(pd.isna(f.tx_success_all_process_failed.iloc[0]))

    def test_cross_hub_and_retry_not_joined(self):
        t = [{"transactionId": "t1", "processHubId": "OTHER", "retryCount": 0, "status": "S", "processCount": 1, "source_period": "a"}]
        f = self.evaluate([row(status="F")], transactions=t, complete_context=True)
        self.assertTrue(f.process_count_mismatch.isna().all())
        t[0]["processHubId"] = "HUB"
        t[0]["retryCount"] = 1
        f = self.evaluate([row(status="F")], transactions=t, complete_context=True)
        self.assertTrue(f.process_count_mismatch.isna().all())

    def test_ambiguous_transaction_never_explodes_rows(self):
        t = [{"transactionId": "t1", "processHubId": "HUB", "retryCount": 0, "status": "S", "processCount": 1, "source_period": "a"}] * 2
        f = self.evaluate([row()], transactions=t, complete_context=True)
        self.assertEqual(len(f), 1)
        self.assertTrue(f.process_count_mismatch.isna().all())

    def test_offline_loader_follows_schema_not_filename_and_keeps_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            period = root / "fixture"
            period.mkdir()
            process = pd.DataFrame([row(dependProcessId=None), row(dependProcessId=None)])
            process.to_csv(period / "EC_TRANSACTION_MONITORING.csv", index=False)
            tx = pd.DataFrame([{"transactionId": "t1", "interfaceId": "IF", "processHubId": "HUB",
                                "retryCount": 0, "processCount": 1, "processTime": 1000, "status": "S",
                                "startTime": "2026-01-01 12:00:00", "endTime": "2026-01-01 12:00:01"}])
            tx.to_csv(period / "EC_MESSAGE_BODY.csv", index=False)
            p, t, audit = load_workspace(root, periods=["fixture"], with_transactions=True)
            self.assertEqual((len(p), len(t)), (2, 1))
            self.assertTrue(audit.iloc[0].source.endswith("EC_TRANSACTION_MONITORING.csv"))
            self.assertTrue(evaluate_rules(p, test_config())[0].invalid_input.all())


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = sample_data()
        cls.config = test_config()
        cls.model = ProcessAnomalyPipeline(cls.config).fit(cls.raw)

    def test_chronological_outer_split_and_transaction_integrity(self):
        p = prepare(self.raw)
        split = chronological_split(p, self.config)
        self.assertLess(p.loc[split.eq("train"), "startTime"].max(), p.loc[split.isin(["calibration", "threshold"]), "startTime"].min())
        self.assertLess(p.loc[split.isin(["calibration", "threshold"]), "startTime"].max(), p.loc[split.eq("test"), "startTime"].min())
        self.assertEqual(pd.DataFrame({"id": p.transactionId, "split": split}).groupby("id").split.nunique().max(), 1)

    def test_real_ensemble_and_new_adapter_fallback(self):
        items = self.raw.tail(2).copy()
        items.iloc[1, items.columns.get_loc("adapterType")] = "NEW.ADAPTER"
        r = self.model.predict(items)
        self.assertEqual(r.model_route.iloc[0], "global+adapter")
        self.assertEqual(r.model_route.iloc[1], "global_only")
        self.assertEqual(r.fallback_reason.iloc[1], "new_adapter")
        self.assertTrue(np.isfinite(r.ensemble_score).all())

    def test_new_process_id_does_not_disable_adapter(self):
        item = self.raw.tail(1).copy()
        item["processId"] = "BRAND_NEW_PROCESS"
        self.assertEqual(self.model.predict(item).model_route.iloc[0], "global+adapter")

    def test_invalid_and_pending_are_returned_unscored(self):
        r = self.model.predict([row(processId=None), row(transactionId="t2", status="N", endTime=None)])
        self.assertEqual(len(r), 2)
        self.assertTrue(r.ensemble_score.isna().all())
        self.assertEqual(r.decision.iloc[0], "quality_error")
        self.assertEqual(r.decision.iloc[1], "not_assessed")

    def test_batch_invariant_threshold_and_scores(self):
        items = self.raw.tail(20)
        many = self.model.predict(items)
        single = self.model.predict(items.head(1))
        self.assertAlmostEqual(many.ensemble_score.iloc[0], single.ensemble_score.iloc[0])
        self.assertAlmostEqual(many.model_threshold.iloc[0], single.model_threshold.iloc[0])

    def test_future_data_does_not_change_training_thresholds(self):
        changed = self.raw.copy()
        changed.loc[changed.startTime.ge("2026-01-17"), "totalCount"] = 999999
        changed.loc[changed.startTime.ge("2026-01-17"), "successCount"] = 999999
        other = ProcessAnomalyPipeline(self.config).fit(changed)
        self.assertEqual(self.model.route_thresholds, other.route_thresholds)
        self.assertEqual(self.model.quantile_book.thresholds, other.quantile_book.thresholds)

    def test_adapter_only_in_future_is_still_new(self):
        changed = self.raw.copy()
        changed.loc[changed.startTime.ge("2026-01-17"), "adapterType"] = "FUTURE.ADAPTER"
        other = ProcessAnomalyPipeline(self.config).fit(changed)
        self.assertNotIn("FUTURE.ADAPTER", other.training_summary["observed_adapters"])
        self.assertEqual(other.predict(changed.tail(1)).fallback_reason.iloc[0], "new_adapter")
        self.assertEqual(other.route_thresholds, self.model.route_thresholds)

    def test_default_statistical_exclusion_is_applied(self):
        model = ProcessAnomalyPipeline(test_config(exclude_statistical_from_training=True)).fit(self.raw)
        self.assertLess(model.global_model.support["train_rows"], model.training_summary["reference_train_before_tail_filter"])

    def test_complete_context_requires_metadata(self):
        with self.assertRaises(ValueError): self.model.predict([row()], complete_context=True)
        with self.assertRaises(ValueError): ProcessAnomalyPipeline(self.config).fit(self.raw, complete_context=True)

    def test_cli_jsonl_inference(self):
        from contextlib import redirect_stdout
        import io
        from process_anomaly_pipeline.cli import main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "own.joblib"
            self.model.save(model)
            records = root / "process.json"
            records.write_text(json.dumps([row(status="F"), row(transactionId="new", adapterType="NEW")]), encoding="utf-8")
            output = root / "result.jsonl"
            with redirect_stdout(io.StringIO()):
                main(["predict", "--model", str(model), "--trust-model", "--input", str(records), "--output", str(output)])
            parsed = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(parsed), 2)
            self.assertEqual(parsed[0]["decision"], "reported_failure")
            self.assertEqual(parsed[1]["model"]["fallback"], "new_adapter")

    def test_json_safe_explanations_including_unscored(self):
        records = self.model.predict_records([row(status="F"), row(transactionId="t2", status="N", endTime=None)])
        json.dumps(records, allow_nan=False)
        self.assertEqual(records[0]["rule_evidence"][0]["rule"], "status_failed")
        self.assertIsNone(records[1]["model"]["ensemble_score"])

    def test_complete_process_identity_and_duplicate_evidence(self):
        records = self.model.predict_records([row(), row()])
        self.assertEqual(records[0]["retryCount"], 0)
        self.assertEqual(records[0]["processHubId"], "HUB")
        self.assertEqual(records[0]["source_period"], "a")
        e = next(e for e in records[0]["rule_evidence"] if e["rule"] == "invalid_input")
        self.assertTrue(e["duplicate_process_key"])

    def test_extra_fields_cannot_break_invalid_input_explanation(self):
        record = row(status="X", _invalid_unknown=None)
        output = self.model.predict_records([record])[0]
        json.dumps(output, allow_nan=False)
        e = next(e for e in output["rule_evidence"] if e["rule"] == "invalid_input")
        self.assertTrue(e["undefined_status"])

    def test_explanation_requires_same_attempt_identity(self):
        records = [row()]
        result = self.model.predict(records)
        result["retryCount"] = 2
        with self.assertRaises(ValueError): self.model.explain(records, result=result)

    def test_missing_adapter_uses_global_model(self):
        result = self.model.predict([row(adapterType=None)])
        self.assertEqual(result.model_route.iloc[0], "global_only")
        self.assertEqual(result.fallback_reason.iloc[0], "missing_adapter")

    def test_p999_explanation_metric_name(self):
        record = row(totalCount=999999, successCount=999999, endTime="2026-01-01 12:10:00")
        explanation = self.model.predict_records([record])[0]
        tail = next(e for e in explanation["rule_evidence"] if e["rule"] == "statistical_tail")
        self.assertTrue(tail["exceedances"])
        self.assertIn("duration_ms", {e["metric"] for e in tail["exceedances"]})

    def test_save_load_and_no_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.joblib"
            self.model.save(path)
            with self.assertRaises(ValueError): ProcessAnomalyPipeline.load(path)
            with self.assertRaises(FileExistsError): self.model.save(path)
            loaded = ProcessAnomalyPipeline.load(path, trusted=True)
            a = self.model.predict(self.raw.tail(4))
            b = loaded.predict(self.raw.tail(4))
            np.testing.assert_allclose(a.ensemble_score, b.ensemble_score)
            self.assertFalse(hasattr(loaded, "split_"))

    def test_explain_existing_full_context_result(self):
        p = prepare([row(status="F"), row(processId="p2", status="F")])
        t = [{"transactionId": "t1", "processHubId": "HUB", "retryCount": 0, "status": "S", "processCount": 2, "source_period": "a"}]
        r = self.model.predict(p, transactions=t, complete_context=True)
        e = self.model.explain(p.iloc[:1], result=r.iloc[:1])
        self.assertIn("tx_success_all_process_failed", [x["rule"] for x in e[0]["rule_evidence"]])

    def test_empty_prediction_and_unfitted(self):
        self.assertTrue(self.model.predict([]).empty)
        with self.assertRaises(RuntimeError): ProcessAnomalyPipeline().predict([row()])

    def test_parameter_validation(self):
        for overrides in ({"n_estimators": 0}, {"n_jobs": 0}, {"unknown": 1},
                          {"global_weight": 1}, {"score_quantile": float("nan")},
                          {"exclude_statistical_from_training": "false"},
                          {"quantile_columns": ["duration_ms", "duration_ms"]}):
            with self.assertRaises(ValueError): PipelineConfig.from_dict(overrides)

    def test_quantile_minimum_support_fallback(self):
        p = prepare(self.raw)
        tiny = p.copy()
        tiny["adapterType"] = "RARE"
        ref = pd.concat([p, tiny.iloc[:2]], ignore_index=True)
        book = QuantileBook.fit(ref, self.config)
        flags, details = book.evaluate(tiny.iloc[:1])
        self.assertEqual(details.p99_duration_ms_basis.iloc[0], "global")
        self.assertFalse(pd.isna(flags.iloc[0]))

    def test_quantile_ties_do_not_exceed(self):
        book = QuantileBook.fit(prepare(self.raw), self.config)
        book.thresholds = {"GLOBAL": {("retryCount", .99): 0.0, ("retryCount", .999): 0.0}}
        tail, details = book.evaluate(prepare([row()]))
        self.assertFalse(tail.iloc[0])
        self.assertFalse(details.p99_retryCount_exceed.iloc[0])


if __name__ == "__main__":
    unittest.main()
