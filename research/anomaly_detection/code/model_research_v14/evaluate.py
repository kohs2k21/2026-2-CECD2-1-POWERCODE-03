"""Frozen, nine-strategy minute/event evaluation; no point adjustment.

Feature caches are keyed by the exact benchmark manifest and source model.
Build features once from CSV; no anomaly label enters the model inputs.
"""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import joblib
import numpy as np
import pandas as pd
from evaluation_benchmark.generate import SCENARIOS
from model_evaluation.data import labels_for_rows, observed_windows, unix_seconds, verify_benchmark, sha256
from model_evaluation.metrics import metric_record
from model_improvement.data import final_rows, control_grid
from model_research_v13.data import baseline_monitors, prepare, read, write
from model_research_v13.features import matched_precision
from model_research_v13.models import METHODS
from model_research_v14.run import OUTPUT

BENCHMARK = ROOT / "data/evaluation"


class Counts:
    def __init__(self): self.values = np.zeros((len(METHODS), 4), np.int64)

    def update(self, y, pred, mask=None):
        for i, p in enumerate(pred.T):
            m = np.ones(len(y), bool) if mask is None else (mask[:, i] if mask.ndim == 2 else mask)
            yy, pp = y[m], p[m]
            self.values[i] += [np.sum(~yy & ~pp), np.sum(~yy & pp), np.sum(yy & ~pp), np.sum(yy & pp)]

    def records(self): return [{"method": name, **metric_record(*row)} for name, row in zip(METHODS, self.values)]


def event_results(observed, normal, events, cases):
    case_map = {c: i for i, c in enumerate(cases.case_id)}
    alerts = np.zeros((len(cases), len(METHODS)), bool)
    np.logical_or.at(alerts, normal["case_index"], normal["prediction"])
    records = []
    for bit, name in enumerate(SCENARIOS):
        relevant = events.loc[events.scenario.eq(name)]
        if relevant.empty: continue
        if relevant.case_id.duplicated().any(): raise ValueError("Multiple same-type events per case")
        ids = relevant.case_id.map(case_map).to_numpy(int)
        starts = unix_seconds(relevant.startTime)
        select = (observed["scenario_bits"] & (1 << bit)) > 0
        for i, method in enumerate(METHODS):
            fired = select & observed["prediction"][:, i]
            first = np.full(len(cases), np.inf)
            np.minimum.at(first, observed["case_index"][fired], observed["window_end_seconds"][fired])
            detected = np.isfinite(first[ids])
            delay = first[ids][detected] - starts[detected]
            records.append({"scenario": name, "method": method, "events": len(ids), "detected": int(detected.sum()),
                            "event_recall": float(detected.mean()),
                            "median_delay_seconds_detected_only": float(np.median(delay)) if len(delay) else None})
    return {"per_type": records, "normal_case_false_alarm_rate": {
        m: float(alerts[:, i].mean()) for i, m in enumerate(METHODS)}, "point_adjustment": False}


def cache_folder():
    return OUTPUT / "feature_cache" / sha256(BENCHMARK / "manifest.json")[:16]


