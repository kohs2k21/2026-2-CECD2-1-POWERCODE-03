"""Independent final audit: immutable inputs, exact labels, metrics and fallback."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import numpy as np
import nbformat
import pandas as pd

from model_evaluation.data import SCENARIOS, verify_benchmark, sha256
from model_evaluation.metrics import Counts
from model_evaluation.tune_windows import write_json
from model_improvement.data import BENCHMARK, SOURCE_CACHE
from model_improvement.refine import OUTPUT, frozen


def run():
    _, selection = frozen()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    summary = json.loads((OUTPUT/"summary.json").read_text(encoding="utf-8"))
    total, fallback = Counts(), 0
    for dataset in summary["datasets"]:
        name = dataset["dataset"]
        count = Counts()
        case_ids = pd.read_csv(BENCHMARK/name/"cases.csv", usecols=["case_id"], encoding="utf-8-sig").case_id.to_numpy()
        old_info = json.loads((ROOT/"reports/model_evaluation_v2"/name/"metrics.json").read_text(encoding="utf-8"))
        old_ids = np.asarray(old_info["case_ids"])
        for cohort, digest in dataset["arrays_sha256"].items():
            path = OUTPUT/name/f"{cohort}.npz"
            assert sha256(path) == digest
            with np.load(path, allow_pickle=False) as arrays, np.load(
                    ROOT/"reports/model_evaluation_v2"/name/f"window_{cohort}.npz", allow_pickle=False) as old:
                # Not just equal denominators: SAME decisions and ground truth.
                for key in ("truth", "window_end_seconds"):
                    np.testing.assert_array_equal(arrays[key], old[key])
                # v2 indexes a sorted case table; v4 indexes the original table.
                # Integer ordinals differ, but actual case identity must match.
                np.testing.assert_array_equal(case_ids[arrays["case_index"]], old_ids[old["case_index"]])
                np.testing.assert_array_equal(arrays["prediction"], arrays["margin"] > 0)
                count.update(arrays["truth"], arrays["prediction"])
                mask = ~arrays["covered"]
                fallback += int(mask.sum())
                np.testing.assert_array_equal(arrays["prediction"][mask][:, [0, 0, 3, 3]], arrays["prediction"][mask][:, [1, 2, 4, 5]])
                np.testing.assert_array_equal(arrays["margin"][mask][:, [0, 0, 3, 3]], arrays["margin"][mask][:, [1, 2, 4, 5]])
                if cohort == "normal": assert not arrays["truth"].any()
        assert count.records() == dataset["metrics"]
        total.values += count.values
    assert total.records() == summary["pooled"]
    notebook = nbformat.read(ROOT/"code/model_improvement/results.ipynb", as_version=4)
    errors = [o for cell in notebook.cells for o in cell.get("outputs", []) if o.output_type == "error"]
    code_cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert not errors and all(c.execution_count is not None for c in code_cells)
    pngs = sum("image/png" in o.get("data", {}) for c in code_cells for o in c.outputs)
    record = {"passed": True, "input_csv_and_original_policy_immutable": True,
              "original_v1_model_sha256": sha256(ROOT/"code/process_anomaly_pipeline/artifacts/process_if_v1.joblib"),
              "model_sha256": selection["model_sha256"], "decision_and_labels_exactly_same_as_v2": True,
              "confusion_matrices_independently_recounted": True, "strict_margin_greater_than_zero": True,
              "fallback_decisions_exactly_match_global": fallback,
              "decision_rows": summary["pooled"][0]["n"], "notebook_cells": len(notebook.cells),
              "executed_code_cells": len(code_cells), "figures": pngs, "notebook_errors": len(errors)}
    write_json(OUTPUT/"verification.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__": run()
