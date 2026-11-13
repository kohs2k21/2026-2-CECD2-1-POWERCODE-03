import type { EvaluationItem } from "@/services/api/pipeline.api";
import type { BasisRates, CompareVersion, ComparisonDetail, ComparisonItem, ComparisonListItem, ComparisonTab, Confusion, EvaluationSet, EvaluationSnapshot, Rate, Recommendation, RecommendationState } from "../src/features/detection/evaluations/evaluationsApi.ts";

/** 평가 결과 화면 시험용 예시 데이터(값은 config-comparison-evaluation.md 예시와 와이어프레임 #93). 시험의 가짜 API가 응답으로 돌려준다. 앱 소스에는 없다. */
const SOURCE = "5c0f8a11-7e21-4a6c-9b1d-2f64a7c0e101";
const SNAPSHOT = "8e13d6b2-40aa-4c17-a3ce-91b07f2d3a55";
const hash = (seed: string) => seed.repeat(8);


const SET_ESB = "e1a7f3c4-2b6d-4f10-8a55-6c1d90b2e7a1";
const SET_ADBENCH = "a1c92d07-5e34-4b8f-9d12-3f70e6b4c8d2";
const SET_ORIGINAL = "07d4e8b1-9c23-4a7e-b1f6-48a2c5d03e93";
const SET_QUEUED = "9b35c1e6-4d70-4e2a-8c09-7d16f3a2b5e4";
const SET_FAILED = "f2e60a98-1c47-4d3b-a6e8-05b9d7c41f65";

const esbSpec = { profileId: "esb-process-research-v1", profileVersion: "1", scenarios: [{ scenario: "P02", severity: 2 }, { scenario: "P04", severity: 2 }], seed: 42, repetitions: 2, targetRows: 50 };
export const evaluationSets: EvaluationSet[] = [
  { id: SET_ESB, sourceId: SOURCE, snapshotId: SNAPSHOT, purpose: "validation", displayName: "ESB 합성 E1", status: "ready", synthetic: esbSpec,
    generation: { rowCount: 2000, originalRowCount: 1800, syntheticRowCount: 200, truthCount: 200, contentHash: hash("4be2d190"), artifactRef: null }, createdAt: "2026-10-19T07:02:00Z", readyAt: "2026-10-19T07:03:00Z" },
  { id: SET_ADBENCH, sourceId: SOURCE, snapshotId: SNAPSHOT, purpose: "validation", displayName: "ADBench 합성 A1", status: "ready",
    synthetic: { profileId: "adbench-numeric-v1", profileVersion: "1", scenarios: [{ scenario: "local", severity: 1 }, { scenario: "global", severity: 1 }, { scenario: "cluster", severity: 1 }], seed: 7, repetitions: 2, targetRows: 50 },
    generation: { rowCount: 3000, originalRowCount: 0, syntheticRowCount: 3000, truthCount: 300, contentHash: hash("91f0c713"), artifactRef: null }, createdAt: "2026-10-19T07:10:00Z", readyAt: "2026-10-19T07:11:00Z" },
  { id: SET_ORIGINAL, sourceId: SOURCE, snapshotId: SNAPSHOT, purpose: "validation", displayName: "원본 검증 세트 10월", status: "ready", synthetic: null,
    generation: { rowCount: 1200, originalRowCount: 1200, syntheticRowCount: 0, truthCount: 20, contentHash: hash("c07a55e2"), artifactRef: null }, createdAt: "2026-10-30T02:00:00Z", readyAt: "2026-10-30T02:01:00Z" },
  { id: SET_QUEUED, sourceId: SOURCE, snapshotId: SNAPSHOT, purpose: "validation", displayName: "ESB 합성 E2", status: "queued", synthetic: { ...esbSpec, seed: 43 }, generation: null, createdAt: "2026-11-05T05:40:00Z", readyAt: null },
  { id: SET_FAILED, sourceId: SOURCE, snapshotId: SNAPSHOT, purpose: "test", displayName: "ESB 합성 E3", status: "failed", synthetic: { ...esbSpec, seed: 44 }, generation: null, createdAt: "2026-11-04T01:10:00Z", readyAt: null },
];

