"""Portable three-algorithm research candidates; no benchmark files at runtime."""
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
from model_evaluation.data import sha256
from model_research_v8.inference import ProcessResearchMonitor as PreviousMonitor
from model_research_v13.features import matched_precision
from model_research_v13.data import prepare, read, write
from model_research_v14.run import OUTPUT
from model_research_v14.models import CalibratedFamily


class ProcessResearchMonitor(PreviousMonitor):
    def __init__(self, family, context, algorithm):
        super().__init__(family, context["reference"], context["baseline"],
                         context["legacy_baselines"], context["history"], algorithm)
        self.anchor = context["anchor"]

    def features(self, native, completions):
        return self.anchor.augment(matched_precision(super().features(native, completions)))


def strip_training_payload(family):
    if isinstance(family, CalibratedFamily): family.strip_reference()
    # These are fit-time frames/caches; frozen score methods do not need them.
    attributes = ("calibration_frame", "threshold_frame", "calibration_components", "threshold_components")
    seen = set()

    def visit(obj):
        if id(obj) in seen: return
        seen.add(id(obj))
        for name in attributes:
            if hasattr(obj, name): delattr(obj, name)
        for name in ("base", "established", "sparse"):
            if hasattr(obj, name): visit(getattr(obj, name))
    visit(family)


def export():
    selection = read(OUTPUT / "selection.json")
    if sha256(OUTPUT / "families.joblib") != selection["model_sha256"]: raise ValueError("Frozen selection changed")
    families = joblib.load(OUTPUT / "families.joblib")
    context = prepare()
    target = OUTPUT / "handoff"; target.mkdir(exist_ok=True)
    models = []
    for algorithm, family in families.items():
        thresholds = family.records()
        family = copy.deepcopy(family); strip_training_payload(family)
        monitor = ProcessResearchMonitor(family, context, algorithm)
        path = target / f"{algorithm}_v14_candidate.joblib"
        joblib.dump(monitor, path, compress=3)
        models.append({"algorithm": algorithm, "file": path.name, "bytes": path.stat().st_size,
                       "sha256": sha256(path), "expert_adapters": sorted(family.experts),
                       "selection": selection["families"][algorithm], "thresholds": thresholds})
    import importlib.metadata as metadata
    write(target / "manifest.json", {"models": models,
          "dependencies": {p: metadata.version(p) for p in ("numpy", "pandas", "scikit-learn", "scipy", "joblib")},
          "requires_module": "model_research_v14.inference", "input": "Process minute grids + completed Process rows",
          "transaction_table_required": False, "runtime_reads_benchmark": False,
          "synthetic_prior_history": True, "operating_models_overwritten": False,
          "trusted_pickle_only": True, "production_promotion": "Requires real-log validation; no automatic deployment"})
    print("v14 handoff exported: IF, K-means, LOF", flush=True)


if __name__ == "__main__":
    from model_research_v14.inference import export
    export()
