"""Authorized in-place mild-v2 preparation, with recoverable activation.

Replay original seeds, reproduce mild-v1 EXACTLY, then strengthen its injected
excess by 1.1. Never select cases by detector scores. Hard/normals are unchanged.
Staging is outside data/, which retains just ONE active benchmark.
"""
import argparse
import csv
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
from benchmark_difficulty import adjust
from model_evaluation.data import sha256, verify_benchmark

ACTIVE = ROOT/"data/evaluation"
WORK = ROOT/"reports/benchmark_mild_v2_work"
STAGING = WORK/"export"
BACKUP = ROOT/"reports/benchmark_before_mild_v2_20261014"
PROFILE = Path(__file__).with_name("mild_v2_profile.json")
OUTPUT = ROOT/"reports/benchmark_mild_v2"


def configure():
    adjust.WORK, adjust.DEST, adjust.PROFILE = WORK, STAGING, PROFILE


def generate():
    configure(); OUTPUT.mkdir(exist_ok=True)
    if (WORK/"generated").exists(): raise FileExistsError("Never overwrite generation")
    if sha256(ACTIVE/"manifest.json") != "7f2dc4351af6166416f7694df97fb2050cab8d348e5b29d8451fcc36031f3b36":
        raise ValueError("Current benchmark changed; preserve user modifications")
    old = adjust.read_json(Path(__file__).with_name("profile.json"))
    saved = {}
    for name in adjust.SCENARIOS:
        with (ACTIVE/name/"cases.csv").open(encoding="utf-8-sig", newline="") as f:
            saved.update({r["case_id"]: r["unique_content_fingerprint"] for r in csv.DictReader(f)})
    parent = {"parent_files": adjust.inventory(ACTIVE), "parent_manifest_sha256": sha256(ACTIVE/"manifest.json"),
              "profile": adjust.read_json(PROFILE), "active": "data/evaluation", "backup": str(BACKUP.relative_to(ROOT)),
              "case_selection_by_detector": False, "models_refit_for_difficulty": False}
    adjust.write_json(OUTPUT/"parent_protocol.json", parent)
    original_strengthen = adjust.strengthen

    def sequential(case, profile):
        case, _ = original_strengthen(case, old)
        if adjust.original_fingerprint(case) != saved[case.case_id]:
            raise ValueError("Mild-v1 reconstruction mismatch; never replace/substitute a case")
        return original_strengthen(case, profile)

    adjust.strengthen = sequential
    try: adjust.generate()
    finally: adjust.strengthen = original_strengthen
    if adjust.inventory(ACTIVE) != parent["parent_files"]: raise ValueError("Active data changed during generation")


def finish():
    configure()
    if not (STAGING/"mixed_hard").exists(): adjust.finish()
    else:
        root = adjust.read_json(STAGING/"manifest.json")
        if root["datasets"] != [*adjust.SCENARIOS, "mixed_hard"]:
            raise ValueError("Incomplete resume; do not overwrite existing outputs")
    sys.path.insert(0, str(ROOT/"code/evaluation_benchmark"))
    from evaluation_benchmark.validate import validate
    validate(STAGING)
    verify_benchmark(STAGING, ROOT/"reports/evaluation_v2_work/inputs.json")
    parent = adjust.read_json(OUTPUT/"parent_protocol.json")
    if adjust.inventory(ACTIVE) != parent["parent_files"]: raise ValueError("Active benchmark changed")
    for name in adjust.SCENARIOS:
        for table in ("normal_processes.csv", "normal_transactions_context.csv"):
            if sha256(STAGING/name/table) != sha256(ACTIVE/name/table): raise ValueError("Normal control changed")
    if adjust.inventory(STAGING/"mixed_hard") != adjust.inventory(ACTIVE/"mixed_hard"):
        raise ValueError("Hard changed")
    validation = adjust.read_json(STAGING/"validation.json")
    if not validation["passed"]: raise ValueError("Hard-rule validation failed")
    adjust.write_json(OUTPUT/"audit.json", {"passed": True, "csv_files": len(list(STAGING.rglob("*.csv"))),
        "all_original_cases_preserved": True, "parent_exactly_reconstructed": True,
        "normal_controls_byte_identical": True, "mixed_hard_byte_identical": True,
        "hard_rule_validation": validation, "manifest_sha256": sha256(STAGING/"manifest.json")})


def preflight():
    if BACKUP.exists(): raise FileExistsError("Recovery backup exists")
    parent = adjust.read_json(OUTPUT/"parent_protocol.json")
    if adjust.inventory(ACTIVE) != parent["parent_files"]: raise ValueError("Active has user edits")
    if not adjust.read_json(OUTPUT/"audit.json")["passed"]: raise ValueError("Staging not validated")
    verify_benchmark(STAGING, ROOT/"reports/evaluation_v2_work/inputs.json")
    adjust.write_json(OUTPUT/"activation.json", {"state": "preflight_passed", "backup": str(BACKUP.relative_to(ROOT)),
        "manifest_sha256": sha256(STAGING/"manifest.json"), "user_date": "2026-10-14", "parent_source_unchanged": True})


def verify():
    receipt = adjust.read_json(OUTPUT/"activation.json")
    parent = adjust.read_json(OUTPUT/"parent_protocol.json")
    if STAGING.exists(): raise ValueError("Staging not moved into active path")
    if adjust.inventory(BACKUP) != parent["parent_files"]: raise ValueError("Recovery backup changed")
    if sha256(ACTIVE/"manifest.json") != receipt["manifest_sha256"]: raise ValueError("Promoted data changed")
    verify_benchmark(ACTIVE, ROOT/"reports/evaluation_v2_work/inputs.json")
    receipt.update(state="active", verified=True, csv_files=len(list(ACTIVE.rglob("*.csv"))),
                   original_backup_exact=True, one_active_benchmark=True)
    adjust.write_json(OUTPUT/"activation.json", receipt)
    print("Mild-v2 active; exact old benchmark recovery backup retained; Hard/normals unchanged", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--stage", choices=("generate", "finish", "preflight", "verify"), required=True)
    globals()[p.parse_args().stage]()