const V_ACTIVE = "4d2f7c10-6b1e-4e39-9a44-1c0e7b85d2a6";
const V_EXPERIMENT_7 = "7be19a34-0c5d-4f82-b3a1-92d6e4f07c18";
const V_EXPERIMENT_6 = "63c8d0f2-a914-47e5-8d20-5b7f1e3c9a04";
const V_PREVIOUS = "2a91e6d7-3f08-4bc2-a175-e04c8d6b1f39";
const V_EIF = "c5e07b42-8d16-4a93-bf60-3a29d1f7e856";
const V_KMEANS = "e8a3f1b6-2c74-49d0-96e1-7f05c4b2d913";
const IFOREST = "Isolation Forest";
export const compareVersions: CompareVersion[] = [
  { id: V_ACTIVE, displayName: "V4", modelKey: "iforest", modelName: IFOREST, active: true, executable: true, latestEvaluation: { evaluationId: "b0e2c6a1-77d3-4f1c-9e20-6a51d8f3c7b2" } },
  { id: V_EXPERIMENT_7, displayName: "실험 7", modelKey: "iforest", modelName: IFOREST, active: false, executable: true, latestEvaluation: { evaluationId: "d3f8a410-1b6e-4c95-a27d-08e7c1b5f963" } },
  { id: V_EXPERIMENT_6, displayName: "실험 6", modelKey: "iforest", modelName: IFOREST, active: false, executable: true, latestEvaluation: null },
  { id: V_PREVIOUS, displayName: "V3", modelKey: "iforest", modelName: IFOREST, active: false, executable: true, latestEvaluation: { evaluationId: "6ac17e93-2d50-4b8e-b1f4-c97e30a2d581" } },
  { id: V_EIF, displayName: "EIF 실험 2", modelKey: "eif", modelName: "Extended Isolation Forest", active: false, executable: false, latestEvaluation: null },
  { id: V_KMEANS, displayName: "KMeans · K=6", modelKey: "minibatch_kmeans", modelName: "MiniBatch K-Means", active: false, executable: false, latestEvaluation: null },
];

/** 세트 기준 스냅샷 = 모델 버전의 학습 스냅샷(`GET /api/evaluation-sets/snapshots`). 첫째는 V4·실험 7, 둘째는 V3이 학습한 데이터. */
export const snapshots: EvaluationSnapshot[] = [
  { id: SNAPSHOT, sourceId: SOURCE, createdAt: "2026-10-18T00:00:00Z", from: "2026-10-01T00:00:00Z", until: "2026-10-15T00:00:00Z", configVersionIds: [V_ACTIVE, V_EXPERIMENT_7] },
  { id: "3c7e1f90-b52d-4a68-9e14-d0a6f2c8b731", sourceId: SOURCE, createdAt: "2026-09-20T00:00:00Z", from: "2026-09-01T00:00:00Z", until: "2026-09-15T00:00:00Z", configVersionIds: [V_PREVIOUS] },
];

export const ruleRevision = 13;
const ACTIVE_REVISION = 12;

