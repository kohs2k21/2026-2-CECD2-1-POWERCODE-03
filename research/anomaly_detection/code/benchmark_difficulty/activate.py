"""Verify a recoverable in-place promotion; filesystem moves use PowerShell.

No datasets are regenerated and no trained model or threshold is changed.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from benchmark_difficulty.adjust import inventory, read_json, write_json
from model_evaluation.data import sha256, verify_benchmark

ACTIVE = ROOT / "data/evaluation"
STAGING = ROOT / "data/evaluation_mild_v1"
BACKUP = ROOT / "reports/benchmark_before_mild_20261014"
RECEIPT = ROOT / "reports/benchmark_mild_v1/activation.json"
FROZEN = ROOT / "reports/benchmark_mild_v1_work/frozen_protocol.json"


def preflight():
    if BACKUP.exists() or RECEIPT.exists():
        raise FileExistsError("Never overwrite a backup or activation record")
    frozen = read_json(FROZEN)
    if inventory(ACTIVE) != frozen["original_files"]:
        raise ValueError("Existing benchmark has user changes; do not overwrite")
    manifest, _ = verify_benchmark(STAGING, ROOT / "reports/evaluation_v2_work/inputs.json")
    if manifest["benchmark_version"] != "esb-process-synthetic-mild-v1":
        raise ValueError("Unexpected replacement version")
    if not read_json(ROOT / "reports/benchmark_mild_v1/audit.json")["passed"]:
        raise ValueError("Replacement audit did not pass")
    models = {name: sha256(ROOT / name) for name in frozen["models"]}
    if models != frozen["models"]:
        raise ValueError("Models changed")
    csvs = {str(p.relative_to(STAGING)).replace("\\", "/"): sha256(p) for p in STAGING.rglob("*.csv")}
    record = {"state": "preflight_passed", "user_date": "2026-10-14", "timezone": "Asia/Seoul",
        "active_path": "data/evaluation", "former_staging_path": "data/evaluation_mild_v1",
        "backup_path": "reports/benchmark_before_mild_20261014", "csv_hashes": csvs,
        "benchmark_manifest_sha256": sha256(STAGING / "manifest.json"),
        "models": models, "regenerated": False, "model_refit": False, "threshold_refit": False}
    write_json(RECEIPT, record)
    print("Preflight passed: validated replacement, 67 CSVs, no user changes; backup path is empty", flush=True)


def verify():
    record, frozen = read_json(RECEIPT), read_json(FROZEN)
    if STAGING.exists():
        raise ValueError("Separate staging dataset still exists")
    if inventory(BACKUP) != frozen["original_files"]:
        raise ValueError("Backup does not exactly preserve original files")
    if sha256(ACTIVE / "manifest.json") != record["benchmark_manifest_sha256"]:
        raise ValueError("Promoted manifest changed")
    for name, expected in record["csv_hashes"].items():
        if sha256(ACTIVE / name) != expected:
            raise ValueError("Promoted CSV differs from validated replacement: " + name)
    if {name: sha256(ROOT / name) for name in record["models"]} != record["models"]:
        raise ValueError("Model files changed")
    verify_benchmark(ACTIVE, ROOT / "reports/evaluation_v2_work/inputs.json")
    report = read_json(ACTIVE / "validation.json")
    record.update(state="active", verified=True, csv_files=len(record["csv_hashes"]),
        validation_passed=report["passed"], original_backup_exact=True,
        separate_staging_removed_by_move=True, model_files_unchanged=True)
    write_json(RECEIPT, record)
    print({k: v for k, v in record.items() if k not in ("csv_hashes", "models")}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("preflight", "verify"), required=True)
    (preflight if parser.parse_args().stage == "preflight" else verify)()
