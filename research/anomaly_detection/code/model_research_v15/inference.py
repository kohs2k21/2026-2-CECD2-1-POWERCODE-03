"""Portable research candidates with causal temporal feature context."""
import copy
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
from model_evaluation.data import sha256
from model_research_v13.data import read, write
from model_research_v14.inference import ProcessResearchMonitor as EarlierMonitor, strip_training_payload
from model_research_v15.data import OUTPUT, prepare
from model_research_v15.features import augment


class ProcessResearchMonitor(EarlierMonitor):
    def features(self, native, completions): return augment(super().features(native, completions))


def export():
    selection = read(OUTPUT/"selection.json")
    if sha256(OUTPUT/"families.joblib") != selection["model_sha256"]: raise ValueError("Frozen models changed")
    families = joblib.load(OUTPUT/"families.joblib"); context = prepare()
    target = OUTPUT/"handoff"; target.mkdir(exist_ok=True); records = []
    for algorithm, original in families.items():
        thresholds = original.records()
        family = copy.deepcopy(original); strip_training_payload(family)
        monitor = ProcessResearchMonitor(family, context, algorithm)
        path = target/f"{algorithm}_v15_candidate.joblib"; joblib.dump(monitor, path, compress=3)
        records.append({"algorithm": algorithm, "file": path.name, "bytes": path.stat().st_size,
                        "sha256": sha256(path), "expert_adapters": sorted(family.experts),
                        "selection": selection["families"][algorithm], "thresholds": thresholds})
    import importlib.metadata as metadata
    write(target/"manifest.json", {"models": records, "requires_module": "model_research_v15.inference",
        "dependencies": {p: metadata.version(p) for p in ("numpy", "pandas", "scikit-learn", "scipy", "joblib")},
        "input": "Process contiguous minute grids + completed Process rows; observed prefix retained",
        "transaction_table_required": False, "runtime_reads_benchmark": False, "synthetic_prior_history": True,
        "case_disjoint_normal_calibration_search_confirmation": True, "production_auto_deploy": False})
    print("v15 IF, K-means and LOF research handoff exported", flush=True)


if __name__ == "__main__":
    from model_research_v15.inference import export
    export()