const rate = (numerator: number, denominator: number, coverage: number): Rate => ({ value: numerator / denominator, unit: "ratio", numerator, denominator, coverage, reason: null });
const noNormal: Rate = { value: null, unit: "ratio", numerator: null, denominator: null, coverage: null, reason: "verified_normal_labels_unavailable" };
const esbRates = (recall: Rate, unavailableCount = 0): BasisRates => ({ recall, precision: noNormal, falsePositiveRate: noNormal, unavailableCount });
const metric = (value: number, unit: string, numerator: number | null = null, denominator: number | null = null) => ({ value, unit, numerator, denominator, coverage: null, reason: null });
const ruleMl = (both: number, rule: number, ml: number, neither: number): EvaluationItem => {
  const total = both + rule + ml + neither;
  return { status: "measured", reason: null, metricVersion: "rule-ml-comparison-v1",
    metrics: { hrBoth: metric(both / total, "ratio", both, total), hrRuleOnly: metric(rule / total, "ratio", rule, total), hrMlOnly: metric(ml / total, "ratio", ml, total), hrNeither: metric(neither / total, "ratio", neither, total) } };
};
const cost = (itemP95Ms: number, throughput: number, processRssBytes: number): EvaluationItem => ({
  status: "measured", reason: null, metricVersion: "execution-cost-v1",
  metrics: { itemP95Ms: metric(itemP95Ms, "ms"), throughput: metric(throughput, "rows/s"), processRssBytes: metric(processRssBytes, "bytes") },
});
const noRuleInput: EvaluationItem = { status: "not_applicable", reason: "ADBench 행에는 룰 입력 필드가 없음", metrics: null, metricVersion: "rule-ml-comparison-v1" };
const operational: EvaluationItem = { status: "unavailable", reason: "operational_report_separate_time_scoped", metrics: null, metricVersion: "operational-v1" };
const measured = ({ id, modelKey = "iforest", role, confusion, completeOnly, decidable, unavailableCount, composite, rules, itemCost, reasonCodes = ["synthetic_set_evaluation"] }: {
  id: string; modelKey?: string; role: ComparisonItem["role"]; confusion: Confusion; completeOnly: BasisRates; decidable: BasisRates; unavailableCount: number;
  composite: ComparisonItem["composite"]; rules: EvaluationItem; itemCost: EvaluationItem; reasonCodes?: string[];
}): ComparisonItem => ({
  configVersionId: id, modelKey, role, status: "succeeded", errorCode: null, evaluationId: `${id.slice(0, 8)}-0000-4000-8000-000000000000`, reasonCodes, composite,
  categoryResults: { ml: { measurementStatus: "measured", methodCount: 1, confusion, unavailableCount, completeOnly, decidable } },
  evaluationEnvelope: { rule_ml: rules, execution_cost: itemCost, operational },
});
const failedItem = (id: string, modelKey: string, errorCode: string): ComparisonItem => ({ configVersionId: id, modelKey, role: "candidate", status: "failed", errorCode, evaluationId: null });

const recommend = (comparisonJobId: string, generatedAt: string, recommendationState: RecommendationState, modelKey: string, summary: string, baseline: string | null, target: string | null, noRecommendationReason: string | null = null): Recommendation => ({
  modelKey, comparisonJobId, baselineConfigVersionId: baseline, targetVersionId: target, targetEvaluationId: target ? `${target.slice(0, 8)}-0000-4000-8000-000000000000` : null, summary, noRecommendationReason, generatedAt, recommendationState,
});

const C_RUNNING = "3fa1c7e2-90b4-4d68-a2e1-5c7d0f9b1a10";
const C_LATEST = "b84e6d15-27a0-4c3f-9e71-0d6f3a1c8e22";
const C_ORIGINAL = "1d7f0a93-c5e2-48b6-b730-6e92a4d1f533";
const C_FAILED = "92c4b7e0-1f36-4ad5-8c02-f7a15d3e6b44";
const C_ADBENCH = "60e3d9a8-7b21-4f05-a6c4-2e81b7f0c955";
const C_ESB_OLD = "d17a2c5f-3e84-49b0-b6d3-94c0e1f7a266";
const job = (seed: string) => `${seed}0000-1111-4222-8333-444455556666`;
const baseline = (configVersionId: string) => [{ modelKey: "iforest", configVersionId }];

