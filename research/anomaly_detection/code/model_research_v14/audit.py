"""Recount final decisions and check portable model equality/frozen baselines."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
import numpy as np
from model_evaluation.data import sha256, verify_benchmark
from model_research_v13.data import read, write, prepare, baseline_monitors
from model_research_v14.evaluate import Counts, cache_folder, BENCHMARK
from model_research_v14.run import OUTPUT


def audit():
    index = read(OUTPUT / "evaluation_index.json"); folder = ROOT / index["current"]
    summary = read(folder / "summary.json")
    count = Counts(); covered = Counts(); fallback = Counts()
    for dataset in summary["datasets"]:
        for cohort in ("observed", "normal"):
            path = folder / dataset["dataset"] / (cohort+".npz")
            if sha256(path) != dataset["arrays_sha256"][cohort]: raise ValueError("Changed final scores")
            with np.load(path, allow_pickle=False) as a:
                np.testing.assert_array_equal(a["prediction"], a["margin"] > 0)
                count.update(a["truth"], a["prediction"])
                covered.update(a["truth"], a["prediction"], a["covered"])
                fallback.update(a["truth"], a["prediction"], ~a["covered"])
                for base in (0, 3, 6):
                    mask = ~a["covered"][:, base]
                    np.testing.assert_array_equal(a["margin"][mask, base], a["margin"][mask, base+1])
                    np.testing.assert_array_equal(a["margin"][mask, base], a["margin"][mask, base+2])
                # K-means v14 is preserved v12. Compare by exact case/time order,
                # not just coincidentally equal aggregate metrics.
                with np.load(ROOT / "reports/benchmark_mild_v1" / dataset["dataset"] / (cohort+".npz"), allow_pickle=False) as old:
                    # Original Hard cache sorted ALL cases together; v14 uses
                    # 500-case blocks. Align explicit keys, never positional rows.
                    order = np.lexsort((a["window_end_seconds"], a["case_index"]))
                    previous = np.lexsort((old["window_end_seconds"], old["case_index"]))
                    np.testing.assert_array_equal(a["case_index"][order], old["case_index"][previous])
                    np.testing.assert_array_equal(a["window_end_seconds"][order], old["window_end_seconds"][previous])
                    np.testing.assert_array_equal(a["truth"][order], old["truth"][previous])
                    np.testing.assert_array_equal(a["prediction"][order, 3:6], old["prediction"][previous, 3:6])
    assert count.records() == summary["pooled"]
    assert covered.records() == summary["supported_only"]
    assert fallback.records() == summary["fallback_only"]
    families = joblib.load(OUTPUT / "families.joblib")
    handoff = read(OUTPUT / "handoff/manifest.json")
    development = prepare(); checked = []
    for r in handoff["models"]:
        path = OUTPUT / "handoff" / r["file"]
        if sha256(path) != r["sha256"]: raise ValueError("Changed portable model")
        model = joblib.load(path)
        for name in ("individual_delay", "gradual_degradation", "time_burst"):
            frame = development["bank"][name]["observed"].iloc[:800]
            expected = families[r["algorithm"]].score(frame)
            actual = model.score_features(frame)
            for x, y in zip(expected, actual): np.testing.assert_array_equal(x, y)
        checked.append(r["algorithm"])
    verify_benchmark(BENCHMARK, ROOT / "reports/evaluation_v2_work/inputs.json")
    baseline_monitors()  # Checks immutable v12 trusted hashes again.
    write(OUTPUT / "audit.json", {"metric_counts_recounted": True, "nine_strategy_margin_flag_equal": True,
          "unsupported_adapter_exact_fallback": True, "kmeans_v12_predictions_identical": True,
          "portable_score_equality_algorithms": checked, "original_models_unchanged": True,
          "current_benchmark_sha256": sha256(BENCHMARK / "manifest.json"),
          "benchmark_unchanged_from_start": sha256(BENCHMARK / "manifest.json") == "7f2dc4351af6166416f7694df97fb2050cab8d348e5b29d8451fcc36031f3b36",
          "selection_sha256": sha256(OUTPUT / "selection.json"),
          "normal_diversity_provenance": read(OUTPUT / "normal_diversity.json"),
          "selected_model_sha256": sha256(OUTPUT / "families.joblib")})
    print("Nine-strategy recount, unchanged K-means, portable equality and baseline/data hashes verified", flush=True)


if __name__ == "__main__": audit()
