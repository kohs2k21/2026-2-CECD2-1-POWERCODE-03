"""Independent receipt/configuration checks; no detector performance execution."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research.evaluation.detector_dual_path.manifest import (
    ROOT, create_manifest, digest, file_digest, load_config,
)

CONFIG_PATH = ROOT / "research/evaluation/detector_dual_path/config.json"


class ManifestTests(unittest.TestCase):
    def config(self, changes=None):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config.update(changes or {})
        return config

    def load_modified(self, changes):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(self.config(changes)), encoding="utf-8")
            return load_config(path)

    def test_config_exact_default(self):
        self.assertEqual(load_config(CONFIG_PATH), self.config())

    def test_invalid_config_rejected(self):
        cases = [
            {"cases_per_scenario": True}, {"cases_per_scenario": 0},
            {"performance_jobs": -1}, {"warmup": False}, {"seed": False},
            {"candidate_strides": [5, 5]}, {"candidate_windows": []},
            {"candidate_strides": [60, -1]}, {"input_rate_hz": True},
            {"input_rate_hz": float("nan")}, {"input_rate_hz": float("inf")},
            {"performance_repeats": 2}, {"n_estimators": 2.5},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load_modified(change)

    def test_manifest_dataset_actual_content_and_repeatable(self):
        from research.evaluation.detector_dual_path.scenarios import build_suite, normal_history_observations
        config = load_config(CONFIG_PATH)
        first = create_manifest(config)
        second = create_manifest(config)
        self.assertEqual(first["dataset_partitions"], second["dataset_partitions"])
        self.assertEqual(first["prior_history_sha256"], second["prior_history_sha256"])
        episodes = build_suite(config["seed"], config["cases_per_scenario"])
        templates = {}
        for split in ("train", "calibration", "threshold", "validation", "test"):
            selected = [episode for episode in episodes if episode.split == split]
            self.assertGreater(len(selected), 0)
            self.assertEqual(first["dataset_partitions"][split]["episodes"], len(selected))
            self.assertEqual(first["dataset_partitions"][split]["sha256"], digest([asdict(episode) for episode in selected]))
            templates[split] = {episode.source_template_id for episode in selected}
        names = list(templates)
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                self.assertFalse(templates[left] & templates[right])
        self.assertEqual(first["prior_history_sha256"], digest([asdict(observation) for observation in normal_history_observations(config["seed"])]))
        changed = deepcopy(config)
        changed["seed"] += 1
        different = create_manifest(changed)
        for split in names:
            self.assertNotEqual(first["dataset_partitions"][split]["sha256"], different["dataset_partitions"][split]["sha256"])

    def test_manifest_versions_hashes_and_limits(self):
        config = load_config(CONFIG_PATH)
        receipt = create_manifest(config)
        self.assertEqual(receipt["config_sha256"], digest(config))
        self.assertEqual(receipt["seed"], config["seed"])
        self.assertEqual(receipt["status"], "prepared")
        self.assertFalse(receipt["original_customer_benchmark_available"])
        self.assertEqual(receipt["git"]["branch"], "feat/45-detector-lab")
        self.assertEqual(len(receipt["git"]["head"]), 40)
        self.assertEqual(receipt["environment"]["python"], "3.12.14")
        self.assertEqual(len(receipt["environment"]["packages"]), 6)
        self.assertEqual(len(receipt["published_models"]), 3)
        for model in receipt["published_models"]:
            self.assertEqual(model["sha256"], file_digest(ROOT / model["path"]))
        self.assertEqual(receipt["source_sha256"], digest(receipt["sources"]))
        for source, expected in receipt["sources"].items():
            self.assertEqual(file_digest(ROOT / source), expected)

    def test_cli_missing_trust_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new-run"
            completed = subprocess.run([sys.executable, "-m", "research.evaluation.detector_dual_path", "--output", str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Explicit --trust-published-models required", completed.stderr)
            self.assertFalse(output.exists())

    def test_cli_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinel = output / "existing.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            completed = subprocess.run([sys.executable, "-m", "research.evaluation.detector_dual_path", "--output", str(output), "--trust-published-models"], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("FileExistsError", completed.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertFalse((output / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