const details: ComparisonDetail[] = [
  { comparisonId: C_RUNNING, jobId: job("aa11"), status: "running" },
  {
    comparisonId: C_LATEST, jobId: job("bb22"), status: "succeeded",
    evaluationSet: { id: SET_ESB, contentHash: hash("4be2d190"), purpose: "validation", rowCount: 2000, originalRowCount: 1800, syntheticRowCount: 200, synthetic: true },
    baselines: baseline(V_ACTIVE), ruleSet: { revision: 13, contentHash: hash("9c40e1aa") },
    replacementRecommendations: [
      recommend(job("bb22"), "2026-11-05T02:20:00Z", "none", "eif", "EIF 계열은 현재 운영 중인 버전이 없어 추천하지 않습니다", null, null, "active_missing"),
      recommend(job("bb22"), "2026-11-05T02:20:00Z", "current", "iforest", "실험 7이 현재 운영 V4보다 모델 재현율 12/18(0.667) > 9/18(0.500), P04 sev1 모델 탐지율 12/18 > 10/18, 지연 P95 41ms(한도 내)", V_ACTIVE, V_EXPERIMENT_7),
    ],
    items: [
      measured({ id: V_ACTIVE, role: "active", confusion: { tp: 9, fp: null, tn: null, fn: 9 }, unavailableCount: 12, completeOnly: esbRates(rate(9, 18, 0.9), 12), decidable: esbRates(rate(10, 19, 0.95), 6),
        composite: { labelCounts: { anomalyLabelCount: 20, normalLabelCount: 0, unlabeledCount: 1180 }, completeOnly: { ...esbRates(rate(10, 18, 0.9), 12), confusion: { tp: 10, fp: null, tn: null, fn: 8 }, hitCount: 31 } },
        rules: ruleMl(6, 3, 4, 1175), itemCost: cost(37.9, 212, 409000000), reasonCodes: ["selected_paths_unavailable", "synthetic_set_evaluation"] }),
      measured({ id: V_EXPERIMENT_7, role: "candidate", confusion: { tp: 12, fp: null, tn: null, fn: 6 }, unavailableCount: 0, completeOnly: esbRates(rate(12, 18, 1)), decidable: esbRates(rate(12, 18, 1)),
        composite: { labelCounts: { anomalyLabelCount: 20, normalLabelCount: 0, unlabeledCount: 1180 }, completeOnly: { ...esbRates(rate(13, 20, 1)), confusion: { tp: 13, fp: null, tn: null, fn: 7 }, hitCount: 29 } },
        rules: ruleMl(8, 2, 4, 1186), itemCost: cost(41.2, 198, 412000000) }),
      failedItem(V_EIF, "eif", "method_not_executable:ml:eif"),
    ],
  },
  {
    comparisonId: C_ORIGINAL, jobId: job("cc33"), status: "succeeded",
    evaluationSet: { id: SET_ORIGINAL, contentHash: hash("c07a55e2"), purpose: "validation", rowCount: 1200, originalRowCount: 1200, syntheticRowCount: 0, synthetic: false },
    baselines: baseline(V_ACTIVE), ruleSet: { revision: 12, contentHash: hash("71d2b6f0") },
    replacementRecommendations: [recommend(job("cc33"), "2026-11-03T06:00:00Z", "none", "iforest", "현재 운영 모델보다 모든 근거 지표에서 같거나 나은 버전이 없습니다.", V_ACTIVE, null, "no_candidate_better_than_active")],
    items: [
      measured({ id: V_ACTIVE, role: "active", confusion: { tp: 14, fp: 3, tn: 80, fn: 6 }, unavailableCount: 0, reasonCodes: [],
        completeOnly: { recall: rate(14, 20, 1), precision: rate(14, 17, 1), falsePositiveRate: rate(3, 83, 1), unavailableCount: 0 }, decidable: { recall: rate(14, 20, 1), precision: rate(14, 17, 1), falsePositiveRate: rate(3, 83, 1), unavailableCount: 0 },
        composite: { labelCounts: { anomalyLabelCount: 20, normalLabelCount: 83, unlabeledCount: 1097 }, completeOnly: { recall: rate(15, 20, 1), precision: rate(15, 19, 1), falsePositiveRate: rate(4, 83, 1), unavailableCount: 0, confusion: { tp: 15, fp: 4, tn: 79, fn: 5 }, hitCount: 22 } },
        rules: ruleMl(10, 4, 3, 1183), itemCost: cost(36.4, 220, 405000000) }),
      measured({ id: V_EXPERIMENT_6, role: "candidate", confusion: { tp: 13, fp: 4, tn: 79, fn: 7 }, unavailableCount: 0, reasonCodes: [],
        completeOnly: { recall: rate(13, 20, 1), precision: rate(13, 17, 1), falsePositiveRate: rate(4, 83, 1), unavailableCount: 0 }, decidable: { recall: rate(13, 20, 1), precision: rate(13, 17, 1), falsePositiveRate: rate(4, 83, 1), unavailableCount: 0 },
        composite: { labelCounts: { anomalyLabelCount: 20, normalLabelCount: 83, unlabeledCount: 1097 }, completeOnly: { recall: rate(14, 20, 1), precision: rate(14, 19, 1), falsePositiveRate: rate(5, 83, 1), unavailableCount: 0, confusion: { tp: 14, fp: 5, tn: 78, fn: 6 }, hitCount: 23 } },
        rules: ruleMl(9, 5, 4, 1182), itemCost: cost(38.8, 205, 407000000) }),
    ],
  },
  { comparisonId: C_FAILED, jobId: job("dd44"), status: "failed", errorCode: "comparison_all_items_failed" },
  {
    comparisonId: C_ADBENCH, jobId: job("ee55"), status: "succeeded",
    evaluationSet: { id: SET_ADBENCH, contentHash: hash("91f0c713"), purpose: "validation", rowCount: 3000, originalRowCount: 0, syntheticRowCount: 3000, synthetic: true },
    baselines: baseline(V_PREVIOUS), ruleSet: { revision: 11, contentHash: hash("5e8fa302") },
    replacementRecommendations: [recommend(job("ee55"), "2026-10-29T07:20:00Z", "applied", "iforest", "V4가 현재 운영 V3보다 모델 재현율 268/300(0.893) > 240/300(0.800), 지연 P95 36ms(한도 내)", V_PREVIOUS, V_ACTIVE)],
    items: [
      measured({ id: V_PREVIOUS, role: "active", confusion: { tp: 240, fp: 54, tn: 2646, fn: 60 }, unavailableCount: 0,
        completeOnly: { recall: rate(240, 300, 1), precision: rate(240, 294, 1), falsePositiveRate: rate(54, 2700, 1), unavailableCount: 0 }, decidable: { recall: rate(240, 300, 1), precision: rate(240, 294, 1), falsePositiveRate: rate(54, 2700, 1), unavailableCount: 0 },
        composite: null, rules: noRuleInput, itemCost: cost(35.1, 240, 401000000) }),
      measured({ id: V_ACTIVE, role: "candidate", confusion: { tp: 268, fp: 41, tn: 2659, fn: 32 }, unavailableCount: 0,
        completeOnly: { recall: rate(268, 300, 1), precision: rate(268, 309, 1), falsePositiveRate: rate(41, 2700, 1), unavailableCount: 0 }, decidable: { recall: rate(268, 300, 1), precision: rate(268, 309, 1), falsePositiveRate: rate(41, 2700, 1), unavailableCount: 0 },
        composite: null, rules: noRuleInput, itemCost: cost(36, 232, 404000000) }),
    ],
  },
  {
    comparisonId: C_ESB_OLD, jobId: job("ff66"), status: "succeeded",
    evaluationSet: { id: SET_ESB, contentHash: hash("4be2d190"), purpose: "validation", rowCount: 2000, originalRowCount: 1800, syntheticRowCount: 200, synthetic: true },
    baselines: baseline(V_PREVIOUS), ruleSet: { revision: 10, contentHash: hash("0be47d19") },
    replacementRecommendations: [recommend(job("ff66"), "2026-10-27T00:30:00Z", "stale", "iforest", "실험 6이 현재 운영 V3보다 모델 재현율 11/18(0.611) > 9/18(0.500), 지연 P95 40ms(한도 내)", V_PREVIOUS, V_EXPERIMENT_6)],
    items: [
      measured({ id: V_PREVIOUS, role: "active", confusion: { tp: 9, fp: null, tn: null, fn: 9 }, unavailableCount: 0, completeOnly: esbRates(rate(9, 18, 1)), decidable: esbRates(rate(9, 18, 1)),
        composite: { labelCounts: { anomalyLabelCount: 18, normalLabelCount: 0, unlabeledCount: 1982 }, completeOnly: { ...esbRates(rate(10, 18, 1)), confusion: { tp: 10, fp: null, tn: null, fn: 8 }, hitCount: 28 } },
        rules: ruleMl(6, 4, 3, 1187), itemCost: cost(38.2, 208, 402000000) }),
      measured({ id: V_EXPERIMENT_6, role: "candidate", confusion: { tp: 11, fp: null, tn: null, fn: 7 }, unavailableCount: 0, completeOnly: esbRates(rate(11, 18, 1)), decidable: esbRates(rate(11, 18, 1)),
        composite: { labelCounts: { anomalyLabelCount: 18, normalLabelCount: 0, unlabeledCount: 1982 }, completeOnly: { ...esbRates(rate(12, 18, 1)), confusion: { tp: 12, fp: null, tn: null, fn: 6 }, hitCount: 30 } },
        rules: ruleMl(7, 3, 4, 1186), itemCost: cost(40.3, 201, 406000000) }),
    ],
  },
];

