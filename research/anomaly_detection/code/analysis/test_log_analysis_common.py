"""Regression checks for export schemas, periods, keys and causal evaluation."""

from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from log_analysis_common import (
    add_time_features, chronological_split, contextual_duration, dependency_features,
    discover_source, fit_anomaly_baseline, load_logs, numeric_drift, parse_kst, transaction_process_summary,
)


class LogAnalysisTests(unittest.TestCase):
    def test_export_content_wins_over_misleading_filename(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            folder = Path(directory) / "period"
            folder.mkdir()
            (folder / "EC_PROCESS_MONITORING.csv").write_text(
                "TRANSACTION_ID,PROCESS_ID,MESSAGE_ID,PROCESS_DT\nt,p,m,2026-10-02\n", encoding="utf-8")
            (folder / "EC_TRANSACTION_MONITORING.csv").write_text(
                "TRANSACTION_ID,PROCESS_ID,DEPEND_PROCESS_ID,ADAPTER_TYPE,TOTAL_COUNT,START_TIME,END_TIME,RETRY_COUNT,RESPONSE_CODE,RESPONSE_MESSAGE\n"
                't,p,NONE,DB.READER,2,2026-10-02 12:00:00,2026-10-02 12:00:01,0,0000,"first\nsecond"\n'
                't,p,NONE,DB.READER,2,2026-10-02 12:00:00,2026-10-02 12:00:01,0,0000,"first\nsecond"\n', encoding="utf-8")
            selected, _ = discover_source(folder, "process")
            self.assertEqual(selected.name, "EC_TRANSACTION_MONITORING.csv")
            result, audit = load_logs("process", directory, periods=["period"], chunksize=1)
            self.assertEqual(len(result), 1)
            self.assertEqual(result.loc[0, "responseCode"], "0000")
            self.assertEqual(result.loc[0, "dependProcessId"], "NONE")
            self.assertEqual(audit.loc[0, "raw_rows"], 2)
            self.assertEqual(audit.loc[0, "exact_duplicates_removed"], 1)

    def test_sql_export_and_escaped_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            folder = Path(directory) / "period"
            folder.mkdir()
            (folder / "EC_MESSAGE_BODY.csv").write_text(
                '-- SQLite lossless export: source columns only.\nBEGIN TRANSACTION;\n'
                'CREATE TABLE EC_TRANSACTION_MONITORING("TRANSACTION_ID" TEXT,"INTERFACE_ID" TEXT,"PROCESS_COUNT" TEXT,"PROCESS_TIME" TEXT,"START_TIME" TEXT,"END_TIME" TEXT);\n'
                "INSERT INTO EC_TRANSACTION_MONITORING VALUES('t','reader''s,interface','2','1000','2026-10-02 12:00:00',NULL);\nCOMMIT;\n", encoding="utf-8")
            frame, audit = load_logs("transaction", directory, periods=["period"])
            self.assertEqual(frame.loc[0, "interfaceId"], "reader's,interface")
            self.assertTrue(pd.isna(frame.loc[0, "endTime"]))
            self.assertEqual(audit.loc[0, "format"], "sql")

    def test_naive_and_aware_times_are_both_kst(self):
        result = parse_kst(pd.Series(["2026-09-03 11:26:31.174", "2026-09-03T11:26:31.174+09:00", "2026-09-03T02:26:31.174Z", "bad", None]))
        self.assertEqual(result[0], result[1])
        self.assertEqual(result[0], result[2])
        self.assertTrue(result.iloc[3:].isna().all())

    def test_dependency_scope_missing_end_and_retry_ambiguity(self):
        frame = pd.DataFrame({
            "source_period": ["a", "a", "b", "a", "a", "a"],
            "transactionId": ["t", "t", "t", "u", "u", "u"],
            "processId": ["p", "q", "q", "p", "p", "q"],
            "dependProcessId": ["NONE", "p", "p", "NONE", "NONE", "p"],
            "startTime": pd.to_datetime(["2026-01-01"] * 6),
            "endTime": pd.to_datetime([None, "2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01"]),
        })
        result = dependency_features(frame)
        self.assertTrue(result.loc[1, "parent_log_found"])
        self.assertFalse(result.loc[1, "dependency_missing"])
        self.assertFalse(result.loc[1, "dependency_time_comparable"])
        self.assertTrue(result.loc[2, "dependency_missing"])
        self.assertTrue(result.loc[5, "dependency_retry_ambiguous"])
        self.assertFalse(result.loc[5, "dependency_time_comparable"])

    def test_declared_count_uses_logical_processes_not_attempts(self):
        tx = pd.DataFrame({"source_period": ["a", "b"], "transactionId": ["t", "t"],
                           "retryCount": [0, 0], "processCount": [1, 1], "status": ["S", "S"],
                           "startTime": pd.to_datetime(["2026-01-01", "2026-08-15"]),
                           "endTime": pd.to_datetime(["2026-01-01 00:00:03", "2026-08-15 00:00:03"])})
        p = pd.DataFrame({"source_period": ["a", "a"], "transactionId": ["t", "t"], "processId": ["p", "p"],
                          "retryCount": [0, 1], "status": ["F", "S"],
                          "startTime": pd.to_datetime(["2026-01-01", "2026-01-01 00:00:01"], format="mixed"),
                          "endTime": pd.to_datetime(["2026-01-01 00:00:01", "2026-01-01 00:00:02"])})
        summary = transaction_process_summary(add_time_features(tx), add_time_features(p))
        first = summary.loc[summary["source_period"].eq("a")].iloc[0]
        second = summary.loc[summary["source_period"].eq("b")].iloc[0]
        self.assertEqual(first["process_attempts"], 2)
        self.assertEqual(first["distinct_processes"], 1)
        self.assertFalse(first["count_mismatch_candidate"])
        self.assertEqual(first["process_derived_status"], "S")
        self.assertTrue(second["no_process_logs"])

    def test_zero_mad_is_not_a_false_normal_decision(self):
        frame = pd.DataFrame({"source_period": ["a"] * 31, "processId": ["p"] * 31, "duration_ms": [1.] * 30 + [10.]})
        _, result = contextual_duration(frame, "processId")
        self.assertTrue(result["context_z"].isna().all())
        self.assertFalse(result["context_comparable"].any())

    def test_drift_detects_new_values_after_constant_reference(self):
        frame = pd.DataFrame({"source_period": ["old"] * 100 + ["new"] * 100,
                              "retryCount": [0.] * 100 + [1.] * 100})
        result = numeric_drift(frame, ["retryCount"], reference_period="old").set_index("source_period")
        self.assertAlmostEqual(result.loc["old", "PSI"], 0)
        self.assertGreater(result.loc["new", "PSI"], 1)

    def test_temporal_split_and_training_only_features(self):
        rng = np.random.default_rng(42)
        frame = pd.DataFrame({"source_period": ["a"] * 200,
                              "transactionId": [f"t{i}" for i in range(200)],
                              "startTime": pd.date_range("2026-01-01", periods=200, freq="h"),
                              "duration_ms": rng.lognormal(size=200), "retryCount": [0.] * 200,
                              "interfaceId": ["old"] * 144 + ["future"] * 56})
        split = chronological_split(frame)
        self.assertLess(frame.loc[split.eq("train"), "startTime"].max(), frame.loc[split.eq("validation"), "startTime"].min())
        result, info, fitted = fit_anomaly_baseline(frame, ["duration_ms", "retryCount"], ["interfaceId"], max_train=50, batch_size=31)
        self.assertEqual(info["training_sample_rows"], 50)
        self.assertEqual(info["threshold"], result.loc[result["split"].eq("validation"), "anomaly_score"].quantile(.99))
        self.assertTrue(np.isfinite(fitted["X_train"]).all())
        with self.assertRaises(ValueError):
            fit_anomaly_baseline(frame, ["duration_ms"], ["transactionId"])

    def test_transaction_spanning_split_boundary_is_excluded(self):
        frame = pd.DataFrame({"source_period": ["a"] * 12,
                              "transactionId": [str(i) for i in range(10)] + ["boundary", "boundary"],
                              "startTime": list(pd.date_range("2026-01-01", periods=10)) + [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-08")]})
        split = chronological_split(frame)
        self.assertTrue(split.iloc[-2:].eq("excluded_boundary_transaction").all())


if __name__ == "__main__":
    unittest.main()
