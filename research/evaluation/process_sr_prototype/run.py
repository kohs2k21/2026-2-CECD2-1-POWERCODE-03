"""One-shot re-run of the PROCESS-row SR prototype (Issue #75).

    .venv/Scripts/python.exe research/evaluation/process_sr_prototype/run.py \
        [--rows 3000] [--target-rows 10] [--seeds 11 23 47] [--out results]

Steps: fixture cohort -> chronological split -> S-only reference train -> fit every method instance
(train only) -> baseline predictions on the unmodified test cohort -> inject P01..P04 x severity x seed
with the repository research generator -> score -> per-trial / per-scenario tables -> timing.
Requires numpy + pandas only; scikit-learn / scipy are recorded in the manifest if present, never used.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sr_prototype import data, evaluate, methods  # noqa: E402

ORIGIN_COMMIT = '872f540f2a90af2ab44640e6b518617e5901f8cf'   # origin/dev base the reused research code was read at

# Method instances evaluated. label -> (method key, parameter overrides). Defaults live in methods.py.
INSTANCES = {
    'robust_z[duration_ms]': ('robust_z', {}),
    'robust_z[total_count]': ('robust_z', {'field': 'total_count'}),
    'robust_z[total_count,meanad]': ('robust_z', {'field': 'total_count', 'madZeroPolicy': 'meanad'}),
    'iqr_fence[duration_ms]': ('iqr_fence', {}),
    'iqr_fence[duration_ms,k=3]': ('iqr_fence', {'k': 3.0}),
    'iqr_fence[total_count]': ('iqr_fence', {'field': 'total_count'}),
    'quantile_tail[duration_ms]': ('quantile_tail', {}),
    'quantile_tail[duration_ms,min30,nofallback]': ('quantile_tail', {'minGroupRows': 30, 'fallback': 'none'}),
    'quantile_tail[total_count]': ('quantile_tail', {'field': 'total_count'}),
    'centered_tail[duration_ms]': ('centered_tail', {}),
    'centered_tail[total_count]': ('centered_tail', {'field': 'total_count'}),
    'ratio_rule': ('ratio_rule', {}),
    'count_conservation': ('count_conservation', {}),
}


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_head():
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=3000)
    parser.add_argument('--target-rows', type=int, default=10)
    parser.add_argument('--seeds', type=int, nargs='+', default=[11, 23, 47])
    parser.add_argument('--latency-rows', type=int, default=200)
    parser.add_argument('--out', default='results')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    # 1. cohort, split, reference train
    frame = data.fixture(args.rows)
    parts = data.chronological_split(frame)
    train_ref, train_info = data.train_reference(parts['train'])
    sr_train = data.to_sr_frame(train_ref)
    sr_test = data.to_sr_frame(parts['test'])
    config = data.experiment_config(args.target_rows, args.seeds)
    baselines = data.adapter_baselines(train_ref)

    # 2. fit (train only)
    fitted, timing = {}, []
    for label, (key, params) in INSTANCES.items():
        t0 = time.perf_counter()
        fitted[label] = methods.fit(key, sr_train, params)
        fit_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        base_pred = methods.score(key, sr_test, fitted[label])
        batch_ms = (time.perf_counter() - t0) * 1000
        fitted[label]['_base_pred'] = base_pred
        rows = sr_test.head(args.latency_rows).to_dict('records')
        lat = []
        for row in rows:
            t0 = time.perf_counter()
            methods.score_row(key, row, fitted[label])
            lat.append((time.perf_counter() - t0) * 1000)
        timing.append({'instance': label, 'method': key, 'fit_rows': len(sr_train), 'fit_ms': fit_ms,
                       'batch_rows': len(sr_test), 'batch_score_ms': batch_ms,
                       'row_latency_rows': len(lat), 'row_latency_mean_ms': float(np.mean(lat)),
                       'row_latency_p99_ms': float(np.quantile(lat, 0.99)), 'row_latency_max_ms': float(np.max(lat))})

    # 3. unmodified control
    controls = []
    for label, (key, _) in INSTANCES.items():
        controls.append(evaluate.control_metrics(fitted[label]['_base_pred'].assign(row_id=sr_test.row_id.values),
                                                 instance=label, method=key))

    # 4. scenarios
    trials, generation = [], []
    for scenario in config.scenarios:
        for severity in range(3):
            for seed in config.seeds:
                mutated, truth, meta = data.inject(parts['test'], baselines, config,
                                                   scenario=scenario, severity=severity, seed=seed)
                generation.append({k: v for k, v in meta.items()})
                if len(truth) == 0:
                    continue
                sr_after = data.to_sr_frame(mutated)
                truth_ids = set(truth.row_id)
                for label, (key, _) in INSTANCES.items():
                    pred = methods.score(key, sr_after, fitted[label]).assign(row_id=sr_after.row_id.values)
                    before = fitted[label]['_base_pred'].assign(row_id=sr_test.row_id.values)
                    trials.append(evaluate.trial_metrics(pred, before, truth_ids, instance=label, method=key,
                                                         scenario=scenario, severity=severity + 1, seed=seed,
                                                         generated_rows=int(meta.get('accepted_rows', 0))))

    # 5. tables
    trials_df = pd.DataFrame(trials)
    summary = evaluate.summarize(trials)
    timing_df = pd.DataFrame(timing)
    controls_df = pd.DataFrame(controls)
    gen_df = pd.DataFrame(generation)
    trials_df.to_csv(out / 'trials.csv', index=False)
    summary.to_csv(out / 'summary_by_instance_scenario.csv', index=False)
    timing_df.to_csv(out / 'timing.csv', index=False)
    controls_df.drop(columns=['reason_counts']).to_csv(out / 'control_unmodified_test.csv', index=False)
    gen_df.to_csv(out / 'generation_trials.csv', index=False)
    (out / 'catalog.json').write_text(json.dumps(methods.catalog(), ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'fitted_params.json').write_text(json.dumps(
        {label: {k: v for k, v in f.items() if not k.startswith('_')} for label, f in fitted.items()},
        ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'control_reasons.json').write_text(json.dumps(
        {c['instance']: c['reason_counts'] for c in controls}, ensure_ascii=False, indent=2), encoding='utf-8')
    pivot = summary.pivot(index='instance', columns='scenario', values='detection_rate').reindex(list(INSTANCES))
    pivot.to_csv(out / 'detection_rate_pivot.csv')

    manifest = {
        'prototype': methods.VERSION, 'issue': 75, 'originCommit': ORIGIN_COMMIT, 'sourceCommit': _git_head(),
        'fixtureSource': data.FIXTURE_SOURCE, 'researchImports': data.RESEARCH_IMPORTS,
        'python': sys.version, 'platform': platform.platform(),
        'packages': {'numpy': np.__version__, 'pandas': pd.__version__,
                     'scikit-learn': _package_version('scikit-learn'), 'scipy': _package_version('scipy')},
        'packagesNote': 'methods use numpy/pandas only; scikit-learn/scipy versions are recorded if installed, never imported',
        'args': vars(args), 'splitRows': {k: int(len(v)) for k, v in parts.items()},
        'trainReference': train_info, 'adapterBaselinesMs': baselines,
        'experimentConfig': config.to_dict(),
        'instances': {label: {'method': key, 'params': params} for label, (key, params) in INSTANCES.items()},
        'generationStatus': gen_df.groupby(['scenario', 'status']).size().astype(int).reset_index()
                                  .to_dict('records') if len(gen_df) else [],
        'labels': 'injected rows label=1 (controlled deviation, not proven incident); other rows unlabelled, not normal',
        'elapsedSeconds': time.perf_counter() - started,
    }
    (out / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    # 6. stdout
    print(f"rows={args.rows} split={manifest['splitRows']} trainReference={train_info}")
    print(f"adapter baselines (train median ms) = {baselines}")
    print('\n## generation status per scenario')
    print(gen_df.groupby(['scenario', 'status']).size().to_string())
    print('\n## detection rate (pooled over severity x seed) - instance x scenario')
    pv = pivot.reset_index()
    print(evaluate.markdown_table(pv, ['instance', *[c for c in pv.columns if c != 'instance']]))
    print('\n## per scenario detail')
    print(evaluate.markdown_table(summary, ['instance', 'scenario', 'trials', 'anomaly_rows', 'detected_anomaly_rows',
                                            'anomaly_unjudged_rows', 'detection_rate', 'baseline_alert_anomaly_rows',
                                            'newly_detected_rows', 'unlabeled_rows', 'unlabeled_alert_rows',
                                            'unlabeled_unjudged_rows', 'unlabeled_alert_rate']))
    print('\n## unmodified test cohort (no labels)')
    print(evaluate.markdown_table(controls_df, ['instance', 'rows', 'judged_rows', 'unjudged_rows',
                                                'unlabeled_alert_rows', 'unlabeled_alert_rate']))
    print('\n## timing')
    print(evaluate.markdown_table(timing_df, ['instance', 'fit_rows', 'fit_ms', 'batch_rows', 'batch_score_ms',
                                              'row_latency_mean_ms', 'row_latency_p99_ms', 'row_latency_max_ms']))
    print(f"\nelapsed {manifest['elapsedSeconds']:.1f}s -> {out.relative_to(ROOT.parents[2])}")


if __name__ == '__main__':
    main()
