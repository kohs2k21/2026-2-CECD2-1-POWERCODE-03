"""Independent adversarial checks; all fixture identities are invented."""
import copy
import tempfile
import unittest
from unittest.mock import patch
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.evaluation.process_synthetic_lab.config import ExperimentConfig
from research.evaluation.process_synthetic_lab.data import frame_hash, load_dataset, row_ids, split_frames
from research.evaluation.process_synthetic_lab.evaluate import PATHS, control_report, score_rows, paired_score_rows
from research.evaluation.process_synthetic_lab.model import FrozenModel
from research.evaluation.process_synthetic_lab.scenarios import adapter_baselines, generate
from process_anomaly_pipeline.data import prepare


def fixture(n=120, paired=False):
    records = []
    for i in range(n):
        start = pd.Timestamp("2025-01-01") + pd.Timedelta(minutes=i)
        records.append(dict(source_period="invented", transactionId=f"tx-{i // 2 if paired else i}",
                            processId=f"step-{i}", processHubId="hub", retryCount=0,
                            dependProcessId=None, adapterType="A" if i % 2 == 0 else "B", channelId="channel",
                            status="S", startTime=start, endTime=start + pd.Timedelta(milliseconds=100 + i % 19),
                            totalCount=10 + i % 7, successCount=10 + i % 7, errorCount=0, responseCode=None))
    frame = prepare(pd.DataFrame(records))
    frame["parentStartTime"] = frame.startTime - pd.Timedelta(minutes=1)
    frame["parentEndTime"] = frame.endTime + pd.Timedelta(minutes=5)
    frame["parentInterfaceId"] = "interface"
    frame["row_id"] = row_ids(frame)
    return frame


def cfg(**kwargs):
    return ExperimentConfig(min_train_rows=20, min_threshold_rows=10, min_adapter_rows=5,
                            target_rows=4, **kwargs)


def write_sources(folder, frame):
    process = folder / "process.csv"
    transaction = folder / "transaction.csv"
    frame.drop(columns=["parentStartTime", "parentEndTime", "parentInterfaceId", "row_id"]).to_csv(process, index=False)
    parents = frame[["source_period", "transactionId", "processHubId", "retryCount"]].copy()
    parents["startTime"] = frame.parentStartTime
    parents["endTime"] = frame.parentEndTime
    parents["interfaceId"] = "interface"
    parents["status"] = "S"
    parents["processCount"] = 1
    parents.to_csv(transaction, index=False)
    return process, transaction


