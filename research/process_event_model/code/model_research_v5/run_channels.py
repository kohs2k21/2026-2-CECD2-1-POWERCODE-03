"""v6 fresh validation, two-channel IF/KMeans and frozen original-test evaluation.

v5's poor tradeoff motivated the channel architecture. No v6 confirmation/test
performance is consulted to choose model/operating points. Earlier test-driven
research motivation means the original final benchmark is still NOT blind.
"""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256, verify_benchmark
from model_evaluation.metrics import Counts
from model_evaluation.evaluate import event_results
from model_evaluation.tune_windows import write_json, selected_references
from model_evaluation.windowing import PriorHistory
from model_improvement.data import BENCHMARK, SOURCE_CACHE, SEARCH, validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import frozen as frozen_v4, load as load_v4, with_legacy, OUTPUT as V4
from model_improvement.run import trusted_load
from model_research_v5.features import context_features, CONTEXT, ALL_FEATURES
from model_research_v5.models import Settings, Suite, METHODS
from model_research_v5.channels import ChannelFamily
from model_research_v5.run import load as load_v5, components, summarize, six_metrics, candidates, frame_from_arrays, METRIC_NAMES

OUTPUT = ROOT/"reports/model_research_v6"
SEED = 8_170_042
CODE_FILES = ("features.py", "models.py", "channels.py", "run_channels.py")


def fingerprints():
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha256(p) for p in (BENCHMARK/"manifest.json", SOURCE_CACHE, V4/"selection.json")}


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists(): return load()
    _, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    prior, old = load_v5(), load_v4()
    _, _, state, _ = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    native = validation_inputs(bundle, state, count=240, seed=SEED)
    bank, legacy = {}, {}
    forbidden = set().union(*(set(d["observed"].case_id) for d in prior["bank"].values()))
    forbidden |= set().union(*(set(d["observed"].case_id) for d in old["bank"].values()))
    for name, d in native.items():
        bank[name], legacy[name] = {}, {}
        for cohort in ("observed", "normal"):
            frame = with_legacy(build_features(d[cohort], d[cohort+"_rows"], history, old["baseline"]), old["legacy_baselines"], history)
            if set(frame.case_id) & forbidden: raise ValueError("v6 cases overlap previous development")
            legacy[name][cohort], bank[name][cohort] = frame, context_features(frame)
    payload = {"partitions": prior["partitions"], "source_support": prior["source_support"], "bank": bank, "legacy_bank": legacy}
    joblib.dump(payload, OUTPUT/"development_inputs.joblib", compress=3)
    write_json(OUTPUT/"preparation.json", {"inputs": fingerprints(), "data_sha256": sha256(OUTPUT/"development_inputs.joblib"),
        "code_sha256": {n: sha256(Path(__file__).parent/n) for n in CODE_FILES},
        "search_cases": 720, "confirmation_cases": 720, "validation_seed": SEED,
        "normal_augmented_windows": {n: len(p) for n, p in prior["partitions"].items()},
        "augmentation_not_independent_source_growth": True,
        "previous_v5_inputs_sha256": sha256(ROOT/"reports/model_research_v5/development_inputs.joblib")})
    return payload


def load():
    record = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if fingerprints() != record["inputs"]: raise ValueError("Different v6 inputs")
    for n, digest in record["code_sha256"].items():
        if sha256(Path(__file__).parent/n) != digest: raise ValueError(f"Stale v6 code: {n}")
    return trusted_load(OUTPUT/"development_inputs.joblib", record["data_sha256"])


