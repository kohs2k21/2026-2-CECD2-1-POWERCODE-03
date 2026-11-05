import assert from "node:assert/strict";
// 이상 분석 데이터 규칙: 주소 쿼리 ↔ 목록 조건 ↔ API 쿼리, 응답 파서, evidence v3/v4 읽기(대체), 결과 문구, 페이지 창, 판정 기록, 사이드바 링크.
await import("react-hot-toast"); // window가 생기기 전에 불러온다(goober 문서 접근 방지, 다른 시험과 같은 순서).
globalThis.window = { location: { origin: "http://local.test" }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const api = await import("../src/features/analysis/data/analysisApi.ts");
const { readEvidence, readEvidenceV4 } = await import("../src/features/analysis/data/evidence.ts");
const labels = await import("../src/features/analysis/labels.ts");
const { pageWindow } = await import("../src/features/analysis/components/DecisionListView.tsx");
const { timelineEntries } = await import("../src/features/analysis/components/DecisionTimeline.tsx");
const { sidebarGroups } = await import("../src/features/analysis/AnalysisPage.tsx");
const { HttpError } = await import("../src/services/api/client.ts");
const HttpErrorClass = HttpError;

// 1) 주소 쿼리 → 목록 조건: 기본값, 정상 값, 잘못된 값(기본값 + invalid)
assert.deepEqual(api.parseListSearch(""), { params: api.defaultListParams, invalid: false });
assert.equal(api.defaultListParams.category, "risk_all", "default = wireframe risk view 전체");
assert.equal(api.parseListSearch("?category=all").params.category, "all", "explicit result-view 전체 keeps working");
assert.deepEqual(api.parseListSearch("?signal=yes"), { params: { ...api.defaultListParams, signal: "yes" }, invalid: false }, "old signal=yes URLs keep working");
assert.equal(api.listApiParams({ ...api.defaultListParams, signal: "yes" }).signal, "yes");
assert.equal(labels.signalFilterLabels.yes, "이상 신호 있음");
// 이상 규칙 4개 이름과 결과 코드 표시(detection-decision.md 4.3)
assert.equal(labels.methodLabel("hard:success-with-error"), "이상 규칙(성공 상태이나 오류 건수가 기록됨)");
assert.equal(labels.methodLabel("hard:count-conservation"), "이상 규칙(건수 합계 불일치)");
assert.deepEqual(["PROCESS_STATUS_H", "PROCESS_STATUS_Z", "HARD_SUCCESS_WITH_ERROR", "HARD_COUNT_CONSERVATION", "OTHER_CODE"].map(labels.resultCodeLabel),
  ["상태 H 부분성공", "상태 Z 데이터없음", "성공 상태이나 오류 건수가 기록됨", "건수 합계 불일치", "OTHER_CODE"]);
assert.equal(api.parseListSearch("?category=bogus").params.category, "risk_all", "invalid category falls back to the default");
assert.equal(api.listSearch({ ...api.defaultListParams, category: "all" }), "?category=all", "result-view 전체 stays explicit in the URL");
const full = api.parseListSearch("?category=anomaly&q=TX-1&signal=anomaly_sign&completeness=partial&sort=oldest&page=3&pageSize=20&tab=x");
assert.deepEqual(full, {
  params: { category: "anomaly", q: "TX-1", signal: "anomaly_sign", completeness: "partial", sort: "oldest", page: 3, pageSize: 20 },
  invalid: false,
});
for (const category of ["anomaly_sign", "risk_all", "risk_critical", "risk_warning", "risk_info", "risk_unrated", "workflow_open", "workflow_resolved"])
  assert.equal(api.parseListSearch(`?category=${category}`).params.category, category, category);
for (const bad of ["?category=risk", "?category=risk:all", "?signal=sign", "?completeness=done", "?sort=severity_desc", "?page=0", "?page=x", "?pageSize=50", `?q=${"a".repeat(101)}`]) {
  const parsed = api.parseListSearch(bad);
  assert.equal(parsed.invalid, true, bad);
}
assert.equal(api.parseListSearch("?pageSize=50").params.pageSize, 10, "invalid page size falls back to 10");
// 목록 조건 → 주소: 기본값 생략, 다른 쿼리 보존, 검색어 앞뒤 공백 제거
assert.equal(api.listSearch(api.defaultListParams), "");
assert.equal(api.listSearch({ ...api.defaultListParams, q: "  tx  ", page: 2 }, "?keep=1&page=9"), "?keep=1&page=2&q=tx");
assert.deepEqual(api.parseListSearch(api.listSearch(full.params)).params, full.params, "round trip");
// API 쿼리: 'all'·빈 검색·기본 정렬·1쪽은 보내지 않음, 페이지 크기는 항상
assert.deepEqual(api.listApiParams(api.defaultListParams), {
  category: "risk_all", q: undefined, signal: undefined, completeness: undefined, sort: undefined, page: undefined, pageSize: 10,
});
assert.equal(api.listApiParams({ ...api.defaultListParams, category: "all" }).category, undefined, "API default is all");
assert.deepEqual(api.listApiParams(full.params), { category: "anomaly", q: "TX-1", signal: "anomaly_sign", completeness: "partial", sort: "oldest", page: 3, pageSize: 20 });

// 2) 응답 파서: 명시 필드만, 잘못된 값 버림, 알 수 없는 분류·단계는 응답 오류
const raw = {
  id: "d-1", kind: "final", stage: "replaced", signal: "no", decisionId: "d-1", jobId: "j-1", jobStatus: null, sourceId: "s-1",
  processObservationId: "o-1", configVersionId: "c-1", status: "complete", ruleStatus: "succeeded", mlStatus: "not_ready",
  decisionCode: null, anomalyScore: "0.42", evidence: { schemaVersion: 3, anomalous: false }, occurredAt: "2026-11-03T01:02:03.000000Z",
  decidedAt: "2026-11-03T01:02:03.000000Z", transactionId: "TX-1", processId: "P01", interfaceId: "IF-1", channelId: "CH", processStatus: "S",
  responseCode: "0000", body: "never copied", grade: null, riskStatus: "not_applicable", riskLevel: null, workflowState: "open", unread: true,
};
const item = api.parseItem(raw);
assert.equal("body" in item, false, "unknown fields are not copied");
assert.deepEqual([item.grade, item.riskStatus, item.riskLevel, item.workflowState, item.unread], [null, "not_applicable", null, "open", true]);
assert.deepEqual((({ grade, riskStatus, riskLevel, workflowState, unread }) => [grade, riskStatus, riskLevel, workflowState, unread])(
  api.parseItem({ ...raw, grade: "danger", riskStatus: "high", riskLevel: "severe", workflowState: "closed", unread: "yes" })),
[null, null, null, "detected", null], "unknown grade/risk/workflow values are dropped (no invented level)");
assert.deepEqual([item.kind, item.stage, item.signal, item.status, item.anomalyScore], ["final", "replaced", "no", "complete", "0.42"]);
assert.equal(api.parseItem({ ...raw, status: "done", occurredAt: "not a time" }).status, null);
assert.equal(api.parseItem({ ...raw, occurredAt: "not a time" }).occurredAt, null);
assert.throws(() => api.parseItem({ ...raw, kind: "window" }), /판정 응답을 확인할 수 없습니다/);
const listBody = { observedAt: "2026-11-03T00:00:00Z", counts: { all: 3, anomaly: 1, in_progress: 0, undetermined: 1, failed_replaced: 1 },
  total: 3, page: 1, pageSize: 10, latestDecidedAt: null, latestEventId: "42", items: [raw] };
const list = api.parseList(listBody);
assert.deepEqual([list.total, list.counts.anomaly, list.latestEventId, list.items.length], [3, 1, "42", 1]);
assert.equal(api.parseList({ ...listBody, latestEventId: "-1" }).latestEventId, null, "bad SSE cursor is ignored");
assert.equal(api.parseList({ ...listBody, counts: { all: -1 } }).counts.all, null);
assert.throws(() => api.parseList({ items: [] }), /판정 목록 응답/);
// 상세: 요청 ID와 다른 판정은 거절, 작업 ID로 요청해 그 작업의 최종 판정이 온 경우는 허용
const detailBody = { ...raw, configuration: { id: "c-1", createdAt: null, detectionMode: "parallel", ruleCount: 2, models: [{ modelKey: "iforest", adapterName: "sklearn-iforest" }, { bad: 1 }] },
  job: { id: "j-1", status: "succeeded", attemptCount: 2, maxAttempts: 3, errorCode: null, createdAt: null, finishedAt: null }, explanation: null };
const detail = api.parseDetail(detailBody, "d-1");
assert.deepEqual(detail.configuration.models, [{ modelKey: "iforest", adapterName: "sklearn-iforest" }]);
assert.deepEqual(detail.workflow, { state: "open", changedAt: null }, "workflow falls back to the item state");
assert.deepEqual(api.parseDetail({ ...detailBody, workflow: { state: "resolved", changedAt: "2026-11-03T02:00:00Z" } }, "d-1").workflow,
  { state: "resolved", changedAt: "2026-11-03T02:00:00Z" });
assert.equal(api.parseDetail(detailBody, "j-1").id, "d-1");
// v04 판정(구성 없음): 구성 값이 비어도 판정 항목·상세로 받는다.
const withoutConfig = api.parseDetail({ ...detailBody, configVersionId: null, configuration: null }, "d-1");
assert.deepEqual([withoutConfig.configVersionId, withoutConfig.configuration, withoutConfig.job.id], [null, null, "j-1"]);
assert.throws(() => api.parseDetail(detailBody, "other"), /판정 상세 응답/);
const history = api.parseInterims({ id: "d-1", jobId: "j-1", items: [
  { id: "i-1", kind: "first_anomaly", attemptNo: 1, seq: 1, evidenceVersion: "detector-evidence-v4", supersededByAttemptNo: null, reasonCode: null,
    createdAt: "2026-11-03T01:00:00Z", payload: { schemaVersion: 4, composite: { anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:elapsed_ms-3f9a2c1d"] } } },
  { id: "i-2", kind: "superseded", attemptNo: 1, seq: 2, supersededByAttemptNo: 2, createdAt: "2026-11-03T01:00:01Z", payload: {} },
  { id: "i-x", kind: "made_up" },
] }, "d-1");
assert.deepEqual(history.items.map((r) => r.kind), ["first_anomaly", "superseded"], "unknown interim kinds are dropped");
// UUID 대소문자: 요청 ID가 대문자여도 같은 판정이면 허용, 다른 판정(또는 다른 작업)의 기록·상세는 거절
const uuid = "8e6b9bf9-c013-4123-8656-ed29a1c383c9", job = "60e86bba-bd00-4ed2-b10a-f409d0fa5b37";
assert.equal(api.parseDetail({ ...detailBody, id: uuid, decisionId: uuid, jobId: job }, uuid.toUpperCase()).id, uuid);
assert.equal(api.parseDetail({ ...detailBody, id: uuid, decisionId: uuid, jobId: job }, job.toUpperCase()).id, uuid, "upper-case job id resolves");
assert.equal(api.parseInterims({ id: uuid, jobId: job, items: [] }, uuid.toUpperCase()).jobId, job);
assert.equal(api.parseInterims({ id: uuid, jobId: job, items: [] }, job.toUpperCase()).id, uuid, "job id request answered by its decision");
assert.throws(() => api.parseInterims({ id: uuid, jobId: job, items: [] }, "11111111-1111-4111-8111-111111111111"), /판정 기록 응답/);
assert.throws(() => api.parseInterims({ id: uuid, jobId: null, items: [] }, job), /판정 기록 응답/);

// 3) evidence v3 읽기
const v3 = readEvidence({
  schemaVersion: 3, anomalous: true, requestedMode: "parallel", actualPaths: ["rule", 7],
  rules: [{ ruleKey: "process-status", version: "process-status-hr-v1", result: "hit", observedStatus: "F", severity: "critical" }, { reason: "rules_unavailable" }, "x"],
  models: [{ modelKey: "iforest", anomalous: false }, { reason: "deadline_exceeded" }],
  checkers: { hr: { status: "succeeded", anomalous: true }, sr: "bad" },
  detectionPolicy: { mode: "parallel", deadlineMs: 3000, combination: "any_anomaly" },
  input: { mode: "csv", runId: "r", configurationScope: "operational" },
});
assert.deepEqual(v3.actualPaths, ["rule"]);
assert.equal(v3.rules.length, 2);
assert.deepEqual(v3.rules[0], { ruleKey: "process-status", version: "process-status-hr-v1", result: "hit", observedStatus: "F", severity: "critical", reason: null });
assert.equal(v3.rules[1].reason, "rules_unavailable");
assert.deepEqual(v3.models[1], { modelKey: null, anomalous: null, reason: "deadline_exceeded" });
assert.deepEqual(Object.keys(v3.checkers), ["hr"]);
assert.deepEqual(v3.policy, { mode: "parallel", combination: "any_anomaly" }, "deadline is a policy value and is not read for display");
assert.equal(v3.input.mode, "csv");
assert.equal(readEvidence(null).anomalous, null, "missing evidence reads as unknown, not normal");
assert.equal(readEvidence({ rules: [{ ruleKey: "soft-rule", version: 12, result: "not_applicable" }] }).rules[0].version, "12");

// 4) evidence v4 읽기: v3·composite 없음 → null(대체), 잘못된 방법 항목 버림
assert.equal(readEvidenceV4({ schemaVersion: 3, anomalous: true }), null);
assert.equal(readEvidenceV4({ schemaVersion: 4 }), null);
assert.equal(readEvidenceV4(null), null);
const v4 = readEvidenceV4({
  schemaVersion: 4, ruleSetRevision: 12, elapsedMs: 9.4,
  methodResults: [
    { methodId: "hard:process-status", category: "hard", status: "succeeded", anomalous: false, riskLevel: null },
    { methodId: "soft:elapsed_ms-3f9a2c1d", category: "soft", status: "succeeded", anomalous: true, severity: "warning", riskLevel: "warning",
      condition: { field: "elapsed_ms", operator: "gt", threshold: "5000", unit: "ms" }, observedValue: "8120" },
    { methodId: "ml:iforest", category: "hard", status: "succeeded" },
    { methodId: "sr:r01", category: "sr" },
    { methodId: "ml:iforest", category: "ml", status: "running", score: 0.53 },
  ],
  categoryResults: { hard: { n: 1, k: 1, result: "normal", status: "succeeded", anomalous: false }, soft: "bad" },
  composite: { status: "complete", anomalous: true, grade: "anomaly_sign", basisMethods: ["soft:elapsed_ms-3f9a2c1d"],
    risk: { status: "rated", level: "warning", determinedBy: ["soft:elapsed_ms-3f9a2c1d"], unratedMethods: [] } },
});
assert.deepEqual(v4.methodResults.map((m) => m.methodId), ["hard:process-status", "soft:elapsed_ms-3f9a2c1d", "ml:iforest"], "category must match the method id prefix");
assert.equal(v4.methodResults[2].status, null, "unknown method status is dropped");
assert.equal(v4.methodResults[2].score, 0.53);
assert.deepEqual(v4.methodResults[1].condition, { field: "elapsed_ms", operator: "gt", threshold: "5000", unit: "ms" });
assert.deepEqual([v4.grade, v4.compositeStatus, v4.anomalous, v4.ruleSetRevision, v4.elapsedMs], ["anomaly_sign", "complete", true, 12, 9.4]);
assert.deepEqual(Object.keys(v4.categoryResults), ["hard"]);
assert.equal(v4.risk.level, "warning");
assert.equal(readEvidenceV4({ schemaVersion: 4, composite: { grade: "danger" } }).grade, null, "unknown grade is not shown");
const v4ml = readEvidenceV4({ schemaVersion: 4, composite: { grade: "anomaly_sign", risk: { status: "unrated", level: null, levelTable: "method-defined-v2", unratedMethods: ["ml:iforest"] } },
  methodResults: [{ methodId: "ml:iforest", category: "ml", status: "succeeded", anomalous: true, riskLevel: null, riskLevelReason: "level_undefined", score: 0.71,
    scoreDetail: { raw: 0.71, threshold: "0.61", direction: "higher_is_anomaly", normalized: { kind: "threshold_ratio", value: 1.164 } } },
  { methodId: "hard:negative-duration", category: "hard", status: "succeeded", anomalous: false, evidence: [{ reason: "end_time_absent" }, "x"] }] });
assert.deepEqual(v4ml.methodResults[0].scoreDetail, { raw: 0.71, threshold: "0.61", ratio: 1.164 });
assert.equal(v4ml.methodResults[0].riskLevelReason, "level_undefined");
assert.deepEqual(v4ml.methodResults[1].reasons, ["end_time_absent"]);
assert.deepEqual([v4ml.risk.status, v4ml.risk.levelTable, v4ml.risk.unratedMethods], ["unrated", "method-defined-v2", ["ml:iforest"]]);
assert.equal(readEvidenceV4({ schemaVersion: 4, composite: {}, methodResults: [{ methodId: "ml:a", category: "ml",
  scoreDetail: { raw: 1, threshold: 2, normalized: { kind: "other", value: 9 } } }] }).methodResults[0].scoreDetail.ratio, null, "only threshold_ratio is shown");

// 5) 결과 문구: 이상 신호가 있으면 이상, 서버가 저장값으로 돌려준 등급 anomaly_sign만 이상 징후. 이상 신호가 아니면 등급을 쓰지 않음
const yes = { ...item, signal: "yes", grade: null, evidence: { schemaVersion: 3, anomalous: true } };
assert.equal(labels.resultText(yes), "이상");
assert.equal(labels.resultText({ ...yes, grade: "anomaly" }), "이상");
assert.equal(labels.resultText({ ...yes, grade: "anomaly_sign" }), "이상 징후");
assert.equal(labels.resultVariant({ ...yes, grade: "anomaly_sign" }), "warning");
assert.equal(labels.resultText({ ...yes, evidence: { schemaVersion: 4, composite: { grade: "anomaly_sign" } } }), "이상", "the label follows the server grade field");
assert.equal(labels.resultText({ ...item, signal: "unknown", grade: "anomaly" }), "정상 여부 미확인");
assert.equal(labels.resultText({ ...item, signal: "failed" }), "판정 실패");
assert.equal(labels.completenessText({ ...item, status: null, kind: "failed" }), "최종 없음");
assert.equal(labels.completenessText({ ...item, status: null, kind: "pending" }), "판정 중");
assert.equal(labels.completenessText({ ...item, status: "unavailable" }), "판정불가");
assert.equal(labels.stampText(null), "—");

// 설명 작업 진행 중 = queued·running만(blocked는 주기 조회·요청 중 표시 없음)
const { jobPending } = await import("../src/features/analysis/data/useAnalysis.ts");
assert.deepEqual(["queued", "running", "blocked", "succeeded", "failed"].map((status) => jobPending({ status })), [true, true, false, false, false]);
assert.equal(jobPending(undefined), false);

// 6) 페이지 창
assert.deepEqual(pageWindow(1, 1), [1]);
assert.deepEqual(pageWindow(3, 7), [1, 2, 3, 4, 5, 6, 7]);
assert.deepEqual(pageWindow(1, 20), [1, 2, null, 20]);
assert.deepEqual(pageWindow(10, 20), [1, null, 9, 10, 11, null, 20]);
assert.deepEqual(pageWindow(20, 20), [1, null, 19, 20]);
assert.deepEqual(pageWindow(3, 20), [1, 2, 3, 4, null, 20]);

// 7) 판정 기록: 중간 → 대체됨 → 최종 / 최종 없음 / 진행 중 / 기록 없는 실패
const finalDetail = { ...detail, kind: "final", status: "complete" };
assert.deepEqual(timelineEntries(finalDetail, history).map((e) => [e.tone, e.text]), [
  ["provisional", "시도 1 · 중간 판정 · 근거 이상 징후 규칙"],
  ["void", "시도 1 결과 · 대체됨"],
  ["final", "시도 2 · 최종 · 모든 방법 종료"],
]);
// 부분·판정불가 최종은 기한 도달·실패를 구분할 저장값이 없어 `최종`만(검사 결과는 요약에 있음)
assert.deepEqual(timelineEntries({ ...finalDetail, status: "partial" }, history).at(-1).text, "시도 2 · 최종");
const noFinal = api.parseInterims({ id: "j-2", jobId: "j-2", items: [
  { id: "a", kind: "first_anomaly", attemptNo: 1, seq: 1, payload: { schemaVersion: 3 } },
  { id: "b", kind: "no_final", attemptNo: 1, seq: 2, reasonCode: "detection_processing_failed", payload: {} },
] }, "j-2");
const failedDetail = { ...detail, id: "j-2", kind: "failed", status: null, job: { ...detail.job, status: "failed", attemptCount: 1, errorCode: "detection_processing_failed" } };
assert.deepEqual(timelineEntries(failedDetail, noFinal).map((e) => [e.text, e.title]),
  [["시도 1 · 중간 판정", undefined], ["시도 1 실패 · 최종 없음(판정 실패)", "detection_processing_failed"]], "wireframe wording, code as tooltip");
assert.deepEqual(timelineEntries(failedDetail, { id: "j-2", jobId: "j-2", items: [] }).map((e) => [e.text, e.title]),
  [["시도 1 실패 · 최종 없음(판정 실패)", "detection_processing_failed"]]);
assert.deepEqual(timelineEntries({ ...detail, kind: "pending", status: null, job: { ...detail.job, status: "queued", attemptCount: 0 } }, { id: "j", jobId: "j", items: [] })
  .map((e) => [e.tone, e.at, e.text]), [["progress", null, "남은 방법 결과 대기"]], "wireframe in-progress line for queued/running jobs");
assert.deepEqual(timelineEntries({ ...detail, kind: "pending", status: null, job: { ...detail.job, status: "blocked", attemptCount: 0 } }, { id: "j", jobId: "j", items: [] })
  .map((e) => e.text), [], "no in-progress line for other job states (wireframe has none)");
assert.deepEqual(timelineEntries({ ...detail, kind: "pending", status: null, job: null }, undefined).map((e) => e.text), []);
// 근거 추가: 같은 시도 직전 기록보다 늘어난 방법만(`근거 추가 · <방법>`), 알 수 없으면 `근거 추가`
const v4p = (basisMethods) => ({ schemaVersion: 4, composite: { anomalous: true, basisMethods } });
const added = api.parseInterims({ id: "j-3", jobId: "j-3", items: [
  { id: "a1", kind: "first_anomaly", attemptNo: 1, seq: 1, payload: v4p(["hard:process-status"]) },
  { id: "a2", kind: "evidence_added", attemptNo: 1, seq: 2, payload: v4p(["hard:process-status", "ml:iforest"]) },
  { id: "a3", kind: "evidence_added", attemptNo: 1, seq: 3, payload: { schemaVersion: 3 } },
] }, "j-3");
const interimDetail = { ...detail, id: "j-3", kind: "interim", status: null, job: { ...detail.job, status: "running", attemptCount: 1 } };
assert.deepEqual(timelineEntries(interimDetail, added).map((e) => e.text),
  ["시도 1 · 중간 판정 · 근거 이상 규칙(상태가 S가 아님)", "시도 1 · 근거 추가 · 머신러닝 모델(iforest)", "시도 1 · 근거 추가", "남은 방법 결과 대기"]);
assert.deepEqual(timelineEntries(interimDetail, added).map((e) => e.title), ["hard:process-status", "ml:iforest", undefined, undefined], "raw method ids only as tooltip");
// 방법 표시 이름(#92 C18·C22): 승인 문구가 있는 하드룰 2개만 이름, 그 밖의 하드룰은 대분류 이름만, 알 수 없는 접두는 생략
assert.deepEqual(["hard:process-status", "hard:negative-duration", "hard:other-rule", "soft:elapsed_ms-3f9a2c1d", "ml:iforest", "sr:r01", "ml:", "plain"].map(labels.methodLabel),
  ["이상 규칙(상태가 S가 아님)", "이상 규칙(시작 시각이 종료 시각보다 늦음)", "이상 규칙", "이상 징후 규칙", "머신러닝 모델(iforest)", null, null, null]);
assert.deepEqual(labels.methodLabels(["soft:a-1", "soft:b-2", "sr:r01", "ml:iforest"]), ["이상 징후 규칙", "머신러닝 모델(iforest)"], "duplicates merged, unknown dropped");
const unknownBasis = api.parseInterims({ id: "j-4", jobId: "j-4", items: [
  { id: "u1", kind: "first_anomaly", attemptNo: 1, seq: 1, payload: v4p(["sr:r01"]) },
] }, "j-4");
assert.deepEqual(timelineEntries({ ...interimDetail, id: "j-4" }, unknownBasis).map((e) => [e.text, e.title]),
  [["시도 1 · 중간 판정 · 근거", "sr:r01"], ["남은 방법 결과 대기", undefined]]);
// 시각 칸: 진행 중 줄만 `진행 중`, 그 밖에 시각이 없는 줄은 `—`
const { DecisionTimeline } = await import("../src/features/analysis/components/DecisionTimeline.tsx");
const { renderToStaticMarkup } = await import("react-dom/server");
const React = (await import("react")).default;
globalThis.React = React; // 컴포넌트가 JSX 런타임으로 전역 React를 쓴다(다른 시험과 같음)
const timelineHtml = renderToStaticMarkup(React.createElement(DecisionTimeline, { entries: [
  { key: "x", at: null, tone: "void", text: "시도 1 실패 · 최종 없음(판정 실패)", title: "detection_processing_failed" },
  { key: "progress", at: null, tone: "progress", text: "남은 방법 결과 대기" },
] }));
// 판정 상세 방법 표: 새 이상 규칙의 이름·조건·결과 코드 표시(코드 원문이 그대로 나오지 않는다).
const { MethodResults } = await import("../src/features/analysis/components/MethodResults.tsx");
const methodHtml = renderToStaticMarkup(React.createElement(MethodResults, { observedStatus: null, evidence: readEvidenceV4({ schemaVersion: 4, composite: {}, categoryResults: {}, methodResults: [
  { methodId: "hard:success-with-error", category: "hard", status: "succeeded", anomalous: true, code: "HARD_SUCCESS_WITH_ERROR", riskLevel: "critical" },
  { methodId: "hard:process-status", category: "hard", status: "succeeded", anomalous: true, code: "PROCESS_STATUS_Z", riskLevel: "warning" },
] }) }));
for (const shown of ["성공 상태이나 오류 건수가 기록됨", "STATUS = S, ERROR_COUNT &gt; 0", "상태 Z 데이터없음"]) assert.ok(methodHtml.includes(shown), shown);
assert.ok(!methodHtml.includes("HARD_SUCCESS_WITH_ERROR") && !methodHtml.includes("PROCESS_STATUS_Z"), "result codes use their labels");
// 위험도 단계 아이콘 색 = 위험도 배지 variant의 글자 색(위험 critical·주의 warning·참고 secondary). 목록 줄 아이콘도 같은 색.
const { riskLevelIconTone } = await import("../src/features/analysis/icons.ts");
assert.deepEqual(riskLevelIconTone, { critical: "text-destructive-text", warning: "text-warning-text", info: "text-secondary-foreground" });
const { DecisionRow } = await import("../src/features/analysis/components/DecisionRow.tsx");
const { MemoryRouter } = await import("react-router-dom");
const infoRowHtml = renderToStaticMarkup(React.createElement(MemoryRouter, null,
  React.createElement(DecisionRow, { item: { ...list.items[0], riskStatus: "rated", riskLevel: "info" }, href: "/analysis/d/x" })));
assert.ok(/<svg[^>]*class="tabler-icon tabler-icon-info-circle size-4 shrink-0 text-secondary-foreground"[^>]*aria-hidden="true"/.test(infoRowHtml)
  || /<svg[^>]*aria-hidden="true"[^>]*class="tabler-icon tabler-icon-info-circle size-4 shrink-0 text-secondary-foreground"/.test(infoRowHtml),
  "참고 row icon uses the secondary badge text colour");
assert.equal((timelineHtml.match(/진행 중/g) ?? []).length, 1, "only the progress entry says 진행 중");
assert.ok(timelineHtml.includes(">—<") && timelineHtml.includes('title="detection_processing_failed"'));
assert.deepEqual(timelineEntries({ ...finalDetail, job: null }, { id: "d", jobId: null, items: [] }).map((e) => e.text), ["최종 · 모든 방법 종료"]);

// 8) 사이드바: 판정 결과별 6분류 / 위험도별 5분류 + 분류함(보류·완료), 건수 그대로(없으면 표시 안 함), 다른 조건 유지·페이지 1·상세 열림 해제
const groups = sidebarGroups({ ...full.params }, list.counts, "result");
assert.deepEqual(groups.map((g) => [g.title, g.separated ?? false]), [["판정", false], ["분류함", true]]);
assert.deepEqual(groups[0].items.map((i) => i.label), ["전체", "이상", "이상 징후", "판정 중·중간 판정", "정상 여부 미확인", "판정 실패·대체"], "wireframe CATS order");
assert.deepEqual(groups[0].items.map((i) => i.count), [3, 1, undefined, 0, 1, 1], "a missing count is not shown as 0");
assert.deepEqual(groups[1].items.map((i) => [i.id, i.label]), [["workflow_open", "보류"], ["workflow_resolved", "완료"]]);
const riskGroups = sidebarGroups(api.defaultListParams, { ...list.counts, risk_all: 4, risk_critical: 1, risk_warning: 1, risk_info: 0, risk_unrated: 2 }, "risk");
assert.deepEqual(riskGroups[0].title, "위험도");
assert.deepEqual(riskGroups[0].items.map((i) => [i.label, i.count]), [["전체", 4], ["위험", 1], ["주의", 1], ["참고", 0], ["위험도 미정", 2]], "wireframe RISK_CATS");
assert.deepEqual(riskGroups[0].items.map((i) => i.muted ?? false), [false, false, false, false, true], "위험도 미정 is muted (wireframe)");
const unknownLink = new URL(groups[0].items[4].to, "http://local.test");
assert.equal(unknownLink.pathname, "/analysis");
assert.deepEqual([unknownLink.searchParams.get("category"), unknownLink.searchParams.get("q"), unknownLink.searchParams.get("page"), unknownLink.searchParams.get("sort")],
  ["undetermined", "TX-1", null, "oldest"]);
assert.equal(api.parseListSearch("?category=normal").invalid, true, "the removed normal category is normalized away");
assert.equal(labels.signalFilterLabels.no, "이상 신호 없음", "normal decisions stay reachable via the signal filter");
assert.equal(new URL(groups[0].items[0].to, "http://local.test").searchParams.get("category"), "all", "result-view 전체 link is explicit");
assert.equal(new URL(riskGroups[0].items[0].to, "http://local.test").search, "", "risk-view 전체 is the default address");
assert.deepEqual(sidebarGroups(api.defaultListParams, undefined, "result")[0].items.map((i) => i.count), [undefined, undefined, undefined, undefined, undefined, undefined]);
assert.deepEqual(labels.workflowActions.detected.map(([to, label]) => [to, label]), [["open", "처리 보류"], ["resolved", "처리 완료"]], "wireframe dWorkflow");
assert.deepEqual(labels.workflowActions.open.map(([to, label]) => [to, label]), [["detected", "원래 상태로 복원"], ["resolved", "처리 완료"]]);
assert.deepEqual(labels.workflowActions.resolved.map(([to, label]) => [to, label]), [["detected", "원래 상태로 복원"]]);
assert.equal(labels.legacyRuleLabel("process-status"), "이상 규칙(상태가 S가 아님)");
assert.equal(labels.legacyRuleLabel("fixture_rule"), "이상 징후 규칙(과거 등록 Rule)", "U6 past registered Rule display");

// 9) Process Flow 응답·트리: 요청 판정(또는 그 작업)의 흐름만, Message는 같은 Process ID의 첫 노드 아래, 부모 없는 Message는 같은 단계 끝
const { flowNodes } = await import("../src/features/analysis/components/ProcessFlowCard.tsx");
const flowBody = { id: "d-1", jobId: "j-1", truncated: false, counts: { processes: 2, messages: 2, errors: 1 },
  transaction: { transactionId: "TX-1", interfaceId: "IF-1", channelId: null, status: "S", responseCode: null, processTimeMs: "41", processCount: null, body: "x" },
  processes: [{ observationId: "p1", processId: "P01", dependsOn: null, status: "F", responseCode: "E500", totalCount: null, focus: true },
    { observationId: "p2", processId: "P99", dependsOn: "P01", status: "S", responseCode: "0000", totalCount: "5", focus: false }, { bad: 1 }],
  messages: [{ observationId: "m1", messageId: "M-REQ", processId: "P01", direction: "REQ", status: "S", dataSize: "512", messageBody: "never" },
    { observationId: "m2", messageId: "M-ORPHAN", processId: "P77", direction: "RES", status: "F", dataSize: null }] };
const flow = api.parseFlow(flowBody, "d-1");
assert.equal(api.parseFlow(flowBody, "j-1").id, "d-1", "job id request answered by its decision");
assert.throws(() => api.parseFlow(flowBody, "other"), /Process Flow 응답/);
assert.equal(flow.processes.length, 2);
assert.deepEqual(flow.counts, { processes: 2, messages: 2, errors: 1 });
assert.equal(api.parseFlow({ ...flowBody, counts: { processes: -1, messages: 2, errors: 0 } }, "d-1").counts, null, "malformed counts are dropped");
assert.ok(!("body" in flow.transaction) && !("messageBody" in flow.messages[0]), "only listed fields are copied");
assert.deepEqual(flowNodes(flow).map((n) => [n.kind, n.level, n.key]), [["tx", 0, "tx"], ["process", 1, "p1"], ["message", 2, "m1"], ["process", 1, "p2"], ["message", 1, "m2"]]);
assert.deepEqual(flowNodes({ ...flow, transaction: null }).map((n) => n.kind)[0], "process", "no transaction node without a stored transaction");
// 조사·조치 기록 파서·쓰기 오류 제목
assert.deepEqual(api.parseFeedbackList({ id: "d-1", items: [{ id: "f1", kind: "action", reason: "확인", actorId: "1", actorEmail: "a@example.test", createdAt: "2026-11-03T00:00:00Z" },
  { id: "f2", kind: "label", reason: "x" }] }).items.map((i) => i.id), ["f1"], "unknown feedback kinds dropped");
assert.equal(api.analysisWriteErrorTitle(new HttpErrorClass("x", 409, "workflow_state_conflict"), "업무 분류"), "업무 분류 실패 · workflow_state_conflict");
assert.deepEqual(labels.feedbackKindLabels, { action: "조치 기록", "suspected-false-positive": "오탐 의심", "insufficient-evidence": "근거 부족" });
// 오류 제목
assert.equal(api.analysisErrorTitle(new HttpError("x", 404, "decision_not_found"), "판정 상세"), "판정 상세 · decision_not_found", "missing decision shows the error code as is");
assert.equal(api.analysisErrorTitle(new HttpError("x", 503, "analysis_storage_unavailable"), "판정 목록"), "판정 목록 미연결");
assert.equal(api.analysisErrorTitle(new HttpError("bad", 400, "invalid_q"), "판정 목록"), "bad");
console.log("analysis data QA passed: URL/query mapping, response parsers, evidence v3 and v4 reader fallback, result labels, page window, timeline, "
  + "result/risk/workflow sidebar, workflow actions, flow parser and tree, feedback parser, error titles");
