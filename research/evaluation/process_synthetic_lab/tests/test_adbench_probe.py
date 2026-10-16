"""Independent tests for the bounded, train-only ADBench integration."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import numpy as np

from research.evaluation.process_synthetic_lab import adbench_probe as probe


def reference_generator(owner, values, labels, mode, alpha, percentage):
    """Test double: deliberately ignores placeholder rows when fitting."""
    reference = values[labels == 0]
    generated = np.random.normal(reference.mean(axis=0), 0.1, values.shape)
    return generated, labels.copy()


class AdbenchProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        rng = np.random.default_rng(819)
        self.train = rng.normal(size=(30, 3))
        self.test = rng.normal(size=(10, 3))

    def tearDown(self):
        self.temp.cleanup()

    def run_probe(self, name="run", **kwargs):
        return probe.run_probe(
            kwargs.pop("train", self.train), kwargs.pop("test", self.test),
            seed=kwargs.pop("seed", 24), output_dir=self.root / name, **kwargs)

    def test_heldout_values_never_enter_generator_fit(self):
        calls = []

        def capture(owner, values, labels, *args):
            calls.append((values.copy(), labels.copy()))
            return reference_generator(owner, values, labels, *args)

        with patch.object(probe, "_official_method", return_value=capture):
            first = self.run_probe("first")
            second = self.run_probe("second", test=np.full_like(self.test, 99999))
        self.assertEqual(first["status"], "completed")
        for values, labels in calls:
            np.testing.assert_array_equal(values[labels == 0], self.train)
            np.testing.assert_array_equal(values[labels == 1], 0)
        self.assertEqual([r["matrix_sha256"] for r in first["results"]],
                         [r["matrix_sha256"] for r in second["results"]])

    def test_reproducibility_labels_constants_and_rng_restoration(self):
        train = np.column_stack((self.train, np.full(30, 7)))
        test = np.column_stack((self.test, np.full(10, 99)))
        before = np.random.get_state()
        with patch.object(probe, "_official_method", return_value=reference_generator):
            first = self.run_probe("one", train=train, test=test)
            second = self.run_probe("two", train=train, test=test)
        after = np.random.get_state()
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])
        for a, b in zip(first["results"], second["results"]):
            self.assertEqual(a["matrix_sha256"], b["matrix_sha256"])
            self.assertEqual(a["label_sha256"], b["label_sha256"])
            data = np.load(self.root / "one" / a["artifact"])
            np.testing.assert_array_equal(data["X"][:, 3], 7)
            self.assertEqual(np.count_nonzero(data["y"] == 1), 10)
            self.assertEqual(np.count_nonzero(data["y"] == 0), 30)
        self.assertEqual(first["frozen_constant_feature_indices"], [3])

    def test_invalid_inputs_fail_before_source_download(self):
        cases = [
            (np.ones((30, 3)), self.test),
            (self.train[:8], self.test),
            (self.train, np.ones((10, 2))),
            (self.train, np.empty((0, 3))),
            (self.train.flatten(), self.test),
            (np.full((30, 3), np.nan), self.test),
            (self.train, np.full((10, 3), np.inf)),
        ]
        with patch.object(probe, "_official_method") as download:
            for i, (train, test) in enumerate(cases):
                with self.subTest(case=i):
                    result = self.run_probe(str(i), train=train, test=test)
                    self.assertEqual(result["status"], "unsupported_or_failed")
                    self.assertTrue(result["reason"])
                    self.assertEqual(json.loads((self.root / str(i) / "adbench-probe.json").read_text()), result)
            download.assert_not_called()

    def test_bounded_sampling_and_explicit_unsupported_dependency(self):
        rng = np.random.default_rng(18)
        with patch.object(probe, "_official_method", return_value=reference_generator):
            result = self.run_probe(train=rng.normal(size=(1500, 3)), test=rng.normal(size=(200, 3)))
        self.assertEqual(result["training_rows_used"], 1000)
        self.assertEqual(result["injected_count"], 100)
        self.assertEqual({r["generated_rows"] for r in result["results"]}, {1100})
        self.assertIn("dependency", result["unsupported_modes"])
        self.assertNotIn("dependency", [r["mode"] for r in result["results"]])

    def test_fixed_predictor_scores_all_modes_and_real_reference_without_refit(self):
        seen = []
        threshold = 0.2

        def predict(values):
            seen.append(values.copy())
            return values[:, 0] > threshold

        with patch.object(probe, "_official_method", return_value=reference_generator):
            result = self.run_probe(predictor=predict)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(seen), 4)
        np.testing.assert_array_equal(seen[-1], self.test)
        self.assertIn("unverified", result["heldout_reference_diagnostic"]["label_status"])
        for row, values in zip(result["results"], seen):
            self.assertEqual(row["status"], "scored")
            self.assertEqual(row["predictions"]["predicted_anomalies"], int((values[:, 0] > threshold).sum()))

    def test_bad_predictor_shape_numeric_flags_and_nan_are_never_success(self):
        bad = [lambda x: np.zeros((len(x), 1), dtype=bool),
               lambda x: np.zeros(len(x), dtype=int),
               lambda x: np.full(len(x), np.nan)]
        with patch.object(probe, "_official_method", return_value=reference_generator):
            for i, predictor in enumerate(bad):
                with self.subTest(case=i):
                    result = self.run_probe(str(i), predictor=predictor)
                    self.assertEqual(result["status"], "unsupported_or_failed")
                    self.assertTrue(all(r["status"] == "unsupported_or_failed" for r in result["results"]))

    def test_network_failure_is_persisted_and_does_not_fake_output(self):
        with patch.object(probe, "urlopen", side_effect=URLError("test network unavailable")):
            result = self.run_probe()
        self.assertEqual(result["status"], "unsupported_or_failed")
        self.assertIn("network unavailable", result["reason"])
        self.assertEqual(result["results"], [])
        self.assertEqual(list(self.root.rglob("*.npz")), [])
        self.assertTrue((self.root / "run" / "adbench-probe.json").exists())

    def test_hash_mismatch_rejects_cached_source_before_execution(self):
        cache = self.root / "cache"
        cache.mkdir()
        source = b"raise AssertionError('untrusted code executed')"
        (cache / f"adbench-{probe.COMMIT}-data_generator.py").write_bytes(source)
        self.assertNotEqual(hashlib.sha256(source).hexdigest(), probe.SOURCE_SHA256)
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            probe._official_method(cache)

    def test_one_mode_failure_is_partial_and_retains_other_evidence(self):
        def generate(owner, values, labels, mode, *args):
            if mode == "cluster":
                raise ValueError("test unsupported mixture")
            return reference_generator(owner, values, labels, mode, *args)

        with patch.object(probe, "_official_method", return_value=generate):
            result = self.run_probe()
        self.assertEqual(result["status"], "partial")
        self.assertEqual([r["status"] for r in result["results"]],
                         ["generated", "unsupported_or_failed", "generated"])
        self.assertIn("unsupported mixture", result["results"][1]["reason"])

    def test_existing_run_is_rejected_without_overwriting_artifacts(self):
        with patch.object(probe, "_official_method", return_value=reference_generator):
            self.run_probe()
            before = {p.relative_to(self.root): p.read_bytes()
                      for p in self.root.rglob("*") if p.is_file()}
            with self.assertRaises((FileExistsError, ValueError)):
                self.run_probe(seed=999)
            after = {p.relative_to(self.root): p.read_bytes()
                     for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
