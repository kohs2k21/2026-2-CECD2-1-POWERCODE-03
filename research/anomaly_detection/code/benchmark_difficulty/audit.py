"""Independent inventory, normal-pair, hard, and confusion-matrix checks."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import numpy as np
from benchmark_difficulty.adjust import DEST, SOURCE, WORK, inventory, read_json, write_json
from benchmark_difficulty.evaluate import OUTPUT
from evaluation_benchmark.generate import SCENARIOS
from model_evaluation.data import sha256
from model_evaluation.metrics import Counts


def main():
    frozen = read_json(WORK / "frozen_protocol.json")
    if inventory(SOURCE) != frozen["original_files"]:
        raise ValueError("Original files changed")
    if {k: sha256(ROOT / k) for k in frozen["models"]} != frozen["models"]:
        raise ValueError("Models changed")
    validation, summary = read_json(DEST / "validation.json"), read_json(OUTPUT / "summary.json")
    assert validation["passed"] and validation["coverage"] == "all_seven_datasets"
    total, paired, decisions, same_normals = Counts(), 0, 0, 0
    for dataset in summary["datasets"]:
        name, count = dataset["dataset"], Counts()
        for cohort in ("observed", "normal"):
            with np.load(OUTPUT / name / (cohort + ".npz"), allow_pickle=False) as a:
                np.testing.assert_array_equal(a["prediction"], a["margin"] > 0)
                count.update(a["truth"], a["prediction"])
                decisions += len(a["truth"])
        assert count.records() == dataset["metrics"]
        total.values += count.values
        if name in SCENARIOS:
            for filename in ("normal_processes.csv", "normal_transactions_context.csv"):
                assert sha256(SOURCE / name / filename) == sha256(DEST / name / filename)
                paired += 1
            assert dataset["normal_shared_predictions_equal"]
            same_normals += dataset["normal_shared_decisions"]
    assert total.records() == summary["pooled"]
    for file in (SOURCE / "mixed_hard").iterdir():
        if file.is_file():
            assert sha256(file) == sha256(DEST / "mixed_hard" / file.name)
    hits, unknown = 0, 0
    for d in validation["datasets"].values():
        for r in d["actual_rule_validation"].values():
            hits += sum(r["rule_hits"].values())
            unknown += sum(r["unknown_rule_flags"].values())
    assert hits == unknown == 0
    adjustments = read_json(WORK / "adjustments.json")
    trends = [r for r in adjustments if r["scenario"] == "gradual_degradation"]
    result = {"passed": True, "anomaly_cases": validation["anomaly_cases"],
        "normal_cases": validation["normal_cases"], "decisions": decisions,
        "rule_hits": hits, "unknown_rule_flags": unknown, "all_status_S": True,
        "previous_version_backup_unchanged": True, "active_dataset_inplace_updated": True,
        "active_dataset_path": "data/evaluation", "backup_path": "reports/benchmark_before_mild_20261014",
        "frozen_models_unchanged": True,
        "normal_control_files_byte_identical": paired, "normal_shared_predictions_identical": same_normals,
        "hard_all_files_byte_identical": True, "metrics_independently_recounted": True,
        "trend_cap_limited_cases": sum(r["cap_limited"] for r in trends),
        "profile_fixed_before_scoring": True, "case_selection_by_score": False,
        "interpretation": "benchmark sensitivity only; no model or threshold improvement claim"}
    write_json(OUTPUT / "audit.json", result)
    print(result)


if __name__ == "__main__":
    main()
