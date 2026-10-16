# Long-term trend research lab (Issue 54)

This independent controlled minute-aggregate simulation compares statistical rules (SR), scikit-learn Isolation Forest (IF), and the authors' Extended Isolation Forest (EIF). It does not replay customer executions or establish business labels, operational accuracy, throughput, or an SLA. DeepIF is outside this experiment.

The frozen protocol is in `protocol.json`. The base recipe has 31 features; the trend recipe adds 12 bounded-window slope, contrast, variability, and count-duration relationship features. All models use the same observable source and eligible rows. Status, event truth, scenario, activity labels, and category identifiers are not model inputs. Unknown sources, unsupported categories, context preparation, and model failures retain explicit unassessed states. A known zero count remains a valid zero.

## Commands and output

Use Python with the repository's existing NumPy, pandas, scikit-learn, and threadpoolctl dependencies. IF/EIF require no PyTorch. Always disable bytecode and use one CPU thread. Output must be an explicit fresh directory outside this repository:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
python -B research/evaluation/long_term_trend/run_experiment.py screen --output <external-screen-directory>
python -B research/evaluation/long_term_trend/run_experiment.py evaluate-blind --screen <external-screen-directory> --output <fresh-external-blind-directory>
python -B research/evaluation/long_term_trend/run_experiment.py evaluate-cadence --screen <external-screen-directory> --output <fresh-external-cadence-directory>
```

`screen` opens only train, calibration, and selection. It fits 144 forests: two algorithms × two recipes × twelve windows × three paired seeds. SR statistics add no forest fits. It checkpoints failures, per-model outcomes, models, reference state, raw calibration/selection scores, source and data hashes, and costs. Selection freezes one recipe/window per algorithm over all three seeds. Every candidate must score 100% of eligible scheduled rows and satisfy each group's selection normal false-episode and positive-time constraints. The deterministic objective is scenario-macro actual-onset recall, then bounded mean delay, fewer false episodes, smaller window, and base recipe. Timing noise is not a selection criterion.

`selection-lock.json` pins the selected references, model hashes, protocol, source hashes, and thresholds before either holdout command can run. The commands validate the lock and model hashes, reuse the train reference and encoder, and perform zero new fits and zero recalibrations. Pickles are trusted local artifacts, verified against the lock before loading. Do not load artifacts supplied by an untrusted party. If no candidate is eligible, there is no operating winner. The predeclared IF/base/W1, EIF/base/W1, or SR/trend/W1 reference remains an explicitly ineligible diagnostic; failure to fit is unassessed rather than a fabricated score.

Outputs include `manifest.json`, full per-seed `results.json`, event records, pooled `aggregate.json`, decision/episode CSV files, `costs.json`, and cadence common-grid checks. The aggregate retains all 60 intended events, scenario and core/source-unknown/source-late/new-category subgroups, normal group-hours, false episodes, positive time, bounded delay, and three-seed recall range. Early hits are a subset of primary hits, never an additional success count.

## Fixed scoring and calibration

Forest preprocessing is fit on common normal train rows only: median and scale `max(q90-q10, 0.05)`. Both forests use 128 trees and at most 256 samples per tree. EIF reuses the pinned authors' tree builder and mathematically equivalent vectorized inference in the previous comparison module. Its measured cost is this laboratory implementation's cost. The original sources, licenses, commits, and verification manifest remain under `../if_feature_comparison/third_party/`; no author source is modified here.

SR receives all 43 trend features. For each physical feature its valid normal training rows set the median and `max(q90-q10, 0.05)` scale. Its score is the maximum absolute standardized deviation over valid physical features. `duration_observed`, `relation_valid`, and `relation_pair_fraction` are excluded from direct deviation scoring. There is no empirical-CDF-only SR score. IF raw scores and EIF raw scores are anomaly rankings, not probabilities.

One strict `score > cut` threshold simultaneously satisfies normal calibration for every group and cadence 1/5/15 minutes: false episodes ≤0.05 per monitored normal group-hour and positive held time ≤1%. Candidate thresholds are the predeclared higher quantiles 0.99, 0.9925, 0.995, 0.9975, 0.999, 0.9995, 1.0, plus the greatest per-group/cadence q0.99 floor. Candidates below that floor are removed; the maximum score is included. Every remaining candidate is checked in order because episode count is not monotone. This deliberately coarse grid can miss a less conservative feasible threshold; no candidate receives extra searches after observing test results.

The shared selected SR branch is paired with each selected forest. Each branch margin is `(raw - calibration_cut) / max(calibration_q95 - calibration_median, 1e-6)`. The combined score is their maximum, giving an OR at zero. A joint cut ≥0 is calibrated with the same normal-only grid and all-cadence guards, locked before holdouts, and also checked on selection. The score scale has no probability interpretation.

## Clock, episodes, and events

Source bins are `[bin_start, endpoint)`. At decision tick `t`, the last allowed source endpoint is `t - 2 minutes`; window endpoints lie in `(window_end-W, window_end]`, with observations available by `t`. Features use a shared one-minute causal trace; cadence only chooses the latest tick to score. Prefix context is excluded from scored/calendar exposure.

A decision emitted at `t` holds on `[t,t+C)`. It never backdates an alarm to an input bin. Current unknown or unready intervals stop the hold and break episode continuity. Positive intervals merge through at most ten minutes of known-negative gap. Consecutive positive C15 decisions therefore remain one episode.

Primary success requires a **new episode** beginning in `[actual event start, event end)`, with one episode matched to one event. A prior sustained alarm is not success. Actual start is the first changed source bin's start. An alarm before `confirmed_start` but after actual start is an early primary hit. Missed and unassessed events stay in the denominator; detected-only median and bounded delay (cap 360 minutes) are separate measures. A late tag adds no on-time credit. There is zero post-event FP exemption: residual alarms on recovered normal time contribute positive-time burden, and new normal episodes count as false episodes.

Native cadence results use each cadence's actual emission times, held states, and event delays. A separate common-15-minute check requires identical scores and predictions at shared ticks, with a common-grid evaluation shown separately. This is not a replacement for native cadence delay.

## Cost and interpretation

Feature construction CPU/wall time, batch scoring per native cadence, interval/event evaluation, disk use, and score ties are recorded separately. The cadence run additionally times at most 120 evenly spaced ready native ticks per seed/algorithm/cadence, scoring the available group batch with frozen preprocessing. These measurements exclude full feature construction and are not production tick latency. Virtual onset-to-emission delay is separate from measured CPU/wall cost. Full one-minute feature construction is shared experimental work, not an optimized streaming implementation.

Training and calibration budgets do not guarantee normal holdout budgets. Ineligible diagnostic references remain ineligible even if a holdout looks good. Unknown and new-category challenges intentionally reduce full-denominator availability. Controlled aggregates lack real execution mixtures, validated business semantics, real drift, and production late-arrival distributions. No production model, operating data, database, or service is changed.
