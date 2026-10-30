"""Same-data model improvements separated from benchmark-difficulty effects.

Reuse verified v14 native-feature caches for the unchanged parent benchmark.
v15 temporal features are derived causally at load. New CSV gets a new cache
key. Both old and new frozen detectors are evaluated on the updated benchmark.
"""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
import model_research_v14.evaluate as evaluator
from model_evaluation.data import sha256
from model_research_v13.data import read, write
from model_research_v15.data import OUTPUT, prepare
from model_research_v15.features import augment
from model_research_v15.run import baselines

PARENT_SHA = "7f2dc4351af6166416f7694df97fb2050cab8d348e5b29d8451fcc36031f3b36"
ACTIVE = ROOT/"data/evaluation"
RECOVERY = ROOT/"reports/benchmark_before_mild_v2_20261014"


def selected_source(stage):
    if stage == "parent":
        return ACTIVE if sha256(ACTIVE/"manifest.json") == PARENT_SHA else RECOVERY
    return ACTIVE


def configure(source, output):
    evaluator.BENCHMARK, evaluator.OUTPUT, evaluator.prepare = source, output, prepare
    # Local module facade, not a global joblib monkeypatch.
    def load(path):
        data = joblib.load(path)
        if isinstance(data, dict) and "frame" in data and "metadata" in data:
            data["frame"] = augment(data["frame"])
        return data
    evaluator.joblib = SimpleNamespace(load=load, dump=joblib.dump)
    digest = sha256(source/"manifest.json")
    if digest == PARENT_SHA:
        evaluator.cache_folder = lambda: ROOT/"reports/model_research_v14/feature_cache"/digest[:16]
    else:
        evaluator.cache_folder = lambda: OUTPUT/"feature_cache"/digest[:16]


def evaluate(stage):
    source = selected_source(stage)
    if stage == "parent" and sha256(source/"manifest.json") != PARENT_SHA: raise ValueError("Parent changed")
    configure(source, OUTPUT)
    evaluator.evaluate()
    index = read(OUTPUT/"evaluation_index.json")
    directory = ROOT/index["current"]
    summary = read(directory/"summary.json")
    summary["protocol"].update({"comparison_stage": stage, "parent_benchmark_sha256": PARENT_SHA,
        "benchmark_modified_in_this_experiment": stage != "parent",
        "normal_calibration_and_all_search_cases_disjoint": True,
        "baseline": "v12 IF/K-means and v14 LOF; parameter changes selected on fresh case-disjoint development only"})
    write(directory/"summary.json", summary)
    write(OUTPUT/f"{stage}_evaluation_index.json", {**index, "summary_sha256": sha256(directory/"summary.json")})
    if stage == "parent": return
    # For isolation of data difficulty: OLD frozen models, SAME new data/cache.
    old_output = OUTPUT/"baseline_on_adjusted"; old_output.mkdir(exist_ok=True)
    models = baselines(); joblib.dump(models, old_output/"families.joblib", compress=3)
    write(old_output/"selection.json", {"model_sha256": sha256(old_output/"families.joblib"), "refit": False})
    configure(source, old_output); evaluator.evaluate()
    old_index = read(old_output/"evaluation_index.json")
    old_summary = read(ROOT/old_index["current"] / "summary.json")
    old_summary["protocol"].update({"comparison_stage": "difficulty_only", "detectors_refit": False,
        "thresholds_refit": False, "benchmark_modified_in_this_experiment": True,
        "baseline": "Exactly v12 IF/K-means and v14 LOF; not the previous IF recall-oriented candidate"})
    write(ROOT/old_index["current"] / "summary.json", old_summary)
    write(OUTPUT/"difficulty_only_evaluation_index.json", {**old_index,
        "summary_sha256": sha256(ROOT/old_index["current"] / "summary.json")})
    # Main notebook points to the NEW model on the active benchmark.
    write(OUTPUT/"evaluation_index.json", {**index, "summary_sha256": sha256(directory/"summary.json")})


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--stage", choices=("cache", "parent", "adjusted"), required=True)
    stage = p.parse_args().stage
    if stage == "cache":
        configure(ACTIVE, OUTPUT); evaluator.build_cache()
    else: evaluate(stage)