class ConfigTests(unittest.TestCase):
    def test_invalid_values_fail_before_experiment(self):
        invalid = [dict(target_rows=0), dict(seeds=(1, 1)), dict(seeds=(-1,)),
                   dict(delay_multipliers=(1,)), dict(delay_multipliers=(float("nan"),)),
                   dict(train_fraction=.9, validation_fraction=.2), dict(threshold_quantile=1),
                   dict(failure_status="F"), dict(failure_status="unknown"), dict(run_adbench="yes")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises((ValueError, TypeError)):
                ExperimentConfig(**item)


class DataTests(unittest.TestCase):
    def test_chronological_split_keeps_transactions_and_purges_crossings(self):
        frame = fixture(paired=True)
        frame.loc[0, "endTime"] = frame.loc[100, "endTime"]
        parts, audit = split_frames(frame, cfg())
        sets = [set(p.transactionId) for p in parts.values()]
        for i, first in enumerate(sets):
            for second in sets[i + 1:]:
                self.assertFalse(first & second)
        self.assertTrue(all("tx-0" not in s for s in sets))
        self.assertEqual(audit["transaction_overlap_count"], 0)
        self.assertLess(parts["train"].endTime.max(), parts["threshold"].startTime.min())
        self.assertLess(parts["threshold"].endTime.max(), parts["test"].startTime.min())

    def test_parent_bounds_loaded_and_sources_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_sources(Path(tmp), fixture())
            before = [p.read_bytes() for p in paths]
            all_rows, _, audit = load_dataset(*paths, cfg())
            self.assertEqual([p.read_bytes() for p in paths], before)
            self.assertEqual(audit["parent_join"]["process_rows_without_parent_bounds"], 0)
            self.assertTrue(all_rows.parentStartTime.le(all_rows.startTime).all())

    def test_linked_templates_cross_split_rejected(self):
        frame = fixture()
        frame["source_template_id"] = [f"template-{i}" for i in range(len(frame))]
        frame["parent_template_id"] = pd.NA
        frame.loc[110, "parent_template_id"] = "template-1"
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_sources(Path(tmp), frame)
            with self.assertRaisesRegex(ValueError, "leakage"):
                load_dataset(*paths, cfg())

    def test_duplicate_process_identity_rejected(self):
        frame = fixture()
        frame.loc[1, ["transactionId", "processId", "retryCount"]] = frame.loc[0, ["transactionId", "processId", "retryCount"]]
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_sources(Path(tmp), frame)
            with self.assertRaisesRegex(ValueError, "Duplicate PROCESS"):
                load_dataset(*paths, cfg())


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.train = fixture(60)
        self.test = fixture(30)
        self.baselines = adapter_baselines(self.train)

    def test_p01_requires_semantics_and_preserves_counts(self):
        unsupported = generate(self.test, self.baselines, cfg(), scenario="P01", severity=0, seed=11)
        self.assertEqual(unsupported.metadata["status"], "unsupported")
        self.assertEqual(len(unsupported.truth), 0)
        accepted = generate(self.test, self.baselines, cfg(failure_status="F", failure_semantics_evidence="fixture contract"),
                            scenario="P01", severity=2, seed=11)
        self.assertEqual(len(accepted.truth), 4)
        mutated = accepted.frame.loc[accepted.frame.row_id.isin(accepted.truth.row_id)]
        self.assertTrue(mutated.status.eq("F").all())
        self.assertTrue(mutated.errorCount.gt(0).all())
        self.assertTrue(mutated.totalCount.eq(mutated.successCount + mutated.errorCount).all())

    def test_p01_unknown_response_mapping_rejects(self):
        self.test["responseCode"] = "unmapped-success-code"
        result = generate(self.test, self.baselines, cfg(failure_status="F", failure_semantics_evidence="fixture contract"),
                          scenario="P01", severity=0, seed=11)
        self.assertEqual(len(result.truth), 0)
        self.assertIn("failure_response_code_contract_missing", result.metadata["rejection_counts"])

    def test_delay_changes_exact_rows_immutable_reproducible(self):
        before = frame_hash(self.test)
        results = [generate(self.test, self.baselines, cfg(), scenario="P02", severity=1, seed=23) for _ in range(2)]
        self.assertEqual(frame_hash(self.test), before)
        self.assertEqual(frame_hash(results[0].frame), frame_hash(results[1].frame))
        self.assertEqual(frame_hash(results[0].truth), frame_hash(results[1].truth))
        result = results[0]
        changed = result.frame.endTime.ne(self.test.endTime)
        self.assertEqual(set(result.frame.loc[changed, "row_id"]), set(result.truth.row_id))
        self.assertEqual(int(changed.sum()), 4)
        self.assertTrue(result.frame.endTime.le(result.frame.parentEndTime).all())
        self.assertTrue(result.frame.duration_ms.eq((result.frame.endTime - result.frame.startTime).dt.total_seconds() * 1000).all())

    def test_insufficient_parent_headroom_is_rejected_not_extended(self):
        self.test["parentEndTime"] = self.test.endTime
        result = generate(self.test, self.baselines, cfg(), scenario="P02", severity=2, seed=11)
        self.assertEqual(len(result.truth), 0)
        pd.testing.assert_frame_equal(result.frame, self.test)

    def test_dependency_start_caps_delay(self):
        self.test.loc[1, ["transactionId", "adapterType"]] = self.test.loc[0, ["transactionId", "adapterType"]]
        self.test.loc[1, "dependProcessId"] = self.test.loc[0, "processId"]
        self.test.loc[1, "startTime"] = self.test.loc[0, "endTime"]
        result = generate(self.test, self.baselines, ExperimentConfig(target_rows=30), scenario="P02", severity=2, seed=11)
        self.assertNotIn(self.test.loc[0, "row_id"], set(result.truth.row_id))
        self.assertEqual(result.frame.loc[0, "endTime"], self.test.loc[0, "endTime"])

    def test_unknown_dependency_start_cannot_fall_back_to_parent_limit(self):
        self.test.loc[1, "transactionId"] = self.test.loc[0, "transactionId"]
        self.test.loc[1, "dependProcessId"] = self.test.loc[0, "processId"]
        self.test.loc[1, "startTime"] = pd.NaT
        result = generate(self.test, self.baselines, ExperimentConfig(target_rows=30), scenario="P02", severity=2, seed=11)
        self.assertNotIn(self.test.loc[0, "row_id"], set(result.truth.row_id))
        self.assertEqual(result.frame.loc[0, "endTime"], self.test.loc[0, "endTime"])

    def test_p03_is_contiguous_within_adapter_interface(self):
        result = generate(self.test, self.baselines, cfg(), scenario="P03", severity=1, seed=47)
        selected = result.frame.loc[result.frame.row_id.isin(result.truth.row_id)]
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected.adapterType.nunique(), 1)
        timeline = result.frame.loc[result.frame.adapterType.eq(selected.adapterType.iloc[0])].sort_values("startTime").row_id.tolist()
        positions = sorted(timeline.index(v) for v in selected.row_id)
        self.assertEqual(positions, list(range(positions[0], positions[0] + 4)))

    def test_p03_block_all_or_nothing(self):
        self.test["parentEndTime"] = self.test.endTime
        result = generate(self.test, self.baselines, cfg(), scenario="P03", severity=0, seed=11)
        self.assertEqual(len(result.truth), 0)
        self.assertEqual(frame_hash(result.frame), frame_hash(self.test))

    def test_parent_propagation_uses_max_child_end_and_separate_journal(self):
        test = fixture(3)
        test["transactionId"] = test.loc[0, "transactionId"]
        test["startTime"] = test.loc[0, "startTime"]
        test["endTime"] = test.startTime + pd.to_timedelta([100, 130, 140], unit="ms")
        test["duration_ms"] = (test.endTime - test.startTime).dt.total_seconds() * 1000
        test["parentStartTime"] = test.startTime.min()
        test["parentEndTime"] = test.endTime.max()
        test["row_id"] = row_ids(test)
        before = test.copy(deep=True)
        config = ExperimentConfig(target_rows=2, parent_policy="propagate_parent_end")
        result = generate(test, self.baselines, config, scenario="P02", severity=2, seed=11)
        self.assertEqual(len(result.truth), 2)
        self.assertEqual(len(result.parent_changes), 1)
        self.assertEqual(result.metadata["accepted_rows"], 2)
        self.assertEqual(result.parent_changes.iloc[0].affected_process_rows, 3)
        self.assertTrue(result.frame.parentEndTime.eq(result.frame.endTime.max()).all())
        self.assertEqual(pd.Timestamp(result.parent_changes.iloc[0]["before"]), before.parentEndTime.iloc[0])
        self.assertEqual(pd.Timestamp(result.parent_changes.iloc[0]["after"]), result.frame.endTime.max())
        changed = result.frame.endTime.ne(test.endTime)
        self.assertEqual(set(result.truth.row_id), set(test.loc[changed, "row_id"]))
        self.assertEqual(int((~changed).sum()), 1)
        self.assertNotIn("parentEndTime", set(result.changes.field))
        pd.testing.assert_frame_equal(test, before)

    def test_p03_cannot_form_block_across_source_periods(self):
        test = fixture(6)
        test["adapterType"] = "A"
        test.loc[3:, "source_period"] = "other-source"
        result = generate(test, self.baselines, cfg(parent_policy="propagate_parent_end"), scenario="P03", severity=1, seed=11)
        self.assertEqual(len(result.truth), 0)

    def test_parent_propagation_keeps_known_and_unknown_dependency_bounds(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                test = fixture(2)
                test.loc[1, "transactionId"] = test.loc[0, "transactionId"]
                test.loc[1, "dependProcessId"] = test.loc[0, "processId"]
                test.loc[1, "startTime"] = pd.NaT if unknown else test.loc[0, "endTime"]
                config = ExperimentConfig(target_rows=2, parent_policy="propagate_parent_end")
                result = generate(test, self.baselines, config, scenario="P02", severity=2, seed=11)
                self.assertNotIn(test.loc[0, "row_id"], set(result.truth.row_id))
                self.assertEqual(result.frame.loc[0, "endTime"], test.loc[0, "endTime"])

    def test_parent_propagation_does_not_repair_existing_sibling_violation(self):
        test = fixture(2)
        test.loc[1, "transactionId"] = test.loc[0, "transactionId"]
        test.loc[1, "endTime"] = test.loc[1, "parentEndTime"] + pd.Timedelta(seconds=1)
        result = generate(test, self.baselines, ExperimentConfig(target_rows=2, parent_policy="propagate_parent_end"),
                          scenario="P02", severity=1, seed=11)
        self.assertEqual(len(result.truth), 0)
        self.assertEqual(len(result.parent_changes), 0)


class ScoringTests(unittest.TestCase):
    def test_missing_predictions_remain_positive_denominator(self):
        truth = pd.DataFrame({"row_id": ["one", "two", "three"], "label": [1, 1, 1]})
        predictions = pd.DataFrame({"row_id": ["one", "two", "unknown"], **{p: [True, False, True] for p in PATHS}})
        for result in score_rows(truth, predictions):
            self.assertEqual(result["anomaly_rows"], 3)
            self.assertEqual(result["detection_rate"], 1 / 3)
            self.assertEqual(result["anomaly_unjudged_rows"], 1)
            self.assertEqual(result["verified_normal_rows"], 0)
            self.assertIsNone(result["false_positive_rate"])

    def test_unknown_truth_cannot_be_treated_as_normal(self):
        predictions = pd.DataFrame({"row_id": ["unknown"], **{p: [True] for p in PATHS}})
        with self.assertRaisesRegex(ValueError, "labels"):
            score_rows(pd.DataFrame({"row_id": ["unknown"], "label": [np.nan]}), predictions)

    def test_duplicate_predictions_rejected(self):
        predictions = pd.DataFrame({"row_id": ["a", "a"], **{p: [True, False] for p in PATHS}})
        with self.assertRaises(ValueError):
            score_rows(pd.DataFrame({"row_id": ["a"], "label": [1]}), predictions)

    def test_wrong_prediction_identity_cannot_count_as_hit(self):
        predictions = pd.DataFrame({"row_id": ["unrelated"], **{p: [True] for p in PATHS}})
        for report in score_rows(pd.DataFrame({"row_id": ["expected"], "label": [1]}), predictions):
            self.assertEqual(report["detection_rate"], 0)
            self.assertEqual(report["anomaly_unjudged_rows"], 1)

    def test_control_report_joins_predictions_by_identity(self):
        frame = fixture(2)
        frame.loc[1, "status"] = "F"
        predictions = pd.DataFrame({"row_id": frame.row_id.iloc[::-1].to_list(), **{p: [True, False] for p in PATHS}})
        for report in control_report(frame, predictions).values():
            self.assertEqual(report["successful_proxy_alert_rate"], 0)

    def test_proxy_control_alerts_never_reported_as_false_positive_rate(self):
        frame = fixture(10)
        predictions = pd.DataFrame({"row_id": frame.row_id, **{p: True for p in PATHS}})
        reports = control_report(frame, predictions)
        for report in reports.values():
            self.assertIsNone(report["false_positive_rate"])
            self.assertEqual(report["verified_normal_rows"], 0)
            self.assertEqual(report["unlabelled_alert_rows"], len(frame))


class ModelTests(unittest.TestCase):
    def test_unseen_adapter_uses_frozen_global_and_test_does_not_refit(self):
        frame = fixture(160)
        model = FrozenModel.fit(frame.iloc[:100], frame.iloc[100:130], cfg())
        before = copy.deepcopy((model.adapter_stats, model.global_stats, model.threshold))
        test = frame.iloc[130:].copy()
        test["adapterType"] = "never-seen"
        features = model.feature_frame(test, model.adapter_stats, model.global_stats)
        self.assertTrue(features.adapter_frequency.eq(0).all())
        self.assertTrue(features.adapter_baseline_missing.eq(1).all())
        first = model.predict(test)
        second = model.predict(test)
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(before, (model.adapter_stats, model.global_stats, model.threshold))
        self.assertTrue(np.isfinite(model.transform(test)).all())


class PairedScoringTests(unittest.TestCase):
    def test_full_cli_automatically_writes_paired_reports(self):
        runs = Path(__file__).resolve().parents[1] / "runs"
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            folder = Path(tmp)
            process, parent = write_sources(folder, fixture())
            config_path = folder / "config.json"
            config_path.write_text(json.dumps(cfg(seeds=(11,), delay_multipliers=(1.5,), run_adbench=False,
                                                  failure_status="F", failure_semantics_evidence="invented fixture contract").to_dict()))
            output = folder / "new-run"
            result = subprocess.run([sys.executable, "-m", "research.evaluation.process_synthetic_lab.run",
                                     "--process", str(process), "--transactions", str(parent), "--config", str(config_path),
                                     "--output", str(output), "--source-commit", "invented-fixture"],
                                    cwd=Path(__file__).resolve().parents[4], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["successful_trials"], 3)
            paired = json.loads((output / "paired-results.json").read_text(encoding="utf-8"))
            self.assertEqual({row["scenario"] for row in paired}, {"P01", "P02", "P03"})
            self.assertTrue((output / "paired-results.csv").is_file())
            self.assertTrue(manifest["source_inputs_unchanged"])
            self.assertTrue(manifest["source_code_unchanged"])

    def test_all_transitions_and_unjudged_are_separate(self):
        ids = ["persistent", "new", "lost", "neither", "unknown-before", "missing-post", "normal"]
        truth = pd.DataFrame({"row_id": ids, "label": [1, 1, 1, 1, 1, 1, 0]})
        before = pd.DataFrame({"row_id": ids, **{p: [True, False, True, False, pd.NA, False, False] for p in PATHS}})
        after = pd.DataFrame({"row_id": ids[:5] + ["normal"], **{p: [True, True, False, False, True, True] for p in PATHS}})
        for row in paired_score_rows(truth, before.iloc[::-1], after.iloc[::-1]):
            self.assertEqual(row["injected_anomaly_rows"], 6)
            self.assertEqual(row["baseline_alert_rows"], 2)
            self.assertEqual(row["post_alert_rows"], 3)
            self.assertEqual(row["newly_detected_rows"], 1)
            self.assertEqual(row["persistent_alert_rows"], 1)
            self.assertEqual(row["lost_alert_rows"], 1)
            self.assertEqual(row["unchanged_nonalert_rows"], 1)
            self.assertEqual(row["paired_assessed_rows"], 4)
            self.assertEqual(row["paired_unjudged_rows"], 2)

    def test_duplicate_identity_rejected_in_each_input(self):
        truth = pd.DataFrame({"row_id": ["a"], "label": [1]})
        pred = pd.DataFrame({"row_id": ["a"], **{p: [True] for p in PATHS}})
        for index in range(3):
            args = [truth, pred, pred]
            args[index] = pd.concat([args[index], args[index]])
            with self.subTest(input=index), self.assertRaises(ValueError):
                paired_score_rows(*args)

    def test_mismatched_baseline_registry_rejected(self):
        truth = pd.DataFrame({"row_id": ["a"], "label": [1]})
        pred = pd.DataFrame({"row_id": ["a"], **{p: [True] for p in PATHS}})
        alien = pred.copy()
        alien["row_id"] = "not-baseline"
        with self.assertRaisesRegex(ValueError, "baseline cohort"):
            paired_score_rows(truth, alien, pred)
        with self.assertRaisesRegex(ValueError, "baseline cohort"):
            paired_score_rows(truth, pred, alien)

    def test_postprocessor_rejects_unfinished_source(self):
        from research.evaluation.process_synthetic_lab.paired_report import build_report
        runs = Path(__file__).resolve().parents[1] / "runs"
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            folder = Path(tmp)
            (folder / "manifest.json").write_text('{"status":"running"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "completed"):
                build_report(folder, runs / "must-not-create-paired-output")

    def test_postprocessor_rejects_overwriting_source(self):
        from research.evaluation.process_synthetic_lab.paired_report import build_report
        runs = Path(__file__).resolve().parents[1] / "runs"
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            with self.assertRaisesRegex(ValueError, "separate"):
                build_report(tmp, Path(tmp) / "child")


class OutputBoundaryTests(unittest.TestCase):
    def test_source_change_during_run_invalidates_manifest(self):
        from research.evaluation.process_synthetic_lab.run import run
        runs = Path(__file__).resolve().parents[1] / "runs"
        runs.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            paths = write_sources(Path(tmp), fixture())
            output = Path(tmp) / "new-run"
            with patch("research.evaluation.process_synthetic_lab.run.source_hashes", side_effect=[{"module": "before"}, {"module": "after"}]):
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    run(*paths, output, cfg(seeds=(11,), delay_multipliers=(1.5,), run_adbench=False), source_commit="fixture")
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["source_code_unchanged"])
            self.assertIn("invalidated", manifest["status"])

    def test_outside_output_rejected_before_input_access(self):
        from research.evaluation.process_synthetic_lab.run import run
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "must-not-exist"
            with self.assertRaisesRegex(ValueError, "ignored runs"):
                run("missing-process", "missing-parent", output, cfg(), source_commit="fixture")
            self.assertFalse(output.exists())

    def test_existing_run_refused_without_overwriting(self):
        from research.evaluation.process_synthetic_lab.run import run
        runs = Path(__file__).resolve().parents[1] / "runs"
        runs.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runs) as tmp:
            sentinel = Path(tmp) / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                run("missing-process", "missing-parent", tmp, cfg(), source_commit="fixture")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
