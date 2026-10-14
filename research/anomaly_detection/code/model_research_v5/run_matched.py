"""v7: match normal replay regimes, without changing benchmark cases/labels.

Normal references are regenerated ONLY from non-reserved source templates in
their original train/calibration/threshold partitions. Sparse single-execution
streams and central-duration streams address a known generator-domain shift.
This is synthetic-domain adaptation, NOT evidence of real production gains.
"""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from evaluation_benchmark.generate import Case
from model_evaluation.data import ReplayCatalog, source_references, history_lookup, sha256, verify_benchmark
from model_evaluation.windowing import window_reference_split, PriorHistory
from model_evaluation.tune_windows import selected_references, write_json
from model_evaluation.metrics import Counts
from model_evaluation.evaluate import event_results
from model_improvement.data import BENCHMARK, SOURCE_CACHE, SEARCH, validation_inputs, rich_rows
from model_improvement.features import build_features
from model_improvement.refine import VIEWS as OLD_VIEWS, ALL_FEATURES as OLD_FEATURES, with_legacy, load as load_v4, frozen as frozen_v4, OUTPUT as V4
from model_improvement.run import trusted_load
from model_research_v5.features import context_features, CONTEXT, ANCHOR
from model_research_v5.models import Family, Settings, Suite, METHODS
from model_research_v5.run import load as load_v5, components, summarize, six_metrics, METRIC_NAMES
from model_research_v5.run_channels import load as load_v6

OUTPUT = ROOT/"reports/model_research_v7"
SEED = 9_170_042
CODE_FILES = ("features.py", "models.py", "run_matched.py")
VIEWS = {"relational": OLD_VIEWS["relational"], "temporal": OLD_VIEWS["temporal"],
         "context": ANCHOR, "combined": tuple(dict.fromkeys((*OLD_VIEWS["relational"], *CONTEXT)))}
ALL_FEATURES = tuple(dict.fromkeys((*OLD_FEATURES, *CONTEXT)))


class CentralNormalCatalog(ReplayCatalog):
    """Only normal selection; conditional centrality uses this split's sources.

    The original evaluation generator samples P05..P75 for supported groups.
    Reproduce that NORMAL reference regime, but never its injected values.
    No held-out template/profile is inspected when building this catalog.
    """
    def __init__(self, templates):
        super().__init__(templates)
        adapter, coarse = {}, {}
        for key, refs in self.groups.items():
            values = [self.templates[t]["rows"][r]["duration_ms"] for t, r in refs]
            adapter.setdefault(key[0], []).extend(values)
            coarse.setdefault((key[0], key[2]), []).extend(values)
        self.central = {}
        for key, refs in self.groups.items():
            values = [self.templates[t]["rows"][r]["duration_ms"] for t, r in refs]
            reference = values if len(values) >= 30 else coarse[(key[0], key[2])]
            if len(reference) < 30: reference = adapter[key[0]]
            low, high = np.quantile(reference, [.05, .75])
            self.central[key] = [(t, r) for t, r in refs if low <= self.templates[t]["rows"][r]["duration_ms"] <= high] or refs

    def pick(self, key, rng):
        refs = self.central[key]
        return refs[int(rng.integers(len(refs)))]


def fingerprints():
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha256(p) for p in (BENCHMARK/"manifest.json", SOURCE_CACHE, V4/"selection.json")}


