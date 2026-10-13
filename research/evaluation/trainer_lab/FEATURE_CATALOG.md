Created: 2026-10-13T22:10:44+09:00
Updated: 2026-10-13T22:10:44+09:00
Author: trainer_data_catalog_builder
Status: draft

# Existing research feature catalog

- Scope: Issue #44 research reproduction; no production input contract or feature-toggle implementation
- Source: `research/anomaly_detection/code/process_anomaly_pipeline/features.py`, `model_evaluation/data.py`, `model_evaluation/windowing.py`, `model_evaluation/models.py`
- Algorithms: IsolationForest / MiniBatchKMeans; shared row/window feature computation
- Inputs: raw canonical PROCESS records or original template archive; BODY not a model input
- Time convention: offset-aware timestamps converted to Asia/Seoul; naive timestamps treated as local wall time by existing code
- Missing/invalid/duplicate inputs: aggregate audit; no silent deletion

## PROCESS — 10 features

| Feature | Calculation / unit | Required source | Availability |
|---|---|---|---|
| `log_duration_ms` | `log1p((endTime-startTime) milliseconds)`; nonnegative only | startTime, endTime | Completed PROCESS after endTime is observed |
| `missing_duration_ms` | Missing duration → 1; otherwise 0 | startTime, endTime | Input snapshot; incomplete PROCESS currently excluded by model eligibility |
| `log_totalCount` | `log1p(totalCount)`; nonnegative only | totalCount | Count present in observed snapshot; source finalization not independently proven |
| `missing_totalCount` | Missing totalCount → 1; otherwise 0 | totalCount | Input snapshot |
| `log_retryCount` | `log1p(retryCount)`; nonnegative only | retryCount | Input snapshot |
| `missing_retryCount` | Missing retryCount → 1; otherwise 0 | retryCount | Input snapshot; required-key eligibility separately enforced |
| `hour_sin` | `sin(2π × startTime.hour / 24)` | startTime | After startTime observed |
| `hour_cos` | `cos(2π × startTime.hour / 24)` | startTime | After startTime observed |
| `weekday_sin` | `sin(2π × startTime.dayofweek / 7)`; Monday=0 | startTime | After startTime observed |
| `weekday_cos` | `cos(2π × startTime.dayofweek / 7)`; Monday=0 | startTime | After startTime observed |

- Raw formula: no fit; nonfinite numeric results → missing
- Encoder fit: train-only feature medians; all-missing median → 0; train-only RobustScaler
- Transform: saved column order → saved median fill → saved scaler; float32
- All-constant training matrix: existing encoder rejects
- IDs/status/responseCode: audit/group/rule context; not numeric model inputs
- All existing 10 features preserved; no automatic weekday/holiday/month exclusion rule introduced
- Negative source values: separate quality error; missing flag is not a substitute for quality auditing

## TIME_WINDOW — 12 features

| Feature | Calculation | Required source / fit |
|---|---|---|
| `log_arrivals` | `log1p(A)` | A: execution arrivals in active lookback |
| `arrival_deviation` | `(A-H50)/(H95+1)` | Prior-history count median/P95 matching width, hour, weekend class |
| `log_arrivals_5m` | `log1p(A5)` | Native arrival counts summed over last five minutes |
| `arrival_deviation_5m` | `(A5-H50_5)/(H95_5+1)` | Matching five-minute prior history |
| `log_p50_duration` | `log1p(D50)` | Median of completed PROCESS durations in active lookback; milliseconds |
| `log_p95_duration` | `log1p(D95)` | P95 of completed PROCESS durations in active lookback; milliseconds |
| `p95_duration_relative` | `log1p(D95)-log1p(B)` | B: train duration median, Adapter+Process → Adapter → global fallback |
| `missing_duration` | Missing D95 → 1; otherwise 0 | No completed duration in the observed window |
| `hour_sin` | `sin(2π × window_start.hour / 24)` | Window-start timestamp, **not window-end timestamp** |
| `hour_cos` | `cos(2π × window_start.hour / 24)` | Window-start timestamp |
| `weekday_sin` | `sin(2π × window_start.dayofweek / 7)` | Window-start timestamp |
| `weekday_cos` | `cos(2π × window_start.dayofweek / 7)` | Window-start timestamp |

- Encoder: window train-only medians and RobustScaler; fixed `WINDOW_FEATURES` order
- Prior history: synthetic reference-history file; provenance/hash and pre-observation availability require separate audit
- Base one-minute version: five native minute count sums; summed history medians/P95 values
- Causal lookback version: `PriorHistory` recomputes actual rolling-count median/P95 for each width; numerical protocol differs from base version
- Completion quantiles: raw completed durations over `[window_end-lookback, window_end)`; no averaging minute quantiles
- Cold start: available history width only; matching-width prior reference
- Zero-arrival intervals: timer/grid required; absence of rows alone does not distinguish service failure from collection failure
- Existing selected window: five-minute lookback / one-minute decision grid; reproduction configuration only, operational interval not confirmed
- Native arrival reconstruction currently based on event start time; late arrival/source visibility not proven by offline timestamps
- Current lab CSV loader: PROCESS-shaped CSV only; a precomputed TIME_WINDOW CSV is not a supported substitute

## Fit and score roles

| Role | Existing implementation |
|---|---|
| Train | Encoder medians/scaler, IF/KMeans, duration baselines |
| Calibration | Raw score median and P95-minus-median scale; standard-deviation fallback |
| Threshold | Held-out score quantile; existing default P99, not a universally approved production default |
| Test | Held-out evaluation; no feature fit/normalization/threshold selection |

- Row split: transaction's first/last start dates; train 60% of observed days, next 20% validation, remaining test
- Validation: deterministic source+transaction hash split into calibration/threshold; neither is chronologically ordered before the other
- Boundary transactions: explicit exclusion; no splitting one transaction across train/validation/test
- Template/parent lineage: supplied identifiers audited across roles; absent provenance → unknown
- Model-only raw scores: IF `-score_samples`, KMeans nearest-center distance
- Normalized score: unbounded `(raw-center)/spread`; not probability
- Ensemble strategy: global/Adapter normalized scores combined; separate from choosing IF versus KMeans
- Source archive reproduction: original config retained; original comparison source-reference helper omitted test from its returned dictionary, this audit retains test for inspection

## Support / deferred definitions

| Candidate | Current status |
|---|---|
| Original TRANSACTION/PROCESS attributes | Source/audit candidate; not all enabled as model inputs |
| Existing row 10/window 12 | Formulas available; toggling arbitrary combinations not implemented |
| Prior semester success/error rates, count mismatch, step/group medians, robust Z, frequency encoding, holidays/month/week | Not implemented by these two fixed encoders; catalog definition and parity evidence required |
| `EndTime-ErrorTime` | Deferred: source ErrorTime/formula not confirmed; no replacement with duration assumed |
| SHAP | Not included in existing feature generation; explanation experiment separate |
| In-progress PROCESS elapsed duration | Not supported by current completed-event model; separate timer/Rule path |

- New feature selection → new fitted encoder/model/threshold bundle; changing a saved model's input columns prohibited
- Current fit role report: intended protocol only; actual fit membership proof belongs to runner/artifact validation
- Event timestamp consistency ≠ source field availability proof; missing observation timestamps remain unknown
