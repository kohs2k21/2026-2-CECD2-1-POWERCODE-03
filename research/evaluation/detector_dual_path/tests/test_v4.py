"""Causality and experiment invariants, independent of measured effectiveness."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

import numpy as np
import pandas as pd

from research.evaluation.detector_dual_path.v4 import select
from research.evaluation.detector_dual_path.v4_core import (
    build_role, History, read_config, row_frame, suppress, truth_at, window_frame)

CONFIG = Path(__file__).resolve().parents[1] / "v4_config.json"


class V4Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read_config(CONFIG)
        cls.train = build_role(451, "train", cls.config)
        cls.history = History.fit(cls.train, cls.config)

    def test_all_calendar_cohorts_independent_dates_and_gaps(self):
        self.assertEqual(len(self.history.support), 168)
        self.assertTrue(all(r["dates"] >= 2 and r["gaps"] >= 30 for r in self.history.support))
        self.assertEqual(sum(r["eligible_seconds"] for r in self.history.support), 336 * 240)

    def test_disjoint_temporal_roles_and_paired_sources(self):
        val = build_role(451, "validation", self.config, include_scenarios=True)
        test = build_role(451, "test", self.config, include_scenarios=True)
        self.assertLess(max(b.end for b in val), min(b.start for b in test))
        self.assertFalse({b.source_id for b in val} & {b.source_id for b in test})
        for scenario in ("input_stop", "arrival_burst", "individual_count", "group_delay",
                         "gradual_degradation", "mixed_hard", "count_contract"):
            self.assertEqual(sum(b.scenario == scenario and not b.control for b in test), 21)

    def test_future_completions_hidden_elapsed_start_only(self):
        block = self.train[0]
        clock = block.start + 900
        frame = window_frame(block, self.history, 60, clocks=[clock])
        seen = block.arrivals[block.arrivals < clock]
        self.assertAlmostEqual(frame.elapsed_seconds.iloc[0], clock - seen[-1])
        altered = replace(block, durations=block.durations + 10000,
                          completions=block.completions + 10000)
        changed = window_frame(altered, self.history, 60, clocks=[clock])
        self.assertEqual(changed.missing_duration.iloc[0], 1)
        self.assertEqual(changed.elapsed_seconds.iloc[0], frame.elapsed_seconds.iloc[0])

    def test_observation_controls_are_unavailable(self):
        blocks = build_role(451, "validation", self.config, include_scenarios=True)
        for scenario in ("business_closed", "collector_unknown", "publication_unknown", "low_frequency"):
            block = next(b for b in blocks if b.scenario == scenario)
            windows = window_frame(block, self.history, 60)
            self.assertFalse(windows.eligible.any())
            self.assertFalse(row_frame(block).eligible.any())

    def test_pure_arrival_burst_keeps_individual_marginals(self):
        blocks = build_role(451, "validation", self.config, include_scenarios=True)
        injected = next(b for b in blocks if b.scenario == "arrival_burst" and not b.control)
        normal = next(b for b in blocks if b.source_id == injected.source_id and b.control)
        self.assertTrue(set(injected.counts).issubset(set(normal.counts)))
        self.assertTrue(set(injected.durations).issubset(set(normal.durations)))
        self.assertFalse(injected.contract_invalid.any())

    def test_half_open_truth_and_recovery_not_hit(self):
        block = replace(self.train[0], control=False, anomaly_start=1000., anomaly_end=1300.)
        q, state = truth_at(block, [1000, 1060, 1300, 1600])
        np.testing.assert_allclose(q, [0, .2, 1, 0])
        self.assertEqual(state.tolist(), ["antecedent", "active", "recovery", "clean"])

    def test_scenario_recovery_has_clean_overlap_timer(self):
        blocks = build_role(451, "validation", self.config, include_scenarios=True)
        block = next(b for b in blocks if b.scenario == "group_delay" and not b.control)
        frame = window_frame(block, self.history, 60)
        self.assertGreater(frame.clock.iloc[-1], block.anomaly_end + 300)

    def test_union_budget_and_misses_in_selection(self):
        self.assertEqual(suppress([1, 1, 20, 60, 61, 122]), [1, 61, 122])
        candidate = {"candidate_id": "x", "normal_alerts_per_hour": .1,
            "event_recall": .7, "penalized_delay_seconds": 20, "cost_units": 1}
        self.assertFalse(select([candidate], .1, .2)["eligible"])
        other = {**candidate, "candidate_id": "a", "penalized_delay_seconds": 30}
        self.assertEqual(select([other, candidate], .1, .05)["candidate_id"], "x")


class V4LoadReleaseContracts(unittest.TestCase):
    def setUp(self):
        from research.evaluation.detector_dual_path.v4_load import measurement_release
        self.release = measurement_release
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.outputs = {}
        self.contracts = {}
        self.warm = {"pending_after_drain": 0, **{
            path: {"complete": 1, "scheduled_jobs": 1, "failed_or_unprocessed": 0}
            for path in ("realtime", "trend")}}
        for group in "ABCD":
            folder = self.root / group
            folder.mkdir()
            self.outputs[group] = str(folder)
            contract = {"group": group, "trace_sha256": "same-trace", "ready_snapshot_sha256": "same-ready"}
            self.contracts[group] = contract
            (folder / "ready_contract.json").write_text(json.dumps(contract), encoding="utf-8")
            # Peer readiness must not read a contract that its owner updates.
            (folder / "contract.json").write_text("mutable contract is not readiness evidence", encoding="utf-8")
            (folder / "warmup.json").write_text(json.dumps(self.warm), encoding="utf-8")
            (folder / "ready.flag").write_text("ready", encoding="utf-8")
        self.path = self.root / "release.json"
        self.epoch = time.time() + 60
        self.path.write_text(json.dumps({"measurement_epoch_utc": self.epoch, "ready_outputs": self.outputs}), encoding="utf-8")

    def test_immutable_ready_contract_accepts_without_mutable_contract(self):
        for folder in self.outputs.values():
            (Path(folder) / "contract.json").unlink()
        epoch, release = self.release(self.path, "same-trace", "same-ready", .01)
        self.assertEqual(epoch, self.epoch)
        self.assertEqual(release["ready_outputs"], self.outputs)

    def test_ready_contract_mismatch_rejected(self):
        path = Path(self.outputs["D"]) / "ready_contract.json"
        for key in ("group", "trace_sha256", "ready_snapshot_sha256"):
            with self.subTest(key=key):
                path.write_text(json.dumps({**self.contracts["D"], key: "wrong"}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.release(self.path, "same-trace", "same-ready", .01)

    def test_missing_snapshot_does_not_fall_back_to_mutable_contract(self):
        folder = Path(self.outputs["D"])
        (folder / "contract.json").write_text(json.dumps(self.contracts["D"]), encoding="utf-8")
        (folder / "ready_contract.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.release(self.path, "same-trace", "same-ready", .01)

    def test_missing_ready_and_unclean_warmup_rejected(self):
        folder = Path(self.outputs["D"])
        (folder / "ready.flag").unlink()
        with self.assertRaises(RuntimeError):
            self.release(self.path, "same-trace", "same-ready", .01)
        (folder / "ready.flag").write_text("ready", encoding="utf-8")
        for key, value in (("complete", 0), ("failed_or_unprocessed", 1)):
            with self.subTest(key=key):
                warm = {**self.warm, "realtime": {**self.warm["realtime"], key: value}}
                (folder / "warmup.json").write_text(json.dumps(warm), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.release(self.path, "same-trace", "same-ready", .01)
        (folder / "warmup.json").write_text(json.dumps({**self.warm, "pending_after_drain": 1}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.release(self.path, "same-trace", "same-ready", .01)

    def test_late_and_missing_release_rejected(self):
        self.path.write_text(json.dumps({"measurement_epoch_utc": time.time(), "ready_outputs": self.outputs}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.release(self.path, "same-trace", "same-ready", .01)
        self.path.unlink()
        with self.assertRaises(RuntimeError):
            self.release(self.path, "same-trace", "same-ready", .01)


if __name__ == "__main__":
    unittest.main()
