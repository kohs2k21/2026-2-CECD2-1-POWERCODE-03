# Recall / non-regression improvement

- Scope: unchanged five-task mild-v2 benchmark, one shared IF / K-means / LOF each; ADBench unchanged
- Baseline: v5 release, not the failed v4 IF
- Maintain pooled Accuracy, Precision and F1; each model Recall >=60%; aim +5..10pp where feasible
- Keep every task normal FPR <=5%, no zero-task regression, previous minimum/median F1 gates
- User asked asynchronously whether Recall >=60% applies to pooled or every task; continue both diagnostics while waiting
- Do not force algorithm roles, fabricate performance, use task identity as input, alter benchmark severity or introduce a hidden classifier
- Prior turn was verified progress (completed v5); this goal begins with fresh state inspection and a frozen v5 baseline

## Approach

1. Diagnose weak signal utilization against the saved baseline
2. Normal-only compact evidence representations / feature weighting / estimator and normal-calibration tuning
3. Freeze attempts, evaluate the full unchanged five tasks; reject pooled metric regressions
4. Independent metric and causal inference audits; execute a readable notebook; summarize real strengths and limitations

Git: Issue38 verified closed through GitHub API; existing checkout clean on feat/38-anomaly-detection. Local research only, no pushes or Issue/PR mutations.

## Completion evidence — 2026-10-18

- Scope was frozen before experiments as each algorithm's pooled Recall/Accuracy/Precision/F1; the user was asked asynchronously whether Recall60 should instead apply to every task, with no reply received. Reports explicitly say the per-task Recall60 criterion is NOT achieved
- All unchanged 1,927,972 minute decisions evaluated; every y/case-index/Adapter vector aligned with v5
- Final IF: Accuracy 93.0640%, Precision 87.5654%, Recall 69.5845%, F1 77.5463%, pooled FPR 2.0544%
- Final K-means: 92.7763% / 88.8326% / 66.3765% / 75.9800% / 1.7349%, existing model retained
- Final LOF: 92.3951% / 84.3860% / 68.4904% / 75.6118% / 2.6348%
- Each model pooled Accuracy/Precision/F1 >= v5; each pooled Recall >=60%; every task FPR <5%, maximum 4.7221%
- Old task minimum F1 >=30% and median >=60% preserved; task-minimum/median also >= their respective v5 values
- IF Recall +20.34pp, F1 +17.51pp, Precision +10.69pp; LOF Recall +9.73pp and F1 +6.59pp; KM improvement candidates did not justify replacing the stable old model
- Important counterevidence: IF individual-delay F1 64.30 ->52.53%, input-stop 86.07 ->84.69%; LOF task Precision not universally preserved; pooled LOF FPR +0.24pp but under requested cap. These are visible in the report, not claimed as all-task non-regression
- Real roles quantified with paired-case bootstrap: IF best F1 on group-delay/input-stop; KM on time-burst; LOF on individual-delay. Gradual-degradation IF/LOF gap 0.15pp has a bootstrap interval crossing zero, so no exclusive strength claimed
- Feature ablations: robust scales, whitening, empirical tails, joint summaries, cadence weighting, preserving old geometry with appended features. Failed candidates retained. Final IF has 15 source inputs -> one maximum-evidence coordinate; not falsely described as a 15-dimensional combination forest
- Independent sklearn confusion/metric checks and 79 tests passed; raw Process inference, save/load equality, unknown Adapter and causal prefix checks passed
- Notebook executed: 18 cells / 8 code cells, no errors; ordinary bars and score-threshold histograms visually inspected
- Current artifact hashes verified under `reports/shared_model_v6/release/handoff`; v5 and experimental attempts preserved
- Benchmark CSV checksums, labels, severity and ADBench unchanged; no point adjustment or hidden model ensemble
- Reused benchmark labels participated in research candidate/adoption decisions; explicitly NOT an external blind evaluation
- Git worktree clean after work; no GitHub push or Notion page update performed
