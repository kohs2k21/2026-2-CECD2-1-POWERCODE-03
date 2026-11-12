"""Same-source rule joins and actual frozen PR98 inference, with no training."""
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from research.integration.feature_rule_ml.common import InputFailure, load_pack, seal, sha, write
from research.integration.feature_rule_ml.process_event_bridge import (
    PACKAGE, bind_rules, predict_process, read_processes,
)

RUNTIME = os.environ.get("PROCESS_EVENT_RUNTIME")


def fixture(root, rows):
    """Explicit CPU wiring fixture, never represented as a fresh LLM output."""
    root.mkdir(parents=True)
    source = root / "processes.csv"
    names = list(dict.fromkeys(k for row in rows for k in row))
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    records, mapping = read_processes(source)
    columns = [{"name": "count", "dtype": "number", "unit": "count", "origin": "CPU_wiring_fixture"}]
    rules = [{"id": "fixture_" + kind, "kind": kind, "column": "count", "source_binding": {},
              "decision": {"request": role, "condition": {"op": op, "value": value}}}
             for kind, role, op, value in [("HR", "hr", "lt", 0), ("SR", "baseline", "gt", 4)]]
    keys = ["transactionId", "processId", "event_row_id"]
    group = {"id": "process_fixture", "grain": {"table": "P", "keys": keys},
             "source_pins": [{"table": "P", "sha256": sha(source)}],
             "columns": columns, "rules": rules, "pending": [], "rows": []}
    for r in records:
        value = None if r["totalCount"] == "" else r["totalCount"]
        group["rows"].append({"source_row_key": [r[k] for k in keys],
                              "features": {"count": {"state": "unavailable" if value is None else "value",
                                                       "value": value}}})
    prepared = root / "prepared"
    prepared.mkdir()
    write(prepared / "prepared.json", {"origin": "controlled_CPU_wiring_fixture_not_LLM_generation",
                                       "groups": [group], "holds": []})
    seal(prepared, "feature_pack")
    return source, prepared, keys, records, mapping


class ProcessBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rows = json.loads((PACKAGE / "examples/completed_process.json").read_text())[:2]
        self.source, self.prepared, self.keys, self.records, self.mapping = fixture(self.root / "input", rows)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identical_source_and_complete_row_identity_bind(self):
        group, bound = bind_rules(self.prepared, "process_fixture", self.keys, self.records,
                                  sha(self.source), self.mapping)
        self.assertEqual(len(bound), 2)
        self.assertEqual(group["grain"]["table"], "P")

    def test_same_ids_on_changed_data_cannot_reuse_rule_features(self):
        with self.assertRaisesRegex(InputFailure, "identical pinned source"):
            bind_rules(self.prepared, "process_fixture", self.keys, self.records, "changed", self.mapping)

    def test_duplicate_or_partial_process_keys_rejected(self):
        with self.assertRaisesRegex(InputFailure, "one-to-one"):
            bind_rules(self.prepared, "process_fixture", self.keys, self.records[:1], sha(self.source), self.mapping)
        with self.assertRaisesRegex(InputFailure, "one-to-one"):
            bind_rules(self.prepared, "process_fixture", self.keys, self.records * 2, sha(self.source), self.mapping)

    def test_physical_key_mapping_cannot_be_swapped(self):
        with self.assertRaisesRegex(InputFailure, "physical field mapping"):
            bind_rules(self.prepared, "process_fixture", list(reversed(self.keys)), self.records,
                       sha(self.source), self.mapping)

    def test_labels_bodies_and_case_ids_do_not_reach_model(self):
        path = self.root / "extra.json"
        write(path, [{"transactionId": "T", "label": 1, "pattern": "spike", "BODY": "secret",
                      "case_id": "synthetic_label", "pair_id": "pair", "injection_applied": True}])
        rows, _ = read_processes(path)
        self.assertEqual(set(rows[0]), {"transactionId", "event_row_id"})
        with self.assertRaisesRegex(InputFailure, "Body/payload"):
            read_processes(path, {"processId": "BODY"})

    def test_duplicate_explicit_row_ids_and_ambiguous_mapping_rejected(self):
        path = self.root / "duplicate.json"
        write(path, [{"event_row_id": 1}, {"event_row_id": "1"}])
        with self.assertRaisesRegex(InputFailure, "Duplicate event_row_id"):
            read_processes(path)
        with self.assertRaisesRegex(InputFailure, "multiple Process fields"):
            read_processes(path, {"transactionId": "id", "processId": "id"})

    def test_untrusted_pickle_rejected(self):
        with self.assertRaisesRegex(InputFailure, "trust-local-model"):
            predict_process(self.source, self.root / "out", "/missing")

    def test_missing_runtime_is_recorded_without_healthy_fallback(self):
        result = predict_process(self.source, self.root / "missing", "/missing/python", trusted=True)
        self.assertTrue(result["runtime_failure"])
        self.assertTrue(all(r["ML"]["state"] == r["OR"] == "unassessable" for r in result["rows"]))


