"""Portable final routing candidate. No benchmark CSV at serving time."""
import copy
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
import joblib
from model_evaluation.data import sha256
from model_research_v13.data import read, write
from model_research_v14.inference import strip_training_payload
from model_research_v15.inference import ProcessResearchMonitor as FeatureMonitor
from model_research_v15.data import prepare
from model_research_v18.run import OUTPUT


class ProcessResearchMonitor(FeatureMonitor):
    pass


def export():
    selection = read(OUTPUT/"selection.json")
    if sha256(OUTPUT/"families.joblib") != selection["model_sha256"]: raise ValueError("Frozen choices changed")
    models = joblib.load(OUTPUT/"families.joblib"); context = prepare(); records = []
    target = OUTPUT/"handoff"; target.mkdir(exist_ok=True)
    for algorithm, original in models.items():
        thresholds = original.records()
        global_thresholds = [r for r in original.global_family.records()
                             if r["route"] == 0 and r["adapter"] == "GLOBAL"]
        adapter_thresholds = [r for r in original.adapter_family.records()
                              if r["route"] == 1 and r["adapter"] in original.experts]
        family = copy.deepcopy(original); family.strip_reference()
        strip_training_payload(family.global_family); strip_training_payload(family.adapter_family)
        monitor = ProcessResearchMonitor(family, context, algorithm)
        path = target/f"{algorithm}_v18_candidate.joblib"; joblib.dump(monitor, path, compress=3)
        records.append({"algorithm": algorithm, "file": path.name, "bytes": path.stat().st_size,
                        "sha256": sha256(path), "expert_adapters": sorted(family.experts), "thresholds": thresholds,
                        "global_thresholds": global_thresholds, "adapter_thresholds": adapter_thresholds,
                        "global_source_version": "v15", "adapter_source_version": "v17",
                        "selection": selection["families"][algorithm]})
    import importlib.metadata as metadata
    write(target/"manifest.json", {"models": records, "requires_module": "model_research_v18.inference",
          "dependencies": {p: metadata.version(p) for p in ("numpy", "pandas", "scikit-learn", "scipy", "joblib")},
          "input": "Process-derived contiguous minute grid + completed rows; observed prefix",
          "runtime_reads_benchmark": False, "transaction_table_required": False,
          "synthetic_prior_history": True, "production_auto_deploy": False})
    print("Final IF, K-means, LOF routed-fusion candidates exported", flush=True)


if __name__ == "__main__":
    from model_research_v18.inference import export
    export()