def build_cache():
    verify_benchmark(BENCHMARK, ROOT / "reports/evaluation_v2_work/inputs.json")
    source = baseline_monitors()["if"]
    development = prepare()
    folder = cache_folder(); folder.mkdir(parents=True, exist_ok=True)
    protocol = {"benchmark_manifest_sha256": sha256(BENCHMARK / "manifest.json"),
                "development_sha256": read(ROOT / "reports/model_research_v13/preparation.json")["data_sha256"],
                "feature_precision": "float32 round trip for frozen baseline compatibility",
                "numeric_features_exclude_ground_truth": True, "cases_per_chunk": 500}
    if (folder / "protocol.json").exists():
        if read(folder / "protocol.json") != protocol: raise ValueError("Cache provenance changed")
    else: write(folder / "protocol.json", protocol)
    for name in read(BENCHMARK / "manifest.json")["datasets"]:
        target = folder / name; target.mkdir(exist_ok=True)
        if (target / "index.json").exists():
            for record in read(target / "index.json"):
                if sha256(target / record["file"]) != record["sha256"]: raise ValueError("Feature cache changed")
            continue
        print(f"feature cache {name}: reading raw CSVs", flush=True)
        data = BENCHMARK / name
        cases = pd.read_csv(data / "cases.csv", encoding="utf-8-sig")
        events = pd.read_csv(data / "events.csv", encoding="utf-8-sig")
        labels = labels_for_rows(data)
        rows = final_rows(data, labels, cases, events); del labels
        observed = observed_windows(data)
        normal = control_grid(observed, rows["normal"])
        mapping = {c: i for i, c in enumerate(cases.case_id)}
        records = []
        for cohort, native in (("observed", observed), ("normal", normal)):
            for offset in range(0, len(cases), 500):
                ids = set(cases.case_id.iloc[offset:offset+500])
                minute = native.loc[native.case_id.isin(ids)]
                complete = rows[cohort].loc[rows[cohort].case_id.isin(ids)]
                oracle = minute.set_index(["case_id", "window_start"])
                inputs = minute.drop(columns=["window_is_anomaly", "scenario_types", "event_ids",
                    "normal_execution_arrivals", "observed_execution_arrivals"], errors="ignore")
                frame = development["anchor"].augment(matched_precision(source.features(inputs, complete)))
                keys = pd.MultiIndex.from_frame(frame[["case_id", "window_start"]])
                aligned = oracle.reindex(keys)
                truth = aligned.window_is_anomaly.to_numpy(bool) if cohort == "observed" else np.zeros(len(frame), bool)
                types = aligned.scenario_types.fillna("").reset_index(drop=True) if cohort == "observed" else pd.Series("", index=frame.index)
                bits = np.zeros(len(frame), np.uint8)
                for bit, kind in enumerate(SCENARIOS):
                    bits |= types.str.split(";").map(lambda x: kind in x).to_numpy(np.uint8) << bit
                meta = {"truth": truth, "case_index": frame.case_id.map(mapping).to_numpy(np.int32),
                        "window_end_seconds": unix_seconds(frame.window_end), "scenario_bits": bits,
                        "traffic_assessed": frame.traffic_assessed.to_numpy(bool),
                        "latency_assessed": frame.latency_assessed.to_numpy(bool)}
                path = target / f"{cohort}_{offset:05d}.joblib"
                joblib.dump({"frame": frame, "metadata": meta}, path, compress=3)
                records.append({"file": path.name, "sha256": sha256(path), "cohort": cohort, "decisions": len(frame)})
                print(f"  {cohort}: {min(offset+500, len(cases))}/{len(cases)}", flush=True)
        write(target / "index.json", records)
    return folder


