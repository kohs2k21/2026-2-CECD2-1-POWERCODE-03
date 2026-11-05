"""Behavioral regression of source binding, missingness, frozen inference and OR."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.integration.feature_rule_ml.common import (
    SPEC, InputFailure, NONWARNING, UNASSESSED, WARNING,
    combine, load_pack, read, seal, write,
)
from research.integration.feature_rule_ml.demo import artificial_capture
from research.integration.feature_rule_ml.spec_bridge import prepare, transform
from research.integration.feature_rule_ml.ml_bridge import (
    fit, inspect_published, load_model, matrix, score_group, signature,
)
from research.integration.feature_rule_ml.runner import predict


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp_parent = Path(__file__).resolve().parents[1] / "runs"
        temp_parent.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="feature-ml-test-", dir=temp_parent)
        cls.root = Path(cls.tmp.name)
        cls.catalog = cls.root / "catalog"
        cls.public = prepare(SPEC / "examples/generated/public_delivery",
                             SPEC / "examples/delivery/suite.json", cls.catalog)
        cls.packs = {}
        for role in ("train", "calibration", "threshold", "application"):
            path = artificial_capture(cls.root / "captures" / role, role, 24)
            dest = cls.root / "packs" / role
            transform(cls.catalog, path, dest)
            cls.packs[role] = dest
        cls.train = load_pack(cls.packs["train"])["groups"][0]
        cls.gid = cls.train["id"]
        cls.model = cls.root / "model"
        fit(cls.packs["train"], cls.packs["calibration"], cls.packs["threshold"],
            cls.gid, ["ratio_value"], "if", cls.model)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return self.root / (self._testMethodName + "-" + name)

    def test_actual_saved_outputs_replayed_and_no_candidate_preserved(self):
        self.assertEqual(sum(len(g["columns"]) for g in self.public["groups"]), 7)
        self.assertEqual(len(self.public["holds"]), 1)
        self.assertEqual(self.public["new_LLM_calls"], 0)

    def test_cross_grain_never_silently_joined(self):
        tables = {g["grain"]["table"] for g in self.public["groups"]}
        self.assertGreater(len(tables), 1)
        for g in self.public["groups"]:
            self.assertTrue(all(c["proposal"]["plan"]["grain"] == g["grain"] for c in g["columns"]))

    def test_or_preserves_unknown_and_warning(self):
        self.assertEqual(combine([NONWARNING, UNASSESSED]), UNASSESSED)
        self.assertEqual(combine([WARNING, UNASSESSED]), WARNING)
        self.assertEqual(combine([NONWARNING] * 3), NONWARNING)
        self.assertEqual(combine([]), UNASSESSED)

    def test_missing_and_population_are_distinct_cells(self):
        group = copy.deepcopy(self.train)
        columns = [c["name"] for c in group["columns"] if c["alias"] == "ratio_value"]
        group["rows"][0]["features"][columns[0]] = {"state": "not_selected"}
        group["rows"][1]["features"][columns[0]] = {"state": "unavailable", "value": None}
        _, reasons = matrix(group, columns)
        self.assertIn("not_selected", reasons[0][0])
        self.assertIn("unavailable", reasons[1][0])

    def test_rule_execution_retains_hr_and_sr_trigger_sources(self):
        result = predict(self.catalog, self.out("rules"))
        hr = [r for g in result["groups"] for r in g["rows"] if r["HR"]["state"] == WARNING]
        sr = [r for g in result["groups"] for r in g["rows"] if r["SR"]["state"] == WARNING]
        self.assertTrue(hr)
        self.assertTrue(sr)
        self.assertTrue(all(r["HR"]["fired_rule_ids"] and r["source_row_key"] for r in hr))

    def test_model_missing_is_unassessable(self):
        result = predict(self.packs["application"], self.out("missing"))
        self.assertTrue(all(r["ML"]["state"] == UNASSESSED for r in result["groups"][0]["rows"]))

    def test_hr_authority_not_inherited_from_identical_schema(self):
        pack = load_pack(self.packs["application"])
        self.assertTrue(any(h["status"] == "authority_unassessable" for h in pack["holds"]))
        self.assertFalse(any(r["kind"] == "HR" for g in pack["groups"] for r in g["rules"]))

    def test_predict_never_fits_or_calls_llm(self):
        with patch("research.integration.feature_rule_ml.ml_bridge.RawModel.fit", side_effect=AssertionError("fit")), \
             patch("specmodel.local_models.LocalModels.generate", side_effect=AssertionError("LLM"), create=True):
            result = predict(self.packs["application"], self.out("frozen"), [self.model], True)
        rows = result["groups"][0]["rows"]
        self.assertEqual(rows[0]["ML"]["state"], UNASSESSED)
        self.assertIsNone(rows[0]["ML"]["methods"]["if"]["score"])
        self.assertEqual(result["new_ML_fit"], False)
        self.assertEqual(result["new_LLM_calls"], 0)
        self.assertTrue(any(r["ML"]["state"] == WARNING for r in rows[1:]))

    def test_serialized_model_needs_trust(self):
        with self.assertRaisesRegex(InputFailure, "trust-local-model"):
            load_model(self.model)

    def test_same_source_for_train_and_threshold_rejected(self):
        with self.assertRaises(InputFailure):
            fit(self.packs["train"], self.packs["calibration"], self.packs["train"],
                self.gid, ["ratio_value"], "if", self.out("badfit"))
        self.assertFalse(self.out("badfit").exists())

    def test_row_overlap_rejected_even_with_separate_split_names(self):
        pack = load_pack(self.packs["threshold"])
        for row, original in zip(pack["groups"][0]["rows"], self.train["rows"]):
            row["source_row_key"] = original["source_row_key"]
        dest = self.out("overlap")
        dest.mkdir()
        write(dest / "prepared.json", pack)
        seal(dest, "feature_pack")
        with self.assertRaisesRegex(InputFailure, "leaked"):
            fit(self.packs["train"], self.packs["calibration"], dest,
                self.gid, ["ratio_value"], "if", self.out("bad-fit"))

    def test_dependency_mismatch_rejected_before_unpickle(self):
        with patch("research.integration.feature_rule_ml.ml_bridge.dependencies", return_value={}), \
             patch("research.integration.feature_rule_ml.ml_bridge.joblib.load", side_effect=AssertionError("unpickle")):
            with self.assertRaisesRegex(InputFailure, "versions differ"):
                load_model(self.model, trusted=True)

    def test_schema_change_rejected_before_inference(self):
        manifest = read(self.root / "captures/application/capture.json")
        manifest["tables"][1]["columns"][0]["description"] = "changed schema"
        path = self.root / "captures/application/changed-schema.json"
        write(path, manifest)
        with self.assertRaisesRegex(InputFailure, "schema"):
            transform(self.catalog, path, self.out("schema"))

    def test_selected_feature_signature_is_immutable(self):
        meta, model = load_model(self.model, trusted=True)
        g = copy.deepcopy(self.train)
        g["columns"][0]["semantic_sha"] = "changed"
        with self.assertRaisesRegex(InputFailure, "mismatch"):
            score_group(g, meta, model)

    def test_artifact_tampering_blocked(self):
        dest = self.out("tampered")
        dest.mkdir()
        write(dest / "prepared.json", {"groups": []})
        seal(dest, "feature_pack")
        write(dest / "prepared.json", {"groups": ["tampered"]})
        with self.assertRaisesRegex(InputFailure, "changed"):
            load_pack(dest)

    def test_existing_v12_inspected_without_pickle(self):
        result = inspect_published()
        self.assertFalse(result["pickle_loaded"])
        self.assertFalse(result["direct_row_feature_input_compatible"])
        self.assertTrue(all(x["sha256_matches"] for x in result["published_models"]))


if __name__ == "__main__":
    unittest.main()
