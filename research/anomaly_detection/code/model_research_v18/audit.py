"""Recount nine metrics, exact fallback, original hashes and handoff equality."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
from model_evaluation.data import sha256, verify_benchmark
from model_research_v13.data import read, write, baseline_monitors
from model_research_v14.evaluate import Counts
from model_research_v15.data import prepare
from model_research_v18.run import OUTPUT


def audit():
    selection = read(OUTPUT/"selection.json")
    assert sha256(OUTPUT/"families.joblib") == selection["model_sha256"]
    stages = {}
    for stage in ("parent", "adjusted", "difficulty_only"):
        index = read(OUTPUT/f"{stage}_evaluation_index.json")
        directory = ROOT/index["current"]
        assert sha256(directory/"summary.json") == index["summary_sha256"]
        summary = read(directory/"summary.json"); counts = Counts()
        for d in summary["datasets"]:
            for cohort in ("observed", "normal"):
                path = directory/d["dataset"]/(cohort+".npz")
                assert sha256(path) == d["arrays_sha256"][cohort]
                with np.load(path, allow_pickle=False) as a:
                    np.testing.assert_array_equal(a["prediction"], a["margin"] > 0)
                    for base in (0,3,6):
                        mask = ~a["covered"][:,base]
                        np.testing.assert_array_equal(a["margin"][mask,base], a["margin"][mask,base+1])
                        np.testing.assert_array_equal(a["margin"][mask,base], a["margin"][mask,base+2])
                    counts.update(a["truth"], a["prediction"])
        assert counts.records() == summary["pooled"]
        stages[stage] = {"recounted": True, "benchmark_sha256": summary["protocol"]["benchmark_manifest_sha256"]}
    parent_dir = ROOT/read(OUTPUT/"parent_evaluation_index.json")["current"]
    adjusted_dir = ROOT/read(OUTPUT/"adjusted_evaluation_index.json")["current"]
    for cohort in ("observed", "normal"):
        with np.load(parent_dir/"mixed_hard"/(cohort+".npz"),allow_pickle=False) as before, \
             np.load(adjusted_dir/"mixed_hard"/(cohort+".npz"),allow_pickle=False) as after:
            assert set(before.files) == set(after.files)
            for name in before.files: np.testing.assert_array_equal(before[name],after[name])
    data = prepare(); families = joblib.load(OUTPUT/"families.joblib")
    manifest = read(OUTPUT/"handoff/manifest.json")
    portable = []
    for m in manifest["models"]:
        path = OUTPUT/"handoff"/m["file"]
        assert sha256(path) == m["sha256"]
        model = joblib.load(path)
        for name in ("individual_delay", "gradual_degradation", "time_burst"):
            frame = data["search"][name]["observed"].iloc[:700]
            expected, actual = families[m["algorithm"]].score(frame), model.score_features(frame)
            for x,y in zip(expected,actual): np.testing.assert_array_equal(x,y)
        portable.append(m["algorithm"])
    cal_ids = set(data["normal_reference"].case_id)
    for bank in (data["search"], data["confirmation"]):
        assert not cal_ids.intersection(set().union(*(set(f.case_id) for c in bank.values() for f in c.values())))
    baseline_monitors()
    verify_benchmark(ROOT/"data/evaluation", ROOT/"reports/evaluation_v2_work/inputs.json")
    activation = read(ROOT/"reports/benchmark_mild_v2/activation.json")
    assert activation["verified"]
    code_hashes = {str(p.relative_to(ROOT)): sha256(p) for v in (15,16,17,18)
                   for p in (ROOT/f"code/model_research_v{v}").glob("*.py")}
    write(OUTPUT/"audit.json", {"stages": stages, "nine_metric_counts_recounted": True,
        "code_sha256": code_hashes,
        "exact_fallback_all_algorithms": True, "portable_equality_algorithms": portable,
        "calibration_and_all_search_cases_disjoint": True, "original_serving_models_unchanged": True,
        "benchmark_activation_verified": True, "hard_and_normal_controls_unchanged": True,
        "hard_same_model_predictions_and_features_metadata_exact": True,
        "production_auto_deployed": False, "github_uploaded": False})
    print("Final metric, fallback, handoff, case-split and source-data audits passed", flush=True)


if __name__ == "__main__": audit()
