"""Auditable local experiment; original fitting, unchanged paired benchmarks.

No writes to data/, previous reports/models, Git remotes or Notion.
Run: python -m process_event_model.run --stage prepare|develop|evaluate
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, TASKS, original, sample_ids, write_json, benchmark, digest
from .features import EventFeatures, NormalReference, FEATURES
from .models import Detector, Settings, metrics

OUT = ROOT/"reports/process_event_model"


def prepare():
    cache = OUT/"original_features.joblib"
    if cache.exists():
        return joblib.load(cache)
    raw = original(OUT)
    normal = raw.loc[raw.normal_fit_eligible & raw.split.eq("train")]
    ids = sample_ids(normal, 40000)
    ref = NormalReference.fit(raw.loc[ids])
    # Use ORIGINAL continuous source days. Limit development compute to selected
    # actual days, not synthetic episodes; all events in each day are retained.
    # State starts at the declared day boundary and has explicit cold-start support.
    dates = raw.availableTime.dt.tz_convert("Asia/Seoul").dt.normalize()
    records, selected = [], {}
    for split, cap in (("train", 60000), ("calibration", 15000), ("threshold", 15000), ("test", 15000)):
        part = raw.loc[raw.split.eq(split) & raw.normal_fit_eligible]
        # Actual observed-date selection, deterministic and adapter-covering.
        candidate_days = sorted(dates.loc[part.index].unique())
        if not candidate_days:
            raise ValueError(f"No normal original rows for {split}")
        chosen = set(candidate_days[::max(1, len(candidate_days)//6)])
        for adapter, group in part.groupby("adapterType", observed=True):
            chosen.add(dates.loc[group.index[0]])
        mask = dates.isin(chosen)
        context = raw.loc[mask].copy()
        context["case_id"] = context.source_period.astype(str)+":"+dates.loc[context.index].astype(str)
        # Work in whole actual streams. Dense streams are not thinned before
        # deriving context; only estimator samples are thinned afterwards.
        print(f"Original {split}: computing context from {len(context):,} rows on {len(chosen)} real dates", flush=True)
        features = EventFeatures(ref).transform(context)
        eligible = features.loc[features.normal_fit_eligible & features.split.eq(split)]
        selected_ids = sample_ids(eligible, cap)
        result = eligible.loc[selected_ids].copy()
        selected[split] = result
        records.append(dict(split=split, source_context_rows=len(context), selected_rows=len(result),
                            actual_dates=[str(d) for d in sorted(chosen)]))
    package = dict(reference=ref, partitions=selected)
    joblib.dump(package, cache, compress=3)
    write_json(OUT/"feature_provenance.json", dict(synthetic_fit_rows=0, raw_rows_preserved=True,
        features=list(FEATURES), actual_source_sessions=records,
        original_times_preserved=True, context_resets="declared actual observed day sessions for fitting",
        cold_start_marked=True, no_gap_filling=True, benchmark_source_fit_overlap=0))
    return package


def features_for_benchmark(ref, task, limit=None, offset=0, processor_kind=None):
    bank, cases = benchmark(task, limit, offset)
    out = {}
    for name, rows in bank.items():
        clean = rows.drop(columns=[c for c in ("label", "row_is_anomaly", "collective_context_is_anomaly") if c in rows])
        from .row_context import ExactReference, RowContext
        if processor_kind=='predictive_row_context':
            from .predictive import PredictiveRowContext
            processor=PredictiveRowContext(ref)
        elif processor_kind in (None,'row_context_v2'):
            processor = RowContext(ref) if isinstance(ref, ExactReference) else EventFeatures(ref)
        else:
            raise ValueError(f'Unsupported benchmark processor: {processor_kind}')
        out[name] = processor.transform(clean)
        out[name]["label"] = bank[name].label.to_numpy()
        # Membership selects evaluation rows only; never estimator inputs.
        targets = cases.set_index("case_id")
        out[name]["target"] = (out[name].adapterType.eq(out[name].case_id.map(targets.target_adapter)) &
                                out[name].processId.eq(out[name].case_id.map(targets.target_process_id)))
    return out, cases


def development(ref, limit=400):
    """Existing benchmark case holdout, never used for original model fitting.

    First case block is development; final report uses remaining cases. Earlier
    project versions already inspected this benchmark, so not externally blind.
    """
    cache = OUT/f"development_{limit}.joblib"
    if cache.exists():
        return joblib.load(cache)
    bank = {}
    for task in TASKS:
        start = time.perf_counter()
        bank[task] = features_for_benchmark(ref, task, limit)
        print(f"Development {task}: features in {time.perf_counter()-start:.1f}s", flush=True)
    joblib.dump(bank, cache, compress=3)
    return bank


def evaluated_rows(pair):
    frames = []
    for cohort in ("observed", "normal"):
        f = pair[cohort]
        # Every original Process row is evaluated, including background roles.
        frames.append(f)
    f = pd.concat(frames, ignore_index=True)
    return f, f.label.to_numpy(bool)


def develop(limit=400):
    package = prepare()
    normal = package["partitions"]["train"]
    calibration = package["partitions"]["calibration"]
    bank = development(package["reference"], limit)
    frames = {task: evaluated_rows(pair)[0] for task, (pair, _) in bank.items() if task != "input_stop"}
    choices, results = {}, []
    for algorithm in ("if", "kmeans", "lof"):
        settings = []
        for view in ("point", "context", "full"):
            for size in ((64, 256, 1024) if algorithm == "if" else (8, 24, 64) if algorithm == "kmeans" else (20, 60, 150)):
                values = dict(samples=size) if algorithm == "if" else dict(clusters=size) if algorithm == "kmeans" else dict(neighbors=size)
                settings.append(Settings(algorithm, view, **values, whiten=algorithm == "kmeans"))
        best = None
        for setting in settings:
            detector = Detector(setting).fit(normal)
            scores = {t: detector.raw_score(f) for t, f in frames.items()}
            original_cal_scores = detector.raw_score(calibration)
            # Labelled development is permitted for threshold/hyperparameters;
            # raw source and normal reference/estimator were fit without labels.
            normal_scores = np.concatenate([s[~frames[t].label.astype(bool).to_numpy()] for t, s in scores.items()])
            for q in (.94, .96, .98, .99, .995, .999):
                threshold = float(max(np.quantile(normal_scores, q), np.quantile(original_cal_scores, .94)))
                per_task = {t: metrics(frames[t].label, s > threshold) for t, s in scores.items()}
                max_fpr = max(m["false_positive_rate"] for m in per_task.values())
                value = float(np.mean([m["f1"] for m in per_task.values()]))
                row = dict(settings=asdict(setting), quantile=q, threshold=threshold,
                           macro_f1=value, maximum_fpr=max_fpr, tasks=per_task,
                           original_normal_calibration_alert_rate=float(np.mean(original_cal_scores > threshold)),
                           calibration_policy="max(benchmark development normal quantile, original normal calibration P94)")
                results.append(row)
                objective = value if max_fpr <= .06 else -max_fpr
                if best is None or objective > best[0]:
                    detector.threshold = threshold
                    # A fitted candidate may be reused across quantiles; persist
                    # the chosen scalar separately instead of aliasing thresholds.
                    import copy
                    best = (objective, copy.deepcopy(detector), row)
            print(f"{algorithm} {setting.view}: best macro F1 {best[2]['macro_f1']:.3f}, FPR {best[2]['maximum_fpr']:.3f}", flush=True)
        choices[algorithm] = best[1]
        write_json(OUT/f"{algorithm}_development.json", best[2])
    joblib.dump(dict(reference=package["reference"], models=choices), OUT/"event_models.joblib", compress=3)
    write_json(OUT/"development_search.json", dict(candidates=results, development_cases_per_task=limit,
        estimator_fit_original_only=True, prior_benchmark_adaptively_inspected=True,
        input_stop_excluded_from_row_classification=True))
    return choices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("prepare", "develop", "evaluate"), default="develop")
    parser.add_argument("--development-cases", type=int, default=400)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True, parents=True)
    if args.stage == "prepare":
        prepare()
    elif args.stage == "develop":
        develop(args.development_cases)
    else:
        from .evaluate import evaluate
        evaluate(args.development_cases)


if __name__ == "__main__":
    main()
