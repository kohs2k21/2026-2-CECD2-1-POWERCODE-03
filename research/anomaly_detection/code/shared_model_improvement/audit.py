"""Requirement-by-requirement completion audit against actual stored scores."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import nbformat
import numpy as np

from adapter_feature_ablation.features import BASE, ADAPTER
from adapter_feature_ablation.run import counts
from model_evaluation.data import sha256, verify_benchmark
from model_evaluation.metrics import metric_record
from model_research_v13.data import read, write
from shared_model_improvement.run import OUTPUT, OLD, ALGORITHMS, load_models, prepare, _stats


def smoke_test():
    import pandas as pd
    from evaluation_benchmark.generate import Case
    from model_evaluation.data import ReplayCatalog, history_lookup
    from model_improvement.data import rich_rows
    source = read(ROOT/"reports/evaluation_v2_work/inputs.json")
    case = Case(ReplayCatalog(source["templates"]), "input_stop", 0, 987654322, history_lookup(ROOT/"data/evaluation"))
    case.baseline(12)
    native = pd.DataFrame(case.export()["windows.csv"])
    native = native.loc[native.scope.eq("TARGET")].reset_index(drop=True)
    rows = rich_rows(case, case.normal)
    models = read(OUTPUT/"handoff/manifest.json")["models"]
    for entry in models:
        path = OUTPUT/"handoff"/entry["file"]
        assert sha256(path) == entry["sha256"]
        monitor = joblib.load(path)
        result = monitor.predict(native, rows)
        assert len(result) == len(native) and np.isfinite(result.score).all()
        np.testing.assert_array_equal(result.anomaly, result.score > result.threshold)
        unseen = native.copy(); unseen["adapterType"] = "UNSEEN_ADAPTER"
        unseen_rows = rows.copy(); unseen_rows["adapterType"] = "UNSEEN_ADAPTER"
        unknown = monitor.predict(unseen, unseen_rows)
        assert np.isfinite(unknown.score).all()
        assert unknown.adapter_reference_level.eq(2).all()
    return {"raw_process_to_prediction": True, "unknown_adapter_finite_global_reference": True, "algorithms": 3}


def audit(folder=None):
    global OUTPUT
    if folder is not None: OUTPUT = Path(folder).resolve()
    selection, fitted = load_models(OUTPUT)
    summary = read(OUTPUT/"summary.json")
    # Verify every original CSV against its existing manifest, not only the manifest name.
    verify_benchmark(ROOT/"data/evaluation", ROOT/"reports/evaluation_v2_work/inputs.json")
    assert sha256(OLD/"shared_models.joblib") == selection["old_models_sha256"]
    assert sha256(OLD/"summary.json") == selection["old_experiment_summary_sha256"]
    assert sha256(ROOT/"reports/model_research_v18/families.joblib") == selection["v18_models_sha256"]
    old_config = {r["method"]:r["thresholds"]["0.03"] for r in read(OLD/"frozen.json")["models"]}
    new_totals, old_totals = np.zeros((3,4), np.int64), np.zeros((3,4), np.int64)
    decisions = 0
    for dataset in summary["datasets"]:
        name = dataset["dataset"]
        new, old = np.load(OUTPUT/(name+"_scores.npz")), np.load(OLD/(name+"_scores.npz"))
        np.testing.assert_array_equal(new["y"], old["y"])
        assert new["scores"].shape == (len(new["y"]), 3)
        decisions += len(new["y"])
        for i, algorithm in enumerate(ALGORITHMS):
            predicted = new["scores"][:,i] > fitted["models"][algorithm].threshold
            vector = counts(new["y"], predicted)
            new_totals[i] += vector
            old_totals[i] += counts(old["y"], old["scores"][:,i*2] > old_config[algorithm+"_baseline"])
            for key, value in metric_record(*vector).items():
                assert np.isclose(value, dataset["metrics"][i][key]), (name, algorithm, key)
    requirements, uses = [], []
    normal = prepare()["train"].iloc[:200].copy()
    search = prepare()["search"]
    for i, algorithm in enumerate(ALGORITHMS):
        model = fitted["models"][algorithm]
        before, after = metric_record(*old_totals[i]), metric_record(*new_totals[i])
        assert after["f1"] >= before["f1"], (algorithm, "F1 below required baseline", before["f1"], after["f1"])
        assert not hasattr(model, "experts")
        adapter_cols = set(model.columns) & set(ADAPTER)
        assert len(adapter_cols) >= 9
        assert set(ADAPTER)-adapter_cols <= {"adapter_workload_z"}
        assert set(model.columns) <= set((*BASE, *ADAPTER))
        assert set(BASE[:6]).issubset(model.columns), "All six causal traffic features retained"
        assert all(model.encoder.weights[j] >= .1 for j,c in enumerate(model.columns) if c in ADAPTER)
        changed = normal.copy(); changed["adapter_count_p95_z"] += 20
        difference = float(np.max(np.abs(model.score(changed)-model.score(normal))))
        assert difference > 1e-8, (algorithm, "Adapter feature block is ineffective")
        observed = _stats(search["y"], model.score(search["frame"]), model.threshold, search["scenario"])
        for metric in ("f1", "precision", "normal_false_positive_rate", "macro_f1"):
            assert np.isclose(observed[metric], selection["selected"][algorithm][metric])
        for key, value in after.items():
            assert np.isclose(value, summary["pooled"][i][key])
        requirements.append({"algorithm": algorithm, "baseline_f1":before["f1"], "new_f1":after["f1"],
                             "f1_requirement_met": True, "adapter_features":len(adapter_cols), "one_estimator":True})
        uses.append({"algorithm":algorithm, "score_change_when_adapter_feature_changes":difference})
    notebook = nbformat.read(ROOT/"code/shared_model_improvement/result.ipynb", as_version=4)
    code_cells = [c for c in notebook.cells if c.cell_type == "code"]
    assert all(c.execution_count is not None for c in code_cells)
    assert not [o for c in code_cells for o in c.outputs if o.output_type == "error"]
    assert decisions == 2167766
    serving = smoke_test()
    record = {"passed": True, "requirements":requirements, "adapter_numeric_sensitivity":uses,
        "unchanged_original_csv_hashes_verified":True, "old_models_and_results_unchanged":True,
        "all_truth_labels_and_row_alignment_equal":True, "metrics_independently_recomputed":True,
        "all_decisions":decisions, "selection_matches_actual_model_search_scores":True,
        "notebook_executed_code_cells":len(code_cells), "notebook_errors":0, "serving":serving,
        "summary_sha256":sha256(OUTPUT/"summary.json"), "model_sha256":selection["model_sha256"],
        "source_sha256":{p.name:sha256(p) for p in (ROOT/"code/shared_model_improvement").glob("*.py")}}
    write(OUTPUT/"audit.json", record)
    print(record, flush=True)


if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path)
    audit(p.parse_args().output)
