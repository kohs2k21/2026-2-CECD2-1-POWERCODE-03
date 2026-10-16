"""Independent P04 positive-control and C00 preservation tests; no normal truth."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.evaluation.process_synthetic_lab.config import ExperimentConfig
from research.evaluation.process_synthetic_lab.controls import noop_report, reload_canonical_snapshot
from research.evaluation.process_synthetic_lab.data import frame_hash
from research.evaluation.process_synthetic_lab.model import FrozenModel
from research.evaluation.process_synthetic_lab.scenarios import generate, adapter_baselines
from research.evaluation.process_synthetic_lab.tests.test_core import fixture, cfg, write_sources
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.rules import evaluate_rules


class PositiveControlTests(unittest.TestCase):
    def setUp(self):
        self.frame = fixture(24)
        self.baselines = adapter_baselines(self.frame)

    def test_contract_opt_in_required(self):
        out = generate(self.frame, self.baselines, cfg(), scenario="P04", severity=0, seed=11)
        self.assertEqual(out.metadata["status"], "unsupported")
        self.assertTrue(out.truth.empty)
        self.assertEqual(frame_hash(out.frame), frame_hash(self.frame))

    def test_only_total_changes_and_exact_positive_control_labels(self):
        for severity, increment in enumerate((1, 2, 3)):
            with self.subTest(increment=increment):
                config = cfg(count_conservation_contract=True, parent_policy="propagate_parent_end")
                original = self.frame.copy(deep=True)
                out = generate(self.frame, self.baselines, config, scenario="P04", severity=severity, seed=23)
                changed = out.frame.totalCount.ne(original.totalCount)
                self.assertEqual(int(changed.sum()), config.target_rows)
                self.assertEqual(set(out.truth.row_id), set(original.loc[changed, "row_id"]))
                self.assertTrue((out.frame.loc[changed, "totalCount"] - original.loc[changed, "totalCount"]).eq(increment).all())
                pd.testing.assert_frame_equal(out.frame.drop(columns="totalCount"), original.drop(columns="totalCount"))
                pd.testing.assert_frame_equal(self.frame, original)
                self.assertEqual(set(out.changes.field), {"totalCount"})
                self.assertTrue(out.parent_changes.empty)
                self.assertTrue(out.frame.totalCount.ge(0).all())
                self.assertTrue(out.frame.totalCount.mod(1).eq(0).all())
                before, _ = evaluate_rules(original, PipelineConfig())
                after, _ = evaluate_rules(out.frame, PipelineConfig())
                self.assertFalse(before.loc[changed, "count_conservation"].any())
                self.assertTrue(after.loc[changed, "count_conservation"].all())
                pd.testing.assert_frame_equal(before.drop(columns="count_conservation"), after.drop(columns="count_conservation"))
                self.assertTrue(all(out.metadata["valid_other_invariants"].values()))

    def test_zero_count_is_valid_but_missing_invalid_and_float_limit_reject(self):
        for value, accepted in ((0, True), (np.nan, False), (-1, False), (1.5, False), (2**53 - 1, False), (float("inf"), False)):
            with self.subTest(value=value):
                test = fixture(1)
                test["totalCount"] = value
                test["successCount"] = value
                out = generate(test, {}, ExperimentConfig(target_rows=1, count_conservation_contract=True),
                               scenario="P04", severity=0, seed=11)
                self.assertEqual(len(out.truth), int(accepted))

    def test_unknown_adapter_does_not_require_duration_fit_for_count_control(self):
        self.frame["adapterType"] = "never-seen"
        out = generate(self.frame, {}, cfg(count_conservation_contract=True), scenario="P04", severity=0, seed=11)
        self.assertEqual(len(out.truth), 4)

    def test_missing_count_contract_never_generates_truth_or_crashes(self):
        for column in ("totalCount", "successCount", "errorCount"):
            with self.subTest(column=column):
                frame = fixture(1)
                frame.loc[0, column] = np.nan
                out = generate(frame, {}, ExperimentConfig(target_rows=1, count_conservation_contract=True),
                               scenario="P04", severity=0, seed=11)
                self.assertTrue(out.truth.empty)
                self.assertIn("count_contract_unverifiable", out.metadata["rejection_counts"])

    def test_seed_repeatability_without_detector_selection(self):
        args = (self.frame, self.baselines, cfg(count_conservation_contract=True))
        first = generate(*args, scenario="P04", severity=1, seed=47)
        second = generate(*args, scenario="P04", severity=1, seed=47)
        self.assertEqual(frame_hash(first.frame), frame_hash(second.frame))
        self.assertEqual(frame_hash(first.truth), frame_hash(second.truth))

    def test_p01_response_contract_remains_unresolved(self):
        self.frame["responseCode"] = "undocumented"
        out = generate(self.frame, self.baselines, cfg(failure_status="F", failure_semantics_evidence="fixture"),
                       scenario="P01", severity=0, seed=11)
        self.assertTrue(out.truth.empty)
        self.assertIn("failure_response_code_contract_missing", out.metadata["rejection_counts"])

    def test_invalid_new_configuration(self):
        for values in (dict(scenarios=()), dict(scenarios=("P04", "P04")), dict(scenarios=("C00",)),
                       dict(count_increments=(0,)), dict(count_increments=(True,)), dict(count_increments=(2**53,)),
                       dict(run_noop_control="yes"), dict(count_conservation_contract=1)):
            with self.subTest(values=values), self.assertRaises((ValueError, TypeError)):
                ExperimentConfig(**values)


class NoopControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = fixture(160)
        cls.model = FrozenModel.fit(frame.iloc[:100], frame.iloc[100:130], cfg())

    def test_roundtrip_preserves_leading_zeros_missing_and_invalid_diagnostics(self):
        frame = fixture(8)
        frame["transactionId"] = [f"000{i}" for i in range(8)]
        frame["row_id"] = [f"000{i}" for i in range(8)]
        frame.loc[2, "endTime"] = pd.NaT
        frame.loc[2, "duration_ms"] = np.nan
        frame.loc[2, "_invalid_endTime"] = True
        baseline = self.model.predict(frame).iloc[::-1].reset_index(drop=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canonical.csv"
            frame.to_csv(path, index=False)
            before = path.read_bytes()
            report, actual = noop_report(path, baseline, self.model, expected_frame=frame)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["truth_rows_created"], 0)
            self.assertNotIn("false_positive_rate", report)
            self.assertEqual(set(actual.row_id), set(frame.row_id))
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(report["scores_exactly_equal"])
            self.assertTrue(all(report["canonical_field_equal"].values()))

    def test_recorded_invalid_flag_corruption_rejected(self):
        frame = fixture(8)
        frame["_invalid_endTime"] = "not-a-bool"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "diagnostic"):
                reload_canonical_snapshot(path)

    def test_actual_timestamp_change_is_rejected_not_hidden_by_unit_normalization(self):
        for nanoseconds in (1, 1000000):
            with self.subTest(nanoseconds=nanoseconds):
                frame = fixture(8)
                original = frame.copy(deep=True)
                baseline = self.model.predict(original)
                frame["endTime"] = frame.endTime.astype("datetime64[ns]")
                frame.loc[0, "endTime"] += pd.Timedelta(nanoseconds=nanoseconds)
                frame.loc[0, "duration_ms"] += nanoseconds / 1000000
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "changed.csv"
                    frame.to_csv(path, index=False)
                    report, _ = noop_report(path, baseline, self.model, expected_frame=original)
                    self.assertEqual(report["status"], "failed")
                    self.assertFalse(report["canonical_field_equal"]["endTime"])

    def test_changed_score_is_not_accepted_as_noop(self):
        frame = fixture(8)
        baseline = self.model.predict(frame)
        baseline["ml_score"] += .01
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canonical.csv"
            frame.to_csv(path, index=False)
            report, _ = noop_report(path, baseline, self.model, expected_frame=frame)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["scores_exactly_equal"])

    def test_baseline_identity_mismatch_rejected(self):
        frame = fixture(8)
        baseline = self.model.predict(frame).iloc[1:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canonical.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                noop_report(path, baseline, self.model, expected_frame=frame)

    def test_method_validation_cli_writes_noop_and_p04_without_normal_labels(self):
        runs = Path(__file__).resolve().parents[1] / "runs"
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            folder = Path(tmp)
            process, parent = write_sources(folder, fixture())
            config = cfg(scenarios=("P04",), count_increments=(1,), count_conservation_contract=True,
                         run_noop_control=True, run_adbench=False, seeds=(11,))
            config_path = folder / "config.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            output = folder / "new-run"
            command = [sys.executable, "-m", "research.evaluation.process_synthetic_lab.run",
                       "--process", str(process), "--transactions", str(parent), "--config", str(config_path),
                       "--output", str(output), "--source-commit", "invented-fixture"]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "noop-control.json").read_text(encoding="utf-8"))
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            truth = pd.read_csv(output / "P04-severity1-seed11/truth.csv")
            self.assertEqual(report["truth_rows_created"], 0)
            self.assertEqual(manifest["noop_control_status"], "passed")
            self.assertEqual(manifest["successful_trials"], 1)
            self.assertTrue(truth.label.eq(1).all())
            self.assertFalse(manifest["verified_normal_FPR_available"])


if __name__ == "__main__":
    unittest.main()
