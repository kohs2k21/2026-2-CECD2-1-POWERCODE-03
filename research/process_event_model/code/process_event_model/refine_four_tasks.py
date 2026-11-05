"""Bounded development-only recalibration of the original-row candidate.

The sklearn estimators and normal reference stay fixed. Only warm-context
fusion weights and evidence-state cutoffs are searched. No task identity is
available at prediction time. Input-stop is explicitly out of scope.
"""
import argparse
import copy
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT, digest, write_json
from .models import metrics
from .scope import ACTIVE_TASKS, EXCLUDED_TASKS


def confusion_counts(y, p):
    return np.array([np.sum(y & p), np.sum(~y & p)], dtype=float)


def search_cutoffs(groups, scores, task_index, y,
                   baseline, initial, normal_groups, normal_scores):
    """Search using cached group contributions rather than rescoring rows."""
    positives = np.array([np.sum(y[task_index == t]) for t in range(4)], float)
    negatives = np.array([np.sum(~y[task_index == t]) for t in range(4)], float)
    normal_count = len(normal_scores)
    options, counts, normal_counts = [], [], []
    for g in range(8):
        if g >= 4:
            opts = np.array([initial[g]])  # exact cold-context preservation
        else:
            v = scores[(groups == g) & ~y]
            orig = normal_scores[normal_groups == g]
            opts = np.unique(np.r_[initial[g],
                np.quantile(v, np.linspace(.7, .9998, 65)) if len(v) else [],
                np.quantile(orig, [.85, .9, .94, .96, .98, .99, .995, .999]) if len(orig) else []])
            opts = np.maximum(opts, 1e-6)
        options.append(opts)
        table = np.zeros((len(opts), 4, 2))
        for t in range(4):
            mask = (groups == g) & (task_index == t)
            for j, cutoff in enumerate(opts):
                table[j, t] = confusion_counts(y[mask], scores[mask] > cutoff)
        counts.append(table)
        normal_counts.append(np.array([np.sum(normal_scores[normal_groups == g] > c) for c in opts]))

    floors = np.array([baseline[t]['f1'] for t in ACTIVE_TASKS])

    def objective(indices):
        total = sum(counts[g][indices[g]] for g in range(8))
        tp, fp = total[:, 0], total[:, 1]
        f1 = 2*tp / np.maximum(positives+tp+fp, 1)
        fpr = fp / np.maximum(negatives, 1)
        nr = sum(normal_counts[g][indices[g]] for g in range(8))/normal_count
        violation = np.maximum(floors-f1, 0).sum() + np.maximum(fpr-.06, 0).sum() + max(nr-.06, 0)
        return float(f1.mean()-100*violation), violation <= 1e-12

    indices = [int(np.argmin(abs(options[g]-initial[g]))) for g in range(8)]
    best, safe = objective(indices)
    for _ in range(8):
        changed = False
        for g in range(4):
            chosen = indices[g]
            for j in range(len(options[g])):
                trial = list(indices); trial[g] = j
                value, valid = objective(trial)
                if value > best + 1e-12:
                    best, safe, chosen = value, valid, j
            if chosen != indices[g]:
                indices[g] = chosen; changed = True
        if not changed:
            break
    return np.array([options[g][indices[g]] for g in range(8)]), best, safe


def run(output):
    source = ROOT/'reports/process_event_model/predictive_counts/frozen_candidate'
    package = joblib.load(source/'event_models.joblib')
    old_sha = digest(source/'event_models.joblib')
    bank = joblib.load(ROOT/'reports/process_event_model/predictive_counts/development.joblib')
    f = pd.concat([bank[t].loc[bank[t].target].assign(_task=t) for t in ACTIVE_TASKS], ignore_index=True)
    y = f.label.to_numpy(bool)
    task_index = np.array([ACTIVE_TASKS.index(t) for t in f._task])
    originals = joblib.load(ROOT/'reports/process_event_model/predictive_counts/original_features.joblib')
    cal = originals['partitions']['calibration']
    selected = {}; results = {}; trials = []
    output.mkdir(parents=True, exist_ok=True)
    for a, old in package['models'].items():
        raw, raw_cal = old.base.raw_score(f), old.base.raw_score(cal)
        baseline = {t:metrics(y[task_index==i], old.predict(f.loc[task_index==i])[1])
                    for i,t in enumerate(ACTIVE_TASKS)}
        incumbent = float(np.mean([m['f1'] for m in baseline.values()]))
        winner = copy.deepcopy(old)
        for weight in (.15, .25, .35, .5):
            candidate = copy.deepcopy(old)
            candidate.model_weight = weight
            groups, scores = candidate.components(f, raw)
            cg, cs = candidate.components(cal, raw_cal)
            cuts, value, safe = search_cutoffs(groups, scores, task_index, y,
                baseline, old.cutoffs, cg, cs)
            candidate.cutoffs = cuts
            tasks = {t:metrics(y[task_index==i], scores[task_index==i] > cuts[groups[task_index==i]])
                     for i,t in enumerate(ACTIVE_TASKS)}
            row = dict(algorithm=a, model_weight=weight, objective=value, eligible=bool(safe),
                       macro_f1=float(np.mean([m['f1'] for m in tasks.values()])),
                       original_calibration_alert_rate=float(np.mean(cs>cuts[cg])),
                       cutoffs=cuts.tolist(), tasks=tasks)
            trials.append(row)
            if safe and value > incumbent+1e-12:
                incumbent = value; winner = candidate
            print(a, weight, 'dev macro F1', round(row['macro_f1'], 5), 'eligible', safe, flush=True)
        selected[a] = winner
        pred = winner.predict(f)[1]
        results[a] = dict(baseline_development=baseline,
            selected_development={t:metrics(y[task_index==i], pred[task_index==i]) for i,t in enumerate(ACTIVE_TASKS)},
            selected=winner.record())
    joblib.dump(dict(reference=package['reference'], models=selected,
                     processor=package['processor']), output/'event_models.joblib', compress=3)
    write_json(output/'selection.json', dict(active_tasks=ACTIVE_TASKS, excluded_tasks=EXCLUDED_TASKS,
        source_sha256=old_sha, model_sha256=digest(output/'event_models.joblib'),
        fit_estimators_again=False, new_training_rows=0,
        selection_data='first 400 benchmark cases/task only; previously observed benchmark, not external blind test',
        objective='macro row F1 with per-task dev F1 nonregression and per-task FPR <= 6%; original calibration alert rate <= 6%',
        cold_decisions_preserved=True, no_task_routing_at_inference=True,
        results=results, candidates=trials))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'reports/process_event_model/four_task_refinement')
    run(parser.parse_args().output)