@unittest.skipUnless(RUNTIME, "Set PROCESS_EVENT_RUNTIME to the approved pinned Python 3.12 interpreter")
class FrozenProcessModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.raw = json.loads((PACKAGE / "examples/completed_process.json").read_text())
        cls.source, cls.prepared, cls.keys, _, _ = fixture(cls.root / "base", cls.raw)
        cls.first = predict_process(cls.source, cls.root / "first", RUNTIME, cls.prepared,
                                    "process_fixture", cls.keys, trusted=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_three_actual_frozen_algorithms_score_every_process(self):
        self.assertIsNone(self.first["runtime_failure"])
        self.assertEqual(len(self.first["rows"]), 12)
        for row in self.first["rows"]:
            self.assertEqual(set(row["ML"]["methods"]), {"if", "kmeans", "lof"})
            self.assertGreater(len(row["ml_features"]["values"]), 18)
            for method in row["ML"]["methods"].values():
                self.assertIsNotNone(method["score"])
                self.assertEqual(method["state"] == "warning", method["anomaly"])

    def test_same_run_shares_original_process_identity_and_rule_results(self):
        self.assertTrue(all(r["source_row_key"] and r["HR"]["state"] == "nonwarning"
                            and r["SR"]["state"] == "nonwarning" for r in self.first["rows"]))
        self.assertTrue(all(r["OR"] == r["ML"]["state"] for r in self.first["rows"]))

    def test_new_run_does_not_inherit_state(self):
        second = predict_process(self.source, self.root / "second", RUNTIME, self.prepared,
                                 "process_fixture", self.keys, trusted=True)
        self.assertEqual(self.first["rows"], second["rows"])

    def test_invalid_count_quarantined_but_rule_warning_survives(self):
        rows = copy.deepcopy(self.raw)
        rows[0]["totalCount"] = -1
        rows[-1]["totalCount"] = 10
        source, prepared, keys, _, _ = fixture(self.root / "bad_count", rows)
        result = predict_process(source, self.root / "bad_count_result", RUNTIME, prepared,
                                 "process_fixture", keys, trusted=True)
        self.assertIsNone(result["runtime_failure"])
        self.assertEqual(result["rows"][0]["ML"]["state"], "unassessable")
        self.assertEqual(result["rows"][0]["HR"]["state"], "warning")
        self.assertEqual(result["rows"][0]["OR"], "warning")
        self.assertEqual(result["rows"][-1]["SR"]["state"], "warning")

    def test_original_model_api_parity_and_no_fit(self):
        script = '''
import sys,json
sys.path.insert(0,sys.argv[1])
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
def forbidden(*a,**k):raise AssertionError("fit forbidden")
for cls in (IsolationForest,MiniBatchKMeans,LocalOutlierFactor):cls.fit=forbidden
from process_event_model.inference import load
records=json.load(open(sys.argv[2]))["records"]
actual={}
for a in ("if","kmeans","lof"):
 out=load(sys.argv[3],a,with_incidents=True).predict(records)
 actual[a]=[{"score":float(s),"anomaly":bool(v)} for s,v in zip(out.score,out.anomaly)]
print(json.dumps(actual))
'''
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        env.pop("PYTHONPATH", None)
        run = subprocess.run([RUNTIME, "-B", "-c", script, str(PACKAGE / "code"),
                              str(self.root / "first/ml_request.json"),
                              str(PACKAGE / "models/latest_process_rows/event_models.joblib")],
                             env=env,capture_output=True,text=True,check=True,timeout=120)
        expected = json.loads(run.stdout)
        for i,row in enumerate(self.first["rows"]):
            for a in ("if", "kmeans", "lof"):
                self.assertAlmostEqual(row["ML"]["methods"][a]["score"], expected[a][i]["score"], places=12)
                self.assertEqual(row["ML"]["methods"][a]["anomaly"], expected[a][i]["anomaly"])

    def test_invalid_process_quarantines_its_complete_transaction(self):
        rows = copy.deepcopy(self.raw[:2])
        rows[1]["transactionId"] = rows[0]["transactionId"]
        rows[0]["totalCount"] = -1
        source, prepared, keys, _, _ = fixture(self.root / "peer", rows)
        result = predict_process(source, self.root / "peer_result", RUNTIME, prepared,
                                 "process_fixture", keys, trusted=True)
        self.assertTrue(all(r["ML"]["state"] == "unassessable" for r in result["rows"]))

    def test_same_transaction_id_in_another_source_period_is_independent(self):
        rows = copy.deepcopy(self.raw[:2])
        rows[1]["transactionId"] = rows[0]["transactionId"]
        rows[0]["source_period"], rows[1]["source_period"] = "source_a", "source_b"
        rows[0]["totalCount"] = -1
        source, prepared, keys, _, _ = fixture(self.root / "periods", rows)
        result = predict_process(source, self.root / "periods_result", RUNTIME, prepared,
                                 "process_fixture", keys, trusted=True)
        self.assertEqual(result["rows"][0]["ML"]["state"], "unassessable")
        self.assertNotEqual(result["rows"][1]["ML"]["state"], "unassessable")

    def test_one_transaction_cannot_have_split_arrival_timestamps(self):
        rows = copy.deepcopy(self.raw[:2])
        rows[1]["transactionId"] = rows[0]["transactionId"]
        rows[0]["availableTime"] = "2026-01-03T09:02:00+09:00"
        rows[1]["availableTime"] = "2026-01-03T09:03:00+09:00"
        source, prepared, keys, _, _ = fixture(self.root / "split", rows)
        result = predict_process(source, self.root / "split_result", RUNTIME, prepared,
                                 "process_fixture", keys, trusted=True)
        self.assertTrue(all(r["ML"]["state"] == "unassessable" for r in result["rows"]))
        self.assertTrue(all(r["input_error"] == "inconsistent_transaction_available_time" for r in result["rows"]))


if __name__ == "__main__":
    unittest.main()
