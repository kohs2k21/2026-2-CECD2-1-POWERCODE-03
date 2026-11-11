"""Independent saved-prediction audit; original labels/time grids unchanged."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import numpy as np
from model_evaluation.metrics import Counts
from model_evaluation.data import sha256, verify_benchmark
from model_evaluation.tune_windows import write_json
from model_improvement.data import BENCHMARK, SOURCE_CACHE
from model_research_v5.report import read, VERSIONS, LABELS
from model_research_v5.run_matched import frozen


def audit():
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    frozen()
    records, overlap = {}, np.zeros((2, 4), np.int64)
    for version in ("v5", "v6", "v7"):
        summary, counts, fallback, phase_counts = read(version), Counts(), 0, {"startup": Counts(), "steady": Counts()}
        for d in summary["datasets"]:
            for cohort, digest in d["arrays_sha256"].items():
                path = ROOT/"reports"/VERSIONS[version]/d["dataset"]/f"{cohort}.npz"
                assert sha256(path) == digest
                with np.load(path, allow_pickle=False) as a, np.load(ROOT/"reports"/VERSIONS["v4"]/d["dataset"]/f"{cohort}.npz", allow_pickle=False) as old:
                    for key in ("truth", "case_index", "window_end_seconds", "scenario_bits", "adapter", "process"):
                        np.testing.assert_array_equal(a[key], old[key])
                    np.testing.assert_array_equal(a["prediction"], a["margin"] > 0)
                    un = ~a["covered"]; fallback += int(un.sum())
                    for first in (0, 3):
                        for col in (first+1, first+2):
                            np.testing.assert_array_equal(a["margin"][un, first], a["margin"][un, col])
                    counts.update(a["truth"], a["prediction"])
                    for phase, counter in phase_counts.items(): counter.update(a["truth"], a["prediction"], a["phase"] == phase)
                    if version == "v7":
                        p, q = a["prediction"][:, 2], a["prediction"][:, 5]
                        flags = [p & ~q, ~p & q, p & q, ~p & ~q]
                        for truth in (0, 1):
                            for i, flag in enumerate(flags): overlap[truth, i] += int(np.sum(flag & (a["truth"] == bool(truth))))
        for recomputed, stored in zip(counts.records(), summary["pooled"]):
            for key in ("tn", "fp", "fn", "tp", "n"): assert recomputed[key] == stored[key], (version, key)
            for key in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate"):
                assert abs(recomputed[key]-stored[key]) < 1e-12, (version, key)
        records[version] = {"decisions": int(counts.records()[0]["n"]), "fallback_decisions": fallback,
                            "data_labels_times_equal_v4": True, "metrics_recomputed": True,
                            "strict_margin_positive": True, "fallback_exact_global": True,
                            "phase_metrics": {p: c.records() for p, c in phase_counts.items()}}
    agreement = [{"truth": bool(truth), "agreement": label, "n": int(overlap[truth, i]),
                  "fraction": float(overlap[truth, i]/overlap[truth].sum())}
                 for truth in (0, 1) for i, label in enumerate(("IF만", "K-means만", "둘 다", "둘 다 안 함"))]
    versions = {v: {"bytes": (ROOT/"reports"/VERSIONS[v]/f"models_{v}.joblib").stat().st_size,
                    "sha256": sha256(ROOT/"reports"/VERSIONS[v]/f"models_{v}.joblib")} for v in ("v4", "v5", "v6", "v7")}
    result = {"verification": records, "alert_agreement": agreement, "models": versions,
              "benchmark_and_previous_serving_model_verified_unchanged": True,
              "point_adjustment": False, "note": "Existing original benchmark, NOT blind external validation."}
    write_json(ROOT/"reports/model_research_v7/audit.json", result)
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    print(json.dumps({"verification": {v: {k: d[k] for k in d if k != "phase_metrics"} for v, d in records.items()}, "models": versions}, indent=2))
    return result


if __name__ == "__main__": audit()
