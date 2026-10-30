"""Broader NORMAL replay from disjoint original source partitions only.

Do not use reserved validation templates or final paired controls in model fit.
Cadence histories remain synthetic; this is diversity augmentation, not real
production traffic and not more independent source transactions.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
import pandas as pd
from evaluation_benchmark.generate import Case
from model_evaluation.data import ReplayCatalog, source_references, history_lookup, sha256
from model_evaluation.windowing import window_reference_split
from model_improvement.data import rich_rows
from model_research_v13.data import prepare, read, write, trusted_cache, baseline_monitors
from model_research_v13.features import matched_precision

OUTPUT = ROOT / "reports/model_research_v14"


def expanded_partitions():
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / "normal_diversity.joblib"
    if path.exists(): return trusted_cache(path, read(OUTPUT / "normal_diversity.json")["sha256"])
    old = prepare()
    source = read(ROOT / "reports/evaluation_v2_work/inputs.json")
    record = read(ROOT / "reports/window_search_v2/selection.json")
    state = trusted_cache(ROOT / "reports/window_search_v2/training_inputs.joblib", record["training_inputs_sha256"])
    original, _, _, _ = source_references(source)
    _, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    history = history_lookup(ROOT / "data/evaluation")
    processor = baseline_monitors()["if"]
    parts, provenance = {}, {}
    for si, name in enumerate(("train", "calibration", "threshold")):
        if sets[name] & set(state["reserved_template_ids"]): raise ValueError("Reserved source leakage")
        templates = [t for t in source["templates"] if t["template_id"] in sets[name]]
        catalog = ReplayCatalog(templates)
        streams = 36 if name == "train" else 20
        frames, used = [], set()
        for i in range(len(catalog.adapter_names)*streams):
            case = Case(catalog, "input_stop", i, 1618000 + si*10000, history)
            case.baseline(60)
            native = pd.DataFrame(case.export()["windows.csv"])
            native = native.loc[native.scope.eq("TARGET")].reset_index(drop=True)
            rows = rich_rows(case, case.normal)
            used.update(m["template"]["template_id"] for m in case.normal_meta.values())
            frame = processor.features(native, rows)
            frames.append(old["anchor"].augment(matched_precision(frame)))
        additions = pd.concat(frames, ignore_index=True)
        if set(additions.case_id) & set(old["partitions"][name].case_id): raise ValueError("Replay case collision")
        parts[name] = pd.concat([old["partitions"][name], additions], ignore_index=True)
        provenance[name] = {"old_windows": len(old["partitions"][name]), "additional_windows": len(additions),
                            "additional_streams": int(additions.case_id.nunique()), "sampled_source_templates": len(used),
                            "available_source_templates": len(sets[name]), "minutes_per_stream": 60}
        print(f"normal diversity {name}: {len(parts[name]):,} windows, {len(used):,} sampled source templates", flush=True)
    result = {"partitions": parts, "source_support": support}
    joblib.dump(result, path, compress=3)
    write(OUTPUT / "normal_diversity.json", {"sha256": sha256(path), "partitions": provenance,
          "reserved_validation_templates_used": False, "final_controls_used": False,
          "normal_source_partition_disjoint": True, "synthetic_cadence": True,
          "new_independent_source_rows_created": False})
    return result


if __name__ == "__main__": expanded_partitions()
