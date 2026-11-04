import type { RecommendationResult } from "./ruleAiApi";
import type { RuleSetState } from "./ruleSetApi";

/**
 * 개발 모드 전용 예시: llm-recommendation-job.md "결과 JSON" 절의 예시 그대로(tests/rules-page.mjs가 문서와 대조).
 * 서버가 recommend_thresholds를 받기 전까지만 쓴다. 운영 빌드에는 들어가지 않는다(tests/rules-dist.mjs).
 * 서버가 준비되면 이 파일과 ruleAiApi.ts의 devSampleAfter를 지운다.
 */
const evidence = (n: number, min: number, max: number, p95: number, baselineId: string | null) => ({
  n, min, max, p95, quantileMethod: "linear_(n-1)*p",
  exclusionRule: "status=S, explicit anomaly labels excluded; unlabeled retained",
  measurementId: "m_…", baselineId,
});

export const devRecommendationResult: RecommendationResult = {
  recommendationContractVersion: "threshold-recommendation-v1",
  mode: "data_p95_with_llm_rationale",
  ruleSet: { revision: 13, contentHash: "9c40…" },
  data: { snapshotId: "<snapshot UUID>", split: "train", timeBasis: "esb_occurred_at",
    exclusionRule: "status=S, explicit anomaly labels excluded; unlabeled retained", rowCount: 47120 },
  provider: "rule_v2",
  adapterSha: "…",
  items: [
    { field: "elapsed_ms", softRuleIds: ["soft:elapsed_ms-3f9a2c1d"], currentThreshold: 5000,
      operator: "gt", threshold: "7210.5", displayThreshold: "7211", unit: "ms",
      source: "data_p95", status: "proposed", evidence: evidence(46980, 12.0, 184233.0, 7210.5, "b_…"),
      rationale: "The 95th percentile of 46,980 successful process durations is 7,210.5 ms; durations above it are rare in the reference period.",
      aiProposal: null, reviews: { schema: true, facts: true, expectedTarget: true } },
    { field: "error_ratio", softRuleIds: ["soft:error_ratio-5d2e0b19"], currentThreshold: 0.01,
      operator: "gt", threshold: "0.00003125", displayThreshold: "0.00003125", unit: "ratio",
      source: "data_p95", status: "proposed", evidence: evidence(46980, 0.0, 0.5, 0.00003125, "b_…"),
      rationale: "…", aiProposal: null, reviews: { schema: true, facts: true, expectedTarget: true } },
    { field: "retry_count", softRuleIds: ["soft:retry_count-91ab77e0"], currentThreshold: 3,
      operator: null, threshold: null, displayThreshold: null, unit: "count",
      source: "data_p95", status: "no_applicable", evidence: evidence(46980, 0.0, 0.0, 0.0, null),
      rationale: "All reference values are 0; a percentile threshold has no discriminative value.",
      aiProposal: null, reviews: { schema: true, facts: true, expectedTarget: true } },
    { field: "total_count", softRuleIds: ["soft:total_count-0a1b2c3d"], currentThreshold: 10000,
      operator: null, threshold: null, displayThreshold: null, unit: "count",
      source: "data_p95", status: "rejected", rejectedReason: "invalid_generation_output", evidence: evidence(46980, 1.0, 25100.0, 9800.0, "b_…"),
      rationale: null, aiProposal: null, reviews: { schema: false, facts: false, expectedTarget: false } },
  ],
  counts: { targets: 4, proposed: 2, requestEvidence: 0, deferred: 0, noApplicable: 1, rejected: 1 },
};

/** 예시의 규칙 id를 화면의 켜진 규칙 id로 맞춘다(같은 필드 기준). 맞는 규칙이 없는 항목은 표에 나오지 않는다. */
export const devRecommendationFor = (state: RuleSetState): RecommendationResult => ({
  ...devRecommendationResult,
  items: devRecommendationResult.items.map((item) => ({ ...item, softRuleIds: state.softRules.filter((rule) => rule.enabled && rule.field === item.field).map((rule) => rule.id) })),
});