/** v1 서버 모양: 교체 추천 없음(null + `not_implemented_v1`), 룰+모델·실행 비용 없음. 목록에는 없고 상세로만 연다. */
export const C_V1 = "5e1d7a20-3c4b-4d5e-8f60-718293a4b5c6";
const v1Item = (configVersionId: string, role: ComparisonItem["role"], tp: number, fn: number): ComparisonItem => ({
  configVersionId, modelKey: "iforest", role, status: "succeeded", errorCode: null, evaluationId: `${configVersionId.slice(0, 8)}-1111-4000-8000-000000000000`, reasonCodes: [],
  categoryResults: { ml: { measurementStatus: "measured", confusion: { tp, fp: null, tn: null, fn }, unavailableCount: 0, completeOnly: esbRates(rate(tp, tp + fn, 1)) } },
});
details.push({
  comparisonId: C_V1, jobId: job("ab12"), status: "succeeded", completedAt: "2026-11-13T01:00:00Z",
  evaluationSet: { id: SET_ORIGINAL, contentHash: hash("c07a55e2"), purpose: "validation", rowCount: 1200, originalRowCount: 1200, syntheticRowCount: 0, synthetic: false },
  baselines: baseline(V_ACTIVE), ruleSet: { revision: 13, contentHash: hash("9c40e1aa") },
  replacementRecommendations: null, replacementRecommendationsReason: "not_implemented_v1",
  items: [v1Item(V_ACTIVE, "active", 9, 11), v1Item(V_EXPERIMENT_7, "candidate", 14, 6)],
});