def evaluate():
    selection = read(OUTPUT / "selection.json")
    if sha256(OUTPUT / "families.joblib") != selection["model_sha256"]: raise ValueError("Frozen model changed")
    families = joblib.load(OUTPUT / "families.joblib")
    folder = cache_folder()
    if not (folder / "protocol.json").exists(): raise FileNotFoundError("Run --stage cache first")
    manifest_hash = sha256(BENCHMARK / "manifest.json")
    destination = OUTPUT / ("evaluation_" + manifest_hash[:16]); destination.mkdir(exist_ok=True)
    pooled, supported, fallback = Counts(), Counts(), Counts()
    adapter_counts, datasets, sample_frames = {}, [], []
    examples = {method: {kind: [] for kind in ("TP", "FP", "FN")} for method in METHODS}
    for name in read(BENCHMARK / "manifest.json")["datasets"]:
        target = destination / name; target.mkdir(exist_ok=True)
        cases = pd.read_csv(BENCHMARK / name / "cases.csv", encoding="utf-8-sig")
        events = pd.read_csv(BENCHMARK / name / "events.csv", encoding="utf-8-sig")
        counts, covered_counts = Counts(), Counts()
        pieces = {"observed": [], "normal": []}
        timings = {a: 0. for a in families}
        for record in read(folder / name / "index.json"):
            path = folder / name / record["file"]
            if sha256(path) != record["sha256"]: raise ValueError("Changed feature input")
            payload = joblib.load(path); frame, meta = payload["frame"], payload["metadata"]
            margins, predictions, coverages = [], [], []
            for a in ("if", "kmeans", "lof"):
                start = time.perf_counter(); m, p, c = families[a].score(frame)
                timings[a] += time.perf_counter() - start
                margins.append(m); predictions.append(p); coverages.append(np.repeat(c[:, None], 3, axis=1))
            margin, pred = np.column_stack(margins), np.column_stack(predictions)
            covered = np.column_stack(coverages)
            np.testing.assert_array_equal(pred, margin > 0)
            for base in (0, 3, 6):
                uncovered = ~covered[:, base]
                np.testing.assert_array_equal(margin[uncovered, base], margin[uncovered, base+1])
                np.testing.assert_array_equal(margin[uncovered, base], margin[uncovered, base+2])
            pieces[record["cohort"]].append({**meta, "margin": margin, "prediction": pred, "covered": covered})
            y = meta["truth"]
            for i, method in enumerate(METHODS):
                for kind, mask in (("TP", y & pred[:, i]), ("FP", ~y & pred[:, i]), ("FN", y & ~pred[:, i])):
                    if len(examples[method][kind]) >= 4 or not mask.any(): continue
                    j = int(np.flatnonzero(mask)[0]); row = frame.iloc[j]
                    examples[method][kind].append({"method": method, "outcome": kind, "dataset": name,
                        "case_id": str(row.case_id), "adapterType": str(row.adapterType), "processId": str(row.processId),
                        "window_start": str(row.window_start), "score_minus_threshold": float(margin[j, i]),
                        "decision": bool(pred[j, i]), "actual_anomaly": bool(y[j]), "expert_available": bool(covered[j, i]),
                        **{c: float(row[c]) if np.isfinite(row[c]) else None for c in (
                            "native_execution_arrivals", "zero_run", "arrival_dev_1m", "duration_p95_ms",
                            "duration_upper_max_1m", "duration_lower_max_1m", "fixed_positive_cusum")},
                        "traffic_assessed": bool(row.traffic_assessed), "latency_assessed": bool(row.latency_assessed),
                        "explanation_scope": "Actual score/threshold decision plus observed feature clues; not causal feature attribution"})
            sample_ids = np.random.default_rng(42 + len(sample_frames)).choice(len(frame), min(120, len(frame)), replace=False)
            sample = frame.iloc[sample_ids].copy()
            sample["is_anomaly"] = y[sample_ids]
            sample["dataset"] = name; sample["cohort"] = record["cohort"]
            sample_frames.append(sample)
            counts.update(y, pred); covered_counts.update(y, pred, covered)
            pooled.update(y, pred); supported.update(y, pred, covered); fallback.update(y, pred, ~covered)
            for adapter in frame.adapterType.unique():
                adapter_counts.setdefault(adapter, Counts()).update(y, pred, frame.adapterType.eq(adapter).to_numpy())
        arrays = {}
        for cohort, parts in pieces.items():
            arrays[cohort] = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
            np.savez_compressed(target / (cohort + ".npz"), **arrays[cohort])
        result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(),
                  "supported_only_metrics": covered_counts.records(),
                  "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                  "coverage_per_algorithm": {a: float(np.concatenate([v["covered"][:, i*3] for v in arrays.values()]).mean())
                                             for i, a in enumerate(("if", "kmeans", "lof"))},
                  "score_seconds": timings,
                  "arrays_sha256": {c: sha256(target / (c + ".npz")) for c in arrays}}
        write(target / "metrics.json", result); datasets.append(result)
        print(name, [(r["method"], round(r["f1"]*100, 2)) for r in result["metrics"]], flush=True)
    macro = [{"method": method, **{k: float(np.mean([d["metrics"][i][k] for d in datasets if d["dataset"] in SCENARIOS]))
              for k in ("accuracy", "precision", "recall", "f1", "normal_false_positive_rate")}}
             for i, method in enumerate(METHODS)]
    summary = {"pooled": pooled.records(), "supported_only": supported.records(), "fallback_only": fallback.records(),
               "per_adapter": {a: c.records() for a, c in adapter_counts.items()}, "basic_macro": macro, "datasets": datasets,
               "protocol": {"model_sha256": selection["model_sha256"], "benchmark_manifest_sha256": manifest_hash,
                   "no_refit_on_final": True, "point_adjustment": False, "decision_unit": "one observed process stream minute",
                   "benchmark_modified_in_this_experiment": False, "baseline": "frozen v12 on exactly same current benchmark",
                   "limitation": "Adaptive research; not blind external production testing"}}
    write(destination / "summary.json", summary)
    write(destination / "decision_examples.json", [r for outcomes in examples.values() for records in outcomes.values() for r in records])
    joblib.dump(pd.concat(sample_frames, ignore_index=True), destination / "analysis_samples.joblib", compress=3)
    write(OUTPUT / "evaluation_index.json", {"current": str(destination.relative_to(ROOT)).replace("\\", "/"),
          "benchmark_manifest_sha256": manifest_hash, "summary_sha256": sha256(destination / "summary.json")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--stage", choices=("cache", "evaluate"), default="evaluate")
    args = parser.parse_args()
    build_cache() if args.stage == "cache" else evaluate()
