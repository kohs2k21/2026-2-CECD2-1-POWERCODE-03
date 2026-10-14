"""Evaluate the frozen v12 handoff; no fit, threshold change, or case selection."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import numpy as np
import pandas as pd
from benchmark_difficulty.adjust import DEST, WORK, SOURCE, inventory, read_json, write_json
from model_evaluation.data import labels_for_rows, observed_windows, unix_seconds, verify_benchmark, sha256
from model_evaluation.metrics import Counts
from model_evaluation.models import METHODS
from model_evaluation.evaluate import event_results
from model_improvement.data import final_rows, control_grid
from model_research_v8.inference import load
from model_research_v8.features import FEATURES
from model_improvement.refine import ALL_FEATURES as OLD_FEATURES

OUTPUT = ROOT / "reports/benchmark_mild_v1"
BASE = ROOT / "reports/model_research_v12"


def match_saved_precision(frame):
    # Original v12 scores were computed from float32 NPZ feature caches promoted
    # back to float64. Use the SAME precision for an apples-to-apples comparison.
    frame = frame.copy()
    columns = list(dict.fromkeys((*OLD_FEATURES, *FEATURES)))
    frame[columns] = frame[columns].astype(np.float32).astype(float)
    return frame


def evaluate():
    # A complete immutable CSV export can be scored while independent rule
    # validation runs. Final metrics are released ONLY after full validation.
    if (DEST / "validation.json").exists():
        verify_benchmark(DEST, ROOT / "reports/evaluation_v2_work/inputs.json")
    else:
        manifest = read_json(DEST / "manifest.json")
        for folder in [DEST, *(DEST / name for name in manifest["datasets"])]:
            for filename, info in read_json(folder / "manifest.json")["files"].items():
                if sha256(folder / filename) != info["sha256"]:
                    raise ValueError("CSV export is incomplete or changed")
    frozen = read_json(WORK / "frozen_protocol.json")
    if inventory(SOURCE) != frozen["original_files"]:
        raise ValueError("Original benchmark changed")
    handoff = BASE / "handoff"
    monitors = {r["algorithm"]: load(handoff / r["file"], r["sha256"])
                for r in read_json(handoff / "manifest.json")["models"]}
    model_hashes = {k: sha256(ROOT / k) for k in frozen["models"]}
    if model_hashes != frozen["models"]:
        raise ValueError("Frozen v12 models changed")
    OUTPUT.mkdir(exist_ok=True)
    protocol = {"difficulty_profile": read_json(WORK / "frozen_protocol.json")["profile"],
        "model_sha256": model_hashes, "benchmark_manifest_sha256": sha256(DEST / "manifest.json"),
        "model_refit": False, "threshold_refit": False, "case_selection_by_score": False,
        "point_adjustment": False, "mixed_hard_byte_identical": True, "full_validation_required_before_summary": True,
        "feature_precision": "float32 round-trip matching original v12 NPZ evaluation; model/threshold unchanged",
        "interpretation": "Benchmark difficulty sensitivity; NOT improved detector performance or blind evaluation."}
    if (OUTPUT / "protocol.json").exists():
        if read_json(OUTPUT / "protocol.json") != protocol:
            raise ValueError("Resume inputs changed")
    else:
        write_json(OUTPUT / "protocol.json", protocol)
    pooled, supported, datasets, normal_matches = Counts(), Counts(), [], {}
    for name in read_json(DEST / "manifest.json")["datasets"]:
        folder, destination = DEST / name, OUTPUT / name
        destination.mkdir(exist_ok=True)
        if (destination / "metrics.json").exists():
            result = read_json(destination / "metrics.json")
            for cohort, digest in result["arrays_sha256"].items():
                if sha256(destination / (cohort + ".npz")) != digest:
                    raise ValueError("Changed cached predictions")
        elif name == "mixed_hard":
            result = read_json(BASE / name / "metrics.json")
            for cohort, digest in result["arrays_sha256"].items():
                shutil.copy2(BASE / name / (cohort + ".npz"), destination / (cohort + ".npz"))
                if sha256(destination / (cohort + ".npz")) != digest:
                    raise ValueError("Hard unchanged cache mismatch")
            result["hard_byte_identical_reuse"] = True
            write_json(destination / "metrics.json", result)
        else:
            print(f"mild evaluation {name}: CSV inputs, frozen models", flush=True)
            cases = pd.read_csv(folder / "cases.csv", encoding="utf-8-sig")
            events = pd.read_csv(folder / "events.csv", encoding="utf-8-sig")
            labels = labels_for_rows(folder)
            raw = final_rows(folder, labels, cases, events)
            del labels
            observed = observed_windows(folder)
            normal = control_grid(observed, raw["normal"])
            case_map = {cid: i for i, cid in enumerate(cases.case_id)}
            arrays, counts, covered_counts = {}, Counts(), Counts()
            for cohort, native in (("observed", observed), ("normal", normal)):
                parts = []
                case_ids = cases.case_id.to_numpy()
                for start in range(0, len(cases), 500):
                    selected = set(case_ids[start:start + 500])
                    minute = native.loc[native.case_id.isin(selected)].copy()
                    complete = raw[cohort].loc[raw[cohort].case_id.isin(selected)].copy()
                    oracle = minute.set_index(["case_id", "window_start"])
                    # Remove every label/counterfactual metadata field before inference.
                    inputs = minute.drop(columns=["window_is_anomaly", "scenario_types", "event_ids",
                                                   "normal_execution_arrivals", "observed_execution_arrivals"], errors="ignore")
                    frame = match_saved_precision(monitors["if"].features(inputs, complete))
                    m_if, p_if, c_if = monitors["if"].score_features(frame)
                    m_k, p_k, c_k = monitors["kmeans"].score_features(frame)
                    np.testing.assert_array_equal(c_if, c_k)
                    m, p = np.column_stack([m_if, m_k]), np.column_stack([p_if, p_k])
                    np.testing.assert_array_equal(p, m > 0)
                    for base in (0, 3):
                        np.testing.assert_array_equal(m[~c_if, base], m[~c_if, base + 1])
                        np.testing.assert_array_equal(m[~c_if, base], m[~c_if, base + 2])
                    truth = np.zeros(len(frame), bool)
                    types = pd.Series("", index=frame.index)
                    if cohort == "observed":
                        keys = pd.MultiIndex.from_frame(frame[["case_id", "window_start"]])
                        aligned = oracle.reindex(keys)
                        if aligned.window_is_anomaly.isna().any():
                            raise ValueError("Unmatched ground truth")
                        truth = aligned.window_is_anomaly.to_numpy(bool)
                        types = aligned.scenario_types.fillna("").reset_index(drop=True)
                    bits = np.zeros(len(frame), np.uint8)
                    from evaluation_benchmark.generate import SCENARIOS
                    for bit, kind in enumerate(SCENARIOS):
                        bits |= types.str.split(";").map(lambda x: kind in x).to_numpy(np.uint8) << bit
                    part = {"margin": m.astype(np.float32), "prediction": p, "covered": c_if,
                        "truth": truth, "valid": np.ones(len(frame), bool),
                        "case_index": frame.case_id.map(case_map).to_numpy(np.int32),
                        "window_end_seconds": unix_seconds(frame.window_end), "scenario_bits": bits,
                        "traffic_assessed": frame.traffic_assessed.to_numpy(bool),
                        "latency_assessed": frame.latency_assessed.to_numpy(bool)}
                    counts.update(truth, p); covered_counts.update(truth, p, c_if)
                    parts.append(part)
                    print(f"  {cohort}: {min(start + 500, len(cases))}/{len(cases)} cases", flush=True)
                arrays[cohort] = {key: np.concatenate([p[key] for p in parts]) for key in parts[0]}
                np.savez_compressed(destination / (cohort + ".npz"), **arrays[cohort])
            # Same normal Process CSVs must yield the same predictions at shared times.
            old_cases = pd.read_csv(SOURCE / name / "cases.csv", encoding="utf-8-sig")
            with np.load(BASE / name / "normal.npz", allow_pickle=False) as old:
                old_ids = old_cases.case_id.to_numpy()[old["case_index"]]
                previous = pd.DataFrame(old["prediction"], index=pd.MultiIndex.from_arrays([old_ids, old["window_end_seconds"]]))
            a = arrays["normal"]
            keys = pd.MultiIndex.from_arrays([cases.case_id.to_numpy()[a["case_index"]], a["window_end_seconds"]])
            shared = keys.isin(previous.index)
            np.testing.assert_array_equal(a["prediction"][shared], previous.reindex(keys[shared]).to_numpy(bool))
            result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(),
                "supported_only_metrics": covered_counts.records(), "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                "normal_shared_decisions": int(shared.sum()), "normal_shared_predictions_equal": True,
                "normal_new_decisions": int((~shared).sum()),
                "arrays_sha256": {c: sha256(destination / (c + ".npz")) for c in arrays}}
            write_json(destination / "metrics.json", result)
            print([(r["method"], round(100 * r["f1"], 2)) for r in result["metrics"]], flush=True)
        for cohort in result["arrays_sha256"]:
            with np.load(destination / (cohort + ".npz"), allow_pickle=False) as a:
                np.testing.assert_array_equal(a["prediction"], a["margin"] > 0)
        pooled.values += Counts.from_records(result["metrics"]).values
        supported.values += Counts.from_records(result["supported_only_metrics"]).values
        datasets.append(result)
        normal_matches[name] = result.get("normal_shared_decisions", "Hard byte-identical")
    from evaluation_benchmark.generate import SCENARIOS
    macro = [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in datasets if d["dataset"] in SCENARIOS]))
            for k in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")}} for i, m in enumerate(METHODS)]
    verify_benchmark(DEST, ROOT / "reports/evaluation_v2_work/inputs.json")
    write_json(OUTPUT / "summary.json", {"pooled": pooled.records(), "supported_only": supported.records(),
        "datasets": datasets, "basic_macro": macro, "normal_prediction_verification": normal_matches, "protocol": protocol})
    if inventory(SOURCE) != frozen["original_files"] or {k: sha256(ROOT / k) for k in frozen["models"]} != model_hashes:
        raise ValueError("Frozen source/models changed during evaluation")
    print("Evaluation complete; original benchmark and detector hashes unchanged", flush=True)


if __name__ == "__main__":
    evaluate()