const listRow = (comparisonId: string, row: Pick<ComparisonListItem, "evaluationSetId" | "activeModelSetRevision" | "completedAt"> & { itemCount: number }): ComparisonListItem => {
  const detail = details.find((item) => item.comparisonId === comparisonId)!;
  const items = detail.items ?? [];
  return {
    comparisonId, jobId: detail.jobId, status: detail.status, baselines: detail.baselines ?? baseline(V_ACTIVE),
    succeededCount: items.filter((item) => item.status === "succeeded").length, failedCount: items.filter((item) => item.status === "failed").length,
    recommendations: (detail.replacementRecommendations ?? []).map((rec) => ({ modelKey: rec.modelKey, targetVersionId: rec.targetVersionId, state: rec.recommendationState })),
    ...row,
  };
};
export const comparisons: ComparisonListItem[] = [
  listRow(C_RUNNING, { evaluationSetId: SET_ESB, activeModelSetRevision: 12, completedAt: null, itemCount: 3 }),
  listRow(C_LATEST, { evaluationSetId: SET_ESB, activeModelSetRevision: 12, completedAt: "2026-11-05T02:20:00Z", itemCount: 3 }),
  listRow(C_ORIGINAL, { evaluationSetId: SET_ORIGINAL, activeModelSetRevision: 12, completedAt: "2026-11-03T06:00:00Z", itemCount: 2 }),
  listRow(C_FAILED, { evaluationSetId: SET_ADBENCH, activeModelSetRevision: 12, completedAt: "2026-11-01T01:40:00Z", itemCount: 2 }),
  listRow(C_ADBENCH, { evaluationSetId: SET_ADBENCH, activeModelSetRevision: 11, completedAt: "2026-10-29T07:20:00Z", itemCount: 2 }),
  listRow(C_ESB_OLD, { evaluationSetId: SET_ESB, activeModelSetRevision: 10, completedAt: "2026-10-27T00:30:00Z", itemCount: 2 }),
];

const PAGE_SIZE = 5;
/** 현재 운영 모델 기준 탭은 와이어프레임 규칙(접수 때 활성 집합 버전이 지금과 같고 실패가 아님)을 예시에만 적용한다. 계약의 조회 쿼리에 이 조건이 없다. */
export const comparisonPage = (tab: ComparisonTab, cursor: string | null) => {
  const rows = tab === "all" ? comparisons : comparisons.filter((row) => row.activeModelSetRevision === ACTIVE_REVISION && row.status !== "failed");
  const start = cursor ? Number(cursor) : 0;
  const end = start + PAGE_SIZE;
  const hasMore = end < rows.length;
  return { items: rows.slice(start, end), pagination: { limit: PAGE_SIZE, hasMore, nextCursor: hasMore ? String(end) : null } };
};
export const comparisonDetail = (id: string) => details.find((item) => item.comparisonId === id);
/** 작업 ID로 연 상세(완료 전 비교는 작업 ID로 연다). */
export const comparisonDetailByJob = (jobId: string) => details.find((item) => item.jobId === jobId);