def tune():
    data = load()
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v6 selection already frozen")
    selected, records = {}, []
    for algorithm in ("if", "kmeans"):
        best = model = fallback = fallback_model = None
        for settings in candidates(algorithm):
            print(f"v6 channel fit {asdict(settings)}", flush=True)
            family = ChannelFamily(CONTEXT, settings).fit(data["partitions"], data["source_support"])
            cached = components(family, data["bank"], "search")
            for q in (.975, .99, .995, .999):
                for weight in (.25, .5, .75):
                    family.set_operating_point(q, weight, True)
                    result = summarize(family, cached)
                    r = {"algorithm": algorithm, "settings": asdict(settings), "quantile": q, "global_weight": weight,
                         "phase_aware": True, "within_budget": result["worst_scenario_strategy_fpr"] <= .05, **result}
                    records.append(r)
                    key = (r["objective"], -r["worst_strategy_pooled_fpr"], -settings.rotations, -settings.k)
                    low_fpr = (-r["worst_scenario_strategy_fpr"], r["objective"])
                    if fallback is None or low_fpr > fallback[0]: fallback, fallback_model = (low_fpr, r), copy.deepcopy(family)
                    if r["within_budget"] and (best is None or key > best[0]): best, model = (key, r), copy.deepcopy(family)
        if best is None: best, model = fallback, fallback_model
        selected[algorithm] = model
        write_json(OUTPUT/f"chosen_{algorithm}.json", best[1])
        print(f"v6 chosen {algorithm}: macro={best[1]['objective']:.2%}, worst FPR={best[1]['worst_scenario_strategy_fpr']:.2%}, feasible={best[1]['within_budget']}", flush=True)
    suite = Suite(selected)
    old, _ = frozen_v4()
    default = Suite({a: ChannelFamily(CONTEXT, Settings(a)).fit(data["partitions"], data["source_support"]) for a in ("if", "kmeans")})
    validation = {name: {split: six_metrics(model, bank, split) for split in ("search", "confirmation")}
                  for name, model, bank in (("baseline_v4", old, data["legacy_bank"]), ("channels_default", default, data["bank"]), ("tuned_v6", suite, data["bank"]))}
    joblib.dump(suite, OUTPUT/"models_v6.joblib", compress=3)
    write_json(OUTPUT/"search_results.json", records)
    write_json(OUTPUT/"validation_results.json", validation)
    normal = np.vstack([suite.score(d["normal"].loc[d["normal"].validation_split.eq("confirmation")])[0] for d in data["bank"].values()])
    write_json(OUTPUT/"matched_fpr_reference.json", {"target": .01, "normal_windows": len(normal),
        "margin_cutoffs": np.quantile(normal, .99, axis=0).tolist(), "note": "Diagnostic only; confirmation normal controls, no final labels."})
    write_json(OUTPUT/"selection.json", {"inputs": fingerprints(), "model_sha256": sha256(OUTPUT/"models_v6.joblib"),
        "training_code_sha256": {n: sha256(Path(__file__).parent/n) for n in CODE_FILES},
        "families": {a: json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in selected},
        "objective": "Equal-scenario F1 averaged across three strategies; EACH scenario/strategy normal FPR<=5%, else least-worst FPR flagged.",
        "confirmation_used_for_selection": False, "final_used_for_selection": False, "hard_used_for_selection": False,
        "fresh_validation_seed": SEED, "candidate_fits_per_algorithm": 4, "operating_points_per_fit": 12,
        "normal_only_fit": True, "labelled_selection": True,
        "latent_latency_assessment": "At least two prior completed-minute observations in preceding five minutes, current completion present. No assumed clean warm-up.",
        "completed_utc": datetime.now(timezone.utc).isoformat()})
    print("v6 frozen before final evaluation", flush=True)


def frozen():
    r = json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    if r["inputs"] != fingerprints(): raise ValueError("Changed frozen v6 inputs")
    for n, digest in r["training_code_sha256"].items():
        if sha256(Path(__file__).parent/n) != digest: raise ValueError(f"Changed frozen v6 code: {n}")
    return trusted_load(OUTPUT/"models_v6.joblib", r["model_sha256"]), r


