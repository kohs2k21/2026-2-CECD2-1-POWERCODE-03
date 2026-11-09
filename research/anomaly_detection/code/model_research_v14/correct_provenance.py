"""Correct case-disjointness metadata, without refit or metric changes."""
from pathlib import Path
import shutil
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from model_evaluation.data import sha256
from model_research_v13.data import read, write, prepare
from model_research_v14.run import OUTPUT, partition_normals


def correct():
    path = OUTPUT / "selection.json"
    note = OUTPUT / "case_partition_audit.json"
    if note.exists(): return
    data = prepare(); reference, bank = partition_normals(data["bank"])
    calibration_ids = set(reference.case_id)
    observed_ids = set().union(*(set(v["observed"].case_id) for v in bank.values()))
    normal_ids = set().union(*(set(v["normal"].case_id) for v in bank.values()))
    confirmation_ids = set().union(*(set(v[c].loc[v[c].validation_split.eq("confirmation"), "case_id"])
                                     for v in data["bank"].values() for c in ("observed", "normal")))
    assert not calibration_ids & normal_ids
    assert not calibration_ids & confirmation_ids
    record = read(path); original_hash = sha256(path)
    backup = OUTPUT / "selection_before_case_audit.json"
    if backup.exists(): raise FileExistsError("Never overwrite the original selection metadata")
    shutil.copy2(path, backup)
    record.update({"normal_calibration_disjoint_search_cases": False,
                   "normal_calibration_disjoint_normal_search_cases": True,
                   "paired_observed_search_case_overlap": True,
                   "shared_calibration_observed_search_case_count": len(calibration_ids & observed_ids),
                   "provenance_metadata_corrected_without_model_or_metric_change": True,
                   "limitation": "Adaptive pilot; normal calibration shares paired observed SEARCH case IDs. Separate confirmation/final cases unused in calibration. Not blind operating performance."})
    write(path, record)
    write(note, {"normal_calibration_cases": len(calibration_ids),
                 "shared_with_observed_search_cases": len(calibration_ids & observed_ids),
                 "shared_with_normal_search_cases": len(calibration_ids & normal_ids),
                 "shared_with_confirmation_cases": len(calibration_ids & confirmation_ids),
                 "original_selection_sha256": original_hash, "corrected_selection_sha256": sha256(path),
                 "model_sha256_unchanged": sha256(OUTPUT / "families.joblib") == record["model_sha256"],
                 "model_refit": False, "metrics_changed": False,
                 "next_experiment": "Exclude BOTH observed and normal paired cases from search when calibration takes that case; obtain fresh confirmation before promotion."})
    print("Case-pair overlap disclosed; original metadata preserved; models/scores unchanged", flush=True)


if __name__ == "__main__": correct()
