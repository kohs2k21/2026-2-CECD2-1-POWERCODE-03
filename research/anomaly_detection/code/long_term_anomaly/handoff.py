"""Export only the selected current long-term model for a future batch worker."""
import argparse
from pathlib import Path
import importlib.metadata
import joblib
from .common import REPORT, read_json, write_json, sha256


def export():
    result = read_json(REPORT / "test_results.json")
    validation = read_json(REPORT / "validation.json")
    if not validation["integrity_passed"]:
        raise ValueError("Integrity checks must pass before handoff")
    package = joblib.load(REPORT / "models.joblib")
    assert sha256(REPORT / "models.joblib") == result["model_sha256"]
    method = package["primary"]
    folder = REPORT / "handoff"
    folder.mkdir(exist_ok=True)
    destination = folder / "long_term_model.joblib"
    joblib.dump({"primary": method, "models": {method: package["models"][method]},
                 "schema_version": 1, "retrospective": True,
                 "benchmark_manifest_sha256": package["benchmark_manifest_sha256"]},
                destination, compress=3)
    restored = joblib.load(destination)
    assert restored["models"][method].record() == package["models"][method].record()
    source = Path(__file__).parent
    runtime = ["__init__.py", "common.py", "benchmark.py", "models.py", "persistent.py", "inference.py"]
    write_json(folder / "metadata.json", {
        "method": method, "model_sha256": sha256(destination), "model_bytes": destination.stat().st_size,
        "only_selected_model_included": True,
        "api": "long_term_anomaly.inference.score_frame(model, hourly_dataframe)",
        "model_dictionary": "package['models'][package['primary']]",
        "runtime_source_hashes": {name: sha256(source / name) for name in runtime},
        "runtime_source_requirement": "add code/ to PYTHONPATH; ship the long_term_anomaly package",
        "dependencies": {name: importlib.metadata.version(name) for name in
                         ("numpy", "pandas", "scipy", "statsmodels", "ruptures", "joblib")},
        "input": {"group": "one adapterType + processId per case_id", "frequency": "1h",
                  "normal_context_hours": 672, "tested_monitor_hours": 336,
                  "collection_health_required": True, "reviewed_normal_context_required": True},
        "no_original_CSV_truth_or_training_input_files_needed_at_inference": True,
        "source_calibration_loaded_from_model": True,
        "reference_seasonality_fit_on_supplied_normal_context": True,
        "retrospective_not_realtime": True,
        "evaluation": result["results"][method],
        "evaluation_scope": "custom semi-synthetic long sequences; not independently verified real faults",
        "pickle_security": "load only trusted local joblib files",
    })
    print("Handoff", destination, destination.stat().st_size, "bytes", flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    export()