def evaluate():
    suite, selection = frozen()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    prior = json.loads((V4/"summary.json").read_text(encoding="utf-8"))
    results, pooled, supported = [], Counts(), Counts()
    for previous in prior["datasets"]:
        name = previous["dataset"]
        dest = OUTPUT/name
        dest.mkdir(exist_ok=True)
        if (dest/"metrics.json").exists():
            result = json.loads((dest/"metrics.json").read_text(encoding="utf-8"))
            if result["model_sha256"] != selection["model_sha256"]: raise ValueError("Different previous v6 model")
            for c, digest in result["arrays_sha256"].items():
                if sha256(dest/f"{c}.npz") != digest: raise ValueError("Changed v6 evaluation cache")
        else:
            arrays, counts, support = {}, Counts(), Counts()
            for cohort, digest in previous["arrays_sha256"].items():
                if sha256(V4/name/f"{cohort}.npz") != digest: raise ValueError("Changed v4 raw-derived inputs")
                with np.load(V4/name/f"{cohort}.npz", allow_pickle=False) as file:
                    a = {k: file[k] for k in file.files if k not in ("margin", "prediction", "covered")}
                frame = frame_from_arrays(a)
                margins, preds, covers, evidence = [], [], [], []
                for start in range(0, len(frame), 50_000):
                    part = frame.iloc[start:start+50_000]
                    m, p, c = suite.score(part)
                    margins.append(m.astype(np.float32)); preds.append(p); covers.append(c)
                    # Global-channel evidence, separate from ensemble final scores.
                    ev = []
                    for algorithm in ("if", "kmeans"):
                        detector = suite.families[algorithm].global_model
                        ev.append(detector.explain(part)[0])
                    evidence.append(np.column_stack(ev).astype(np.float32))
                a["margin"], a["prediction"], a["covered"] = np.vstack(margins), np.vstack(preds), np.concatenate(covers)
                a["features"] = frame[list(ALL_FEATURES)].to_numpy(dtype=np.float32)
                a["channel_evidence"] = np.vstack(evidence)
                a["latency_assessed"] = suite.families["if"].global_model.raw_model.eligible(frame)
                a["phase"] = frame.phase.to_numpy(dtype=str)
                counts.update(a["truth"], a["prediction"])
                support.update(a["truth"], a["prediction"], a["covered"])
                np.savez_compressed(dest/f"{cohort}.npz", **a)
                arrays[cohort] = a
            cases = pd.read_csv(BENCHMARK/name/"cases.csv", encoding="utf-8-sig")
            events = pd.read_csv(BENCHMARK/name/"events.csv", encoding="utf-8-sig")
            result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(), "supported_only_metrics": support.records(),
                "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                "latency_assessed_fraction": float(np.concatenate([a["latency_assessed"] for a in arrays.values()]).mean()),
                "model_sha256": selection["model_sha256"], "arrays_sha256": {c: sha256(dest/f"{c}.npz") for c in arrays}}
            write_json(dest/"metrics.json", result)
        pooled.values += Counts.from_records(result["metrics"]).values
        supported.values += Counts.from_records(result["supported_only_metrics"]).values
        results.append(result)
        print(f"v6 {name}: IF ensemble={result['metrics'][2]['f1']:.2%}, KMeans ensemble={result['metrics'][5]['f1']:.2%}", flush=True)
    write_json(OUTPUT/"summary.json", {"pooled": pooled.records(), "supported_only": supported.records(), "datasets": results,
        "basic_macro": [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in results if d["dataset"] != "mixed_hard"])) for k in METRIC_NAMES}}
                        for i, m in enumerate(METHODS)],
        "thresholds": suite.threshold_records(), "support": suite.families["if"].support, "model_sha256": selection["model_sha256"],
        "protocol": {"input": "Process-only causal minute grid; synthetic prior traffic history", "point_adjustment": False,
            "data_and_labels_unchanged": True, "normal_only_fit": True,
            "limitations": ["Original final benchmark was previously inspected; not blind external validation.",
                            "Adaptive channel design after v5; new confirmation cases share sources/generator.",
                            "Empirical channel ranks are not guaranteed probabilities under dependent/shifted data.",
                            "Latency cannot be assessed without prior context; these rows stay in metric denominators.",
                            "Injected anomalies may contaminate subsequent causal context.",
                            "Normal reset augmentation repeats data; no increase in independent source support.",
                            "Not a full 3.7M-row fit, not an automatic production-model replacement."]}})
    verify_benchmark(BENCHMARK, SOURCE_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "tune", "evaluate", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("prepare", "all"): prepare()
    if args.stage in ("tune", "all"): tune()
    if args.stage in ("evaluate", "all"): evaluate()
