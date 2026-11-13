// 평가 결과 API 가짜 응답(시험 전용): evaluations-sample.ts 값을 서버 응답 모양(config-comparison-evaluation.md v1, evaluations-ui.md)으로 돌려준다.
// `up=false`면 평가 API가 없는 서버처럼 503 service_unavailable(성공으로 바꾸지 않는 화면 확인용).
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** 비교 대상 후보(`GET /api/configurations`)의 모델 버전 모양. 화면이 쓰는 키만 채운다. */
export const configurationOf = (version) => ({
  id: version.id, sourceId: "5c0f8a11-7e21-4a6c-9b1d-2f64a7c0e101", schemaVersion: 2, displayName: version.displayName, contentHash: "c".repeat(64),
  createdAt: "2026-11-01T00:00:00Z", modelKey: version.modelKey, active: version.active, activeSince: null, createdFrom: { kind: "train", trainJobId: null },
  executable: version.executable, methods: [], trainingSelection: { trainingPeriod: null, excludedIntervalCount: 0 }, modelSummary: null,
  latestEvaluation: version.latestEvaluation ? { ...version.latestEvaluation, applicability: "valid", completedAt: "2026-11-02T00:00:00Z" } : null,
  applicationEligibility: { eligible: Boolean(version.latestEvaluation), evaluationId: version.latestEvaluation?.evaluationId ?? null, reason: version.latestEvaluation ? null : "no_eligible_evaluation" },
});

/**
 * 요청 하나를 처리한다. 평가 API가 아니면 undefined(호출자 경로표로 넘김).
 * options: { up, catalog, posts: { compare?, evaluationSet? }, requests: [] } — posts 함수가 없으면 POST는 503.
 */
export const evaluationResponse = (sample, options, method, target, body) => {
  const path = target.pathname;
  const evaluationPath = path.startsWith("/api/comparisons") || path.startsWith("/api/evaluation-sets") || (method === "POST" && path === "/api/jobs" && body?.kind === "compare");
  if (!evaluationPath) return undefined;
  options.requests?.push({ method, path, body });
  if (!options.up) return json({ code: "service_unavailable" }, 503);
  if (method === "POST" && path === "/api/jobs") return options.posts?.compare ? options.posts.compare(body) : json({ code: "service_unavailable" }, 503);
  if (method === "POST" && path === "/api/evaluation-sets") return options.posts?.evaluationSet ? options.posts.evaluationSet(body) : json({ code: "service_unavailable" }, 503);
  if (path === "/api/comparisons") {
    const tab = target.searchParams.get("scope") === "current" ? "current" : "all";
    return json(sample.comparisonPage(tab, target.searchParams.get("cursor")));
  }
  if (path.startsWith("/api/comparisons/")) {
    const id = decodeURIComponent(path.slice("/api/comparisons/".length));
    if (!UUID.test(id)) return json({ code: "invalid_comparison_id" }, 400);
    const detail = sample.comparisonDetail(id) ?? sample.comparisonDetailByJob?.(id);
    return detail ? json(detail) : json({ code: "comparison_not_found" }, 404);
  }
  if (path === "/api/evaluation-sets") return json({ items: sample.evaluationSets, pagination: { limit: 100, hasMore: false, nextCursor: null } });
  if (path === "/api/evaluation-sets/snapshots") return json({ items: sample.snapshots });
  const set = sample.evaluationSets.find((item) => item.id === decodeURIComponent(path.slice("/api/evaluation-sets/".length)));
  return set ? json(set) : json({ code: "evaluation_set_not_found" }, 404);
};
