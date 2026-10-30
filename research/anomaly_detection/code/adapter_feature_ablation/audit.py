"""Independent counts, original-data preservation and executed-notebook checks."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import nbformat
import numpy as np

from adapter_feature_ablation.run import OUTPUT, METHODS, MAIN_SCENARIOS, counts
from model_research_v13.data import read, write
from model_evaluation.data import sha256
from model_evaluation.metrics import metric_record


def verify_raw_process_path():
    """Generate one normal input in memory, not a new benchmark dataset."""
    import pandas as pd
    from evaluation_benchmark.generate import Case
    from model_evaluation.data import ReplayCatalog, history_lookup
    from model_improvement.data import rich_rows
    source = read(ROOT/"reports/evaluation_v2_work/inputs.json")
    case = Case(ReplayCatalog(source["templates"]), "input_stop", 0, 987654321,
                history_lookup(ROOT/"data/evaluation"))
    case.baseline(12)
    native = pd.DataFrame(case.export()["windows.csv"])
    native = native.loc[native.scope.eq("TARGET")].reset_index(drop=True)
    rows = rich_rows(case, case.normal)
    manifest = read(OUTPUT/"handoff/manifest.json")
    for record in manifest["models"]:
        path = OUTPUT/"handoff"/record["file"]
        assert sha256(path) == record["sha256"]
        monitor = joblib.load(path)
        result = monitor.predict(native, rows)
        assert len(result) == len(native)
        assert np.isfinite(result.score).all()
        np.testing.assert_array_equal(result.anomaly, result.score > result.threshold)
    return {"in_memory_normal_stream_minutes": len(native), "algorithms": 3,
            "raw_process_to_features_to_prediction_passed": True}


def audit():
    frozen, summary = read(OUTPUT/"frozen.json"), read(OUTPUT/"summary.json")
    assert sha256(ROOT/"data/evaluation/manifest.json") == frozen["benchmark_manifest_sha256"]
    assert sha256(ROOT/"reports/model_research_v18/families.joblib") == frozen["old_model_sha256"]
    assert sha256(OUTPUT/"shared_models.joblib") == frozen["model_sha256"]
    models = joblib.load(OUTPUT/"shared_models.joblib")["models"]
    assert set(models) == set(METHODS)
    assert all(not hasattr(model, "experts") for model in models.values())
    matrix, main = np.zeros((len(METHODS), 4), np.int64), np.zeros((len(METHODS), 4), np.int64)
    decisions = 0
    for dataset in summary["datasets"]:
        data = np.load(OUTPUT/(dataset["dataset"]+"_scores.npz"))
        y, scores = data["y"], data["scores"]
        assert scores.shape == (len(y), len(METHODS))
        assert np.isfinite(scores).all()
        decisions += len(y)
        for i, method in enumerate(METHODS):
            observed = counts(y, scores[:, i] > models[method].thresholds["0.03"])
            expected = metric_record(*observed)
            stored = dataset["metrics"][i]
            for key, value in expected.items():
                assert np.isclose(value, stored[key]), (method, key)
            matrix[i] += observed
            if dataset["dataset"] in MAIN_SCENARIOS:
                main[i] += observed
    for section, totals in (("pooled", matrix), ("main_five_pooled", main)):
        for i, method in enumerate(METHODS):
            for key, value in metric_record(*totals[i]).items():
                assert np.isclose(value, summary[section][i][key]), (section, method, key)
    adapter_counts = np.sum([np.array([[r[k] for k in ("tn", "fp", "fn", "tp")]
        for r in rows]) for rows in summary["per_adapter"].values()], axis=0)
    np.testing.assert_array_equal(adapter_counts, matrix)
    assert decisions == 2167766 == summary["protocol"]["evaluation_decisions"]
    notebook = nbformat.read(ROOT/"code/adapter_feature_ablation/result.ipynb", as_version=4)
    cells = [c for c in notebook.cells if c.cell_type == "code"]
    assert all(c.execution_count is not None for c in cells)
    assert not [o for c in cells for o in c.outputs if o.output_type == "error"]
    serving = verify_raw_process_path()
    report = {"passed": True, "decision_counts_recomputed_from_saved_scores": True,
        "per_adapter_counts_sum_to_pooled": True, "single_estimator_no_experts": True,
        "benchmark_manifest_unchanged": True, "v18_models_unchanged": True,
        "notebook_executed_code_cells": len(cells), "notebook_errors": 0,
        "test_threshold_tuning": False, "all_decisions": decisions, "serving_smoke_test": serving,
        "summary_sha256": sha256(OUTPUT/"summary.json"),
        "source_sha256": {p.name:sha256(p) for p in (ROOT/"code/adapter_feature_ablation").glob("*.py")}}
    write(OUTPUT/"audit.json", report)
    print(report, flush=True)


if __name__ == "__main__":
    audit()
