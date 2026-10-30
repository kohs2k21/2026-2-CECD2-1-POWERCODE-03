# Five-task model improvement

## Required outcome

- Remove column-relation and mixed-Hard tasks from active evaluation, selection and current reporting; preserve retired source files outside the active dataset for recovery
- Keep the five remaining task CSV rows, labels and severity unchanged
- IF, K-means and LOF each: every task F1 >= 30%, median of the five task F1 >= 60%, every task and pooled normal FPR < 5%
- Preserve a single shared detector per algorithm, no Adapter experts; do not change ADBench data/results
- Verify full unchanged five-task evaluation, portable saved artifacts and executed readable notebook; update the current Notion report consistently

## Work sequence

1. Freeze scope, per-task metrics and acceptance gates; recompute identical five-task baselines
2. Search existing/new normal-only detector representations on separate development data with per-task constraints
3. Freeze candidate settings and normal-calibration cutoffs, evaluate all five existing task datasets without point adjustment
4. Archive retired tasks and reconcile active manifests, evaluation configuration, generators and report entry points
5. Audit labels/counts, source overlap, actual estimator settings, raw inference, save/load and causal prefix behavior; create notebook and refresh Notion

## Evidence discipline

- A high pooled F1 cannot bypass a failed task gate
- Removing tasks is an explicit scope change, not claimed as model improvement
- Training uses normal candidates; development labels may select hyperparameters, never fit the normal detector
- Existing benchmark reuse is adaptive research; frozen attempts and failed results stay recorded
- No GitHub push, branch mutation or production deployment is authorized by this task

## Completed evidence — 2026-10-18

- Scope frozen before new work; baseline metrics recomputed on exactly the same retained five tasks
- Active manifests and generator CLI now use five tasks; historical scenario-bit positions kept unchanged
- Two retired directories moved recoverably to `reports/retired_tasks_20261018`; original root metadata copied there
- Artifact Tool authored the five-row scenario summary; all 63 retained/archived task CSV hashes verified unchanged; ADBench NPZ unchanged
- Existing candidate search used only the two development folds, with retired-task rows filtered out; LOF search's empty-feasible-set logging failure fixed
- Development gates did not all pass for IF/LOF. They are not represented as passing development results. Final adoption is explicitly based on the adaptively reused research benchmark, not an independent blind test
- First frozen LOF attempt: time-burst F1 53.07%, but group-delay normal FPR 5.5835%; rejected despite pooled FPR passing
- Normal calibration cutoff candidates P99.25/P99.5/P99.75 recorded in `adoption_candidates.json`; adopted P99.5 from the feasible candidates after research-benchmark inspection
- Current release: `reports/shared_model_v5/release`; minimum/median task F1 and maximum task FPR:
  - IF: 39.89% / 64.30% / 4.9971% — older balanced estimator restored, not newly trained
  - K-means: 62.63% / 72.07% / 2.8531% — existing estimator retained
  - LOF: 50.37% / 70.45% / 4.8155% — linear+whitening, 200 neighbors, 2048 references, availability-CDF calibration
- All pooled FPRs below 5%; independent confusion counts and 71 unit tests passed
- Portable monitors exported; raw Process input, no runtime research-data reads, save/load identity, unknown Adapter fallback and causal prefix checks passed
- Notebook executed: 18 cells including 9 code cells, no error output; ordinary-bar plots and explicit trade-offs included
- Notion page updated and read back: eight new resolved figures, five active task sections, latest metrics and caveats, historical v18 content preserved
- Same-five-task pooled F1 declines are disclosed: IF 68.76% -> 60.03%, LOF 73.69% -> 69.02%; K-means 75.98% unchanged
- IF maximum FPR is very close to the cutoff; this is a numerical research gate pass, not an operational guarantee
- Git worktree remained clean; no commits/pushes/branch changes or production deployment performed