def normal_references(bundle, state, old, central=True):
    original, _, _, _ = source_references(bundle)
    _, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    history_values, history = history_lookup(BENCHMARK), PriorHistory(BENCHMARK/"reference_history_windows.csv")
    partitions, metadata = {}, []
    for si, name in enumerate(("train", "calibration", "threshold")):
        templates = [t for t in bundle["templates"] if t["template_id"] in sets[name]]
        catalog = CentralNormalCatalog(templates) if central else ReplayCatalog(templates)
        windows, raw = [], []
        for index in range(len(catalog.adapter_names)*24):
            # Four regimes, six repetitions each per Adapter. NOT model inputs.
            regime = (index//len(catalog.adapter_names)) % 4
            case = Case(catalog, "individual_delay", index, SEED+100_000+si*10_000, history_values)
            if regime == 0:
                case.clone(case.key, 2); case.background((0, 4))
            elif regime == 1:
                case.clone(case.key, 2.2)
                other = [k for k in catalog.groups if k[0] == case.key[0] and k[2] != case.key[2]]
                case.clone(other[int(case.rng.integers(len(other)))] if other else case.key, 2.6)
                case.background((0, 4))
            else:
                case.baseline(36 if regime == 2 else 60)
            if case.events or case.labels: raise ValueError("Normal reference contains injection")
            if any(r["status"] != "S" for flow in case.observed.values() for r in flow): raise ValueError("Non-success reference")
            export = case.export()
            windows.extend(r for r in export["windows.csv"] if r["scope"] == "TARGET")
            raw.append(rich_rows(case, case.observed))
        frame = with_legacy(build_features(pd.DataFrame(windows), pd.concat(raw, ignore_index=True), history, old["baseline"]), old["legacy_baselines"], history)
        partitions[name] = context_features(frame)
        metadata.append({"partition": name, "episodes": int(frame.case_id.nunique()), "windows": len(frame),
                         "source_templates": len(sets[name]), "central_sampling": central})
        print(f"v7 normal {name}: {len(frame):,} windows, sources={len(sets[name]):,}", flush=True)
    return partitions, support, metadata


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists(): return load()
    _, bundle = verify_benchmark(BENCHMARK, SOURCE_CACHE)
    old = load_v4()
    _, _, state, _ = selected_references(SEARCH, BENCHMARK, SOURCE_CACHE)
    partitions, support, metadata = normal_references(bundle, state, old)
    native = validation_inputs(bundle, state, count=240, seed=SEED)
    history = PriorHistory(BENCHMARK/"reference_history_windows.csv")
    previous = [old, load_v5(), load_v6()]
    forbidden = set().union(*(set(d["observed"].case_id) for p in previous for d in p["bank"].values()))
    bank, legacy = {}, {}
    for name, d in native.items():
        bank[name], legacy[name] = {}, {}
        for cohort in ("observed", "normal"):
            frame = with_legacy(build_features(d[cohort], d[cohort+"_rows"], history, old["baseline"]), old["legacy_baselines"], history)
            if set(frame.case_id) & forbidden: raise ValueError("Validation cases overlap previous versions")
            legacy[name][cohort], bank[name][cohort] = frame, context_features(frame)
    reference_ids = set().union(*(set(p.case_id) for p in partitions.values()))
    if any(reference_ids & set(d["observed"].case_id) for d in bank.values()): raise ValueError("Reference/validation episode overlap")
    joblib.dump({"partitions": partitions, "source_support": support, "bank": bank, "legacy_bank": legacy,
                 "baseline": old["baseline"], "legacy_baselines": old["legacy_baselines"]}, OUTPUT/"development_inputs.joblib", compress=3)
    write_json(OUTPUT/"preparation.json", {"inputs": fingerprints(), "data_sha256": sha256(OUTPUT/"development_inputs.joblib"),
        "code_sha256": {n: sha256(Path(__file__).parent/n) for n in CODE_FILES}, "normal_references": metadata,
        "search_cases": 720, "confirmation_cases": 720, "validation_seed": SEED,
        "reference_source_pool_disjoint_from_reserved_validation_test_templates": True,
        "normal_source_support_not_inflated_by_replay": True,
        "change": "Central-duration and sparse/dense normal replay matching; generator-domain adaptation only."})
    return load()


def load():
    record = json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"))
    if fingerprints() != record["inputs"]: raise ValueError("Changed v7 inputs")
    for n, digest in record["code_sha256"].items():
        if sha256(Path(__file__).parent/n) != digest: raise ValueError(f"Changed frozen v7 code: {n}")
    return trusted_load(OUTPUT/"development_inputs.joblib", record["data_sha256"])


def tune():
    data = load()
    if (OUTPUT/"selection.json").exists(): raise FileExistsError("v7 already frozen")
    selected, results = {}, []
    for algorithm in ("if", "kmeans"):
        best = model = fallback = fallback_model = None
        settings_list = ([Settings("if", samples=s) for s in (256, 1024)] if algorithm == "if" else
                         [Settings("kmeans", k=k, shape_distance=d) for k, d in ((6, False), (12, False), (6, True))])
        for view, columns in VIEWS.items():
            for settings in settings_list:
                print(f"v7 fit {algorithm} {view} {asdict(settings)}", flush=True)
                family = Family(columns, settings).fit(data["partitions"], data["source_support"])
                cached = components(family, data["bank"], "search")
                for aware in (False, True):
                    for q in (.975, .99, .995, .999):
                        for weight in (.25, .5, .75):
                            family.set_operating_point(q, weight, aware)
                            r = {"algorithm": algorithm, "view": view, "settings": asdict(settings), "quantile": q,
                                 "global_weight": weight, "phase_aware": aware, **summarize(family, cached)}
                            r["within_budget"] = r["worst_scenario_strategy_fpr"] <= .05
                            results.append(r)
                            key = (r["objective"], -r["worst_strategy_pooled_fpr"], -len(columns), -settings.samples, -settings.k)
                            low_fpr = (-r["worst_scenario_strategy_fpr"], r["objective"])
                            if fallback is None or low_fpr > fallback[0]: fallback, fallback_model = (low_fpr, r), copy.deepcopy(family)
                            if r["within_budget"] and (best is None or key > best[0]): best, model = (key, r), copy.deepcopy(family)
        if best is None: best, model = fallback, fallback_model
        selected[algorithm] = model
        write_json(OUTPUT/f"chosen_{algorithm}.json", best[1])
        print(f"v7 chosen {algorithm}: macro={best[1]['objective']:.2%}, worst FPR={best[1]['worst_scenario_strategy_fpr']:.2%}, feasible={best[1]['within_budget']}", flush=True)
    suite = Suite(selected)
    suite.baseline, suite.legacy_baselines = data["baseline"], data["legacy_baselines"]
    old, _ = frozen_v4()
    default = Suite({a: Family(OLD_VIEWS["relational"], Settings(a)).fit(data["partitions"], data["source_support"]) for a in ("if", "kmeans")})
    for f in default.families.values(): f.set_operating_point(.99, .5, False)
    validation = {name: {split: six_metrics(m, bank, split) for split in ("search", "confirmation")}
                  for name, m, bank in (("baseline_v4", old, data["legacy_bank"]), ("matched_normal_defaults", default, data["bank"]), ("tuned_v7", suite, data["bank"]))}
    joblib.dump(suite, OUTPUT/"models_v7.joblib", compress=3)
    write_json(OUTPUT/"validation_results.json", validation)
    write_json(OUTPUT/"search_results.json", results)
    write_json(OUTPUT/"selection.json", {"inputs": fingerprints(), "model_sha256": sha256(OUTPUT/"models_v7.joblib"),
        "training_code_sha256": {n: sha256(Path(__file__).parent/n) for n in CODE_FILES},
        "families": {a: json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in selected},
        "confirmation_used_for_selection": False, "final_used_for_selection": False, "hard_used_for_selection": False,
        "normal_only_model_fit": True, "labelled_validation_selection": True,
        "objective": "Equal-scenario F1 across three strategies, EACH scenario/strategy normal FPR<=5%; otherwise flagged least-worst FPR.",
        "limitation": "Generator-aware normal replay adaptation, previously inspected original test. NOT blind or production evidence."})


def frozen():
    r = json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"))
    if r["inputs"] != fingerprints(): raise ValueError("Changed frozen v7 inputs")
    for n, digest in r["training_code_sha256"].items():
        if sha256(Path(__file__).parent/n) != digest: raise ValueError(f"Changed frozen v7 code: {n}")
    return trusted_load(OUTPUT/"models_v7.joblib", r["model_sha256"]), r


def evaluate():
    suite, selection = frozen()
    verify_benchmark(BENCHMARK, SOURCE_CACHE)
    prior = json.loads((V4/"summary.json").read_text(encoding="utf-8"))
    datasets, pooled, support = [], Counts(), Counts()
    for previous in prior["datasets"]:
        name = previous["dataset"]; dest = OUTPUT/name; dest.mkdir(exist_ok=True)
        if (dest/"metrics.json").exists():
            result = json.loads((dest/"metrics.json").read_text(encoding="utf-8"))
            if result["model_sha256"] != selection["model_sha256"]: raise ValueError("Stale v7 model")
            for c, digest in result["arrays_sha256"].items():
                if sha256(dest/f"{c}.npz") != digest: raise ValueError("Changed v7 cache")
        else:
            arrays, counts, supported = {}, Counts(), Counts()
            for cohort, digest in previous["arrays_sha256"].items():
                if sha256(V4/name/f"{cohort}.npz") != digest: raise ValueError("Changed raw-derived v4 cache")
                with np.load(V4/name/f"{cohort}.npz", allow_pickle=False) as file:
                    a = {k: file[k] for k in file.files if k not in ("margin", "prediction", "covered")}
                frame = pd.DataFrame(a["features"], columns=OLD_FEATURES, dtype=float)
                frame["case_id"], frame["adapterType"], frame["processId"] = a["case_index"], a["adapter"], a["process"]
                frame["window_start"] = pd.to_datetime(a["window_end_seconds"]-60, unit="s")
                frame = context_features(frame)
                margins, preds, covered = [], [], []
                for start in range(0, len(frame), 50_000):
                    m, p, c = suite.score(frame.iloc[start:start+50_000])
                    margins.append(m.astype(np.float32)); preds.append(p); covered.append(c)
                a["margin"], a["prediction"], a["covered"] = np.vstack(margins), np.vstack(preds), np.concatenate(covered)
                a["features"], a["phase"] = frame[list(ALL_FEATURES)].to_numpy(dtype=np.float32), frame.phase.to_numpy(dtype=str)
                counts.update(a["truth"], a["prediction"]); supported.update(a["truth"], a["prediction"], a["covered"])
                np.savez_compressed(dest/f"{cohort}.npz", **a); arrays[cohort] = a
            cases = pd.read_csv(BENCHMARK/name/"cases.csv", encoding="utf-8-sig")
            events = pd.read_csv(BENCHMARK/name/"events.csv", encoding="utf-8-sig")
            result = {"dataset": name, "case_count": len(cases), "metrics": counts.records(), "supported_only_metrics": supported.records(),
                      "events": event_results(arrays["observed"], arrays["normal"], events, cases),
                      "coverage": float(np.concatenate([a["covered"] for a in arrays.values()]).mean()),
                      "model_sha256": selection["model_sha256"], "arrays_sha256": {c: sha256(dest/f"{c}.npz") for c in arrays}}
            write_json(dest/"metrics.json", result)
        pooled.values += Counts.from_records(result["metrics"]).values; support.values += Counts.from_records(result["supported_only_metrics"]).values
        datasets.append(result)
        print(f"v7 {name}: IF ensemble={result['metrics'][2]['f1']:.2%}, KMeans ensemble={result['metrics'][5]['f1']:.2%}", flush=True)
    write_json(OUTPUT/"summary.json", {"pooled": pooled.records(), "supported_only": support.records(), "datasets": datasets,
        "basic_macro": [{"method": m, **{k: float(np.mean([d["metrics"][i][k] for d in datasets if d["dataset"] != "mixed_hard"])) for k in METRIC_NAMES}}
                        for i, m in enumerate(METHODS)], "thresholds": suite.threshold_records(), "support": suite.families["if"].support,
        "model_sha256": selection["model_sha256"], "protocol": {"data_and_labels_unchanged": True, "point_adjustment": False,
        "normal_fit_source_disjoint_from_reserved": True, "normal_reference_change": "Sparse/dense and central-duration synthetic replay, not more independent observations.",
        "not_blind_original_test": True, "not_automatic_production_replacement": True}})
    verify_benchmark(BENCHMARK, SOURCE_CACHE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "tune", "evaluate", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("prepare", "all"): prepare()
    if args.stage in ("tune", "all"): tune()
    if args.stage in ("evaluate", "all"): evaluate()
