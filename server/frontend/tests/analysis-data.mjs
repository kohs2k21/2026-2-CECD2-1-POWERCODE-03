import assert from "node:assert/strict";
// 이상 분석 데이터 규칙: 주소 쿼리 ↔ 목록 조건 ↔ API 쿼리, 응답 파서, evidence v3/v4 읽기(대체), 결과 문구, 페이지 창, 판정 기록, 사이드바 링크.
await import("react-hot-toast"); // window가 생기기 전에 불러온다(goober 문서 접근 방지, 다른 시험과 같은 순서).
globalThis.window = { location: { origin: "http://local.test" }, localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const api = await import("../src/features/analysis/data/analysisApi.ts");
const { readEvidence, readEvidenceV4 } = await import("../src/features/analysis/data/evidence.ts");
const labels = await import("../src/features/analysis/labels.ts");
const { pageWindow } = await import("../src/features/analysis/components/DecisionListView.tsx");
const { mergeTimeline, recordEntries, timelineEntries } = await import("../src/features/analysis/components/DecisionTimeline.tsx");
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
// Detector v3 근거 구분(relation-rules.md 1·5절, protocol-rules.md 5.2): 관계 규칙 위반 = 규약 위반, 프로토콜·거래 실패는 기존 이름, 수집 미완료 보류 = 관계 확인 보류.
// 방법 행은 구분 순(내장 → 관계 → 프로토콜 → 이상 징후 → 머신러닝)으로 묶이고, 내부 id·methodVersion·T 코드 원문은 보이지 않는다.
const v3Evidence = readEvidenceV4({ schemaVersion: 4, composite: { grade: "anomaly", basisMethods: ["hard:rel-3f9a2c1d", "ml:iforest"] }, categoryResults: {}, methodResults: [
  { methodId: "ml:iforest", category: "ml", status: "succeeded", anomalous: false },
  { methodId: "hard:rel-3f9a2c1d", category: "hard", methodVersion: "relation-precedes-v1", status: "succeeded", anomalous: true, code: "REL_PRECEDES", riskLevel: "critical",
    evidence: [{ result: "hit", violation: "other_predecessor", node: "process:TX:IF_A_PR002", edge: null }] },
  { methodId: "hard:proto-5c1e9a07", category: "hard", methodVersion: "protocol-path-v1", status: "succeeded", anomalous: true, code: "PROTO_PATH_UNMATCHED", riskLevel: "critical", evidence: [{ interfaceMatched: true, routes: [] }] },
  { methodId: "hard:rel-0a1b2c3d", category: "hard", methodVersion: "relation-message_handoff-v1", status: "not_ready", anomalous: null, cause: "capacity", evidence: [{ reason: "graph_truncated" }] },
  { methodId: "hard:transaction-failed", category: "hard", methodVersion: "transaction-failed-v1", status: "succeeded", anomalous: true, code: "HARD_TRANSACTION_FAILED", riskLevel: "warning",
    evidence: [{ result: "hit", transactionObservationId: "t-1", responseCode: "E901" }] },
], relationNotes: [
  { ruleId: "hard:rel-9e8d7c6b", kind: "precedes", result: "held", reason: "collection_incomplete", waitingFor: { processId: "IF_A_PR001" } },
  { ruleId: "hard:transaction-failed", kind: "transaction", result: "held", reason: "transaction_pending", waitingFor: { layer: "T" } },
  { ruleId: null, kind: "required_step", result: "deferred_to_transaction", reason: null, waitingFor: { layer: "T" } },
] });
const v3Html = renderToStaticMarkup(React.createElement(MethodResults, { observedStatus: null, evidence: v3Evidence }));
assert.deepEqual([...v3Html.matchAll(/data-group="([a-z]+)"/g)].map((m) => m[1]), ["hard", "relation", "relation", "protocol", "ml"], "method rows grouped by evidence kind");
for (const shown of ["관계 규칙(선행 관계)", "규약 위반", "다른 선행 단계", "관계 규칙(메시지 인계)", "capacity · graph_truncated", "정상 프로토콜", "처음 보는 경로",
  "이상 규칙(거래 실패)", "응답 코드 E901", "관계 확인 보류", "선행 단계 로그 없음 · IF_A_PR001", "거래 행 없음"]) assert.ok(v3Html.includes(shown), shown);
assert.equal((v3Html.match(/data-held=/g) ?? []).length, 2, "only held notes are listed under 관계 확인 보류");
assert.ok(!/>(REL_PRECEDES|PROTO_PATH_UNMATCHED|HARD_TRANSACTION_FAILED|relation-precedes-v1|protocol-path-v1|hard:rel-3f9a2c1d)</.test(v3Html), "raw ids/codes stay in tooltips");
const v3Row = (id) => v3Html.match(new RegExp(`<tr[^>]*data-method="${id}"[^>]*>.*?</tr>`))?.[0] ?? "";
assert.ok(v3Row("hard:rel-3f9a2c1d").includes(">규약 위반<") && !v3Row("hard:rel-3f9a2c1d").includes(">이상<"), "relation violation says 규약 위반");
assert.ok(v3Row("hard:proto-5c1e9a07").includes(">이상<") && !v3Row("hard:proto-5c1e9a07").includes("규약 위반"), "protocol is not labelled 규약 위반 (hard:rel-* only)");
assert.equal(labels.resultCodeLabel("PROTO_PATH_UNMATCHED"), "정상 프로토콜 확인 · 처음 보는 경로", "same prefix as the protocol alert title");
assert.deepEqual(["REL_PRECEDES", "REL_REQUIRED_STEP", "PROTO_INTERFACE_UNREGISTERED", "HARD_TRANSACTION_FAILED"].map(labels.resultCodeLabel),
  ["규약 위반 · 선행 관계", "규약 위반 · 필수 단계", "정상 프로토콜 확인 · 등록되지 않은 인터페이스", "거래 실패"], "list cause line uses the same kind names");
assert.deepEqual(["hard:rel-3f9a2c1d", "hard:proto-5c1e9a07", "hard:rel-XYZ", "hard:unregistered-interface"].map(labels.methodLabel),
  ["관계 규칙", "정상 프로토콜", "이상 규칙", "이상 규칙(등록되지 않은 인터페이스)"]);
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

// 8) 사이드바: 판정 결과별 7분류(정상 포함, 사용자 11/13) / 위험도별 5분류 + 분류함(보류·완료), 건수 그대로(없으면 표시 안 함), 다른 조건 유지·페이지 1·상세 열림 해제
const groups = sidebarGroups({ ...full.params }, list.counts, "result");
assert.deepEqual(groups.map((g) => [g.title, g.separated ?? false]), [["판정", false], ["분류함", true]]);
assert.deepEqual(groups[0].items.map((i) => i.label), ["전체", "이상", "이상 징후", "정상", "판정 중·중간 판정", "정상 여부 미확인", "판정 실패·대체"], "wireframe CATS order + normal after anomaly sign");
assert.deepEqual(groups[0].items.map((i) => i.count), [3, 1, undefined, undefined, 0, 1, 1], "a missing count is not shown as 0");
assert.deepEqual(groups[1].items.map((i) => [i.id, i.label]), [["workflow_open", "보류"], ["workflow_resolved", "완료"]]);
const riskGroups = sidebarGroups(api.defaultListParams, { ...list.counts, risk_all: 4, risk_critical: 1, risk_warning: 1, risk_info: 0, risk_unrated: 2 }, "risk");
assert.deepEqual(riskGroups[0].title, "위험도");
assert.deepEqual(riskGroups[0].items.map((i) => [i.label, i.count]), [["전체", 4], ["위험", 1], ["주의", 1], ["참고", 0], ["위험도 미정", 2]], "wireframe RISK_CATS");
assert.deepEqual(riskGroups[0].items.map((i) => i.muted ?? false), [false, false, false, false, true], "위험도 미정 is muted (wireframe)");
const unknownLink = new URL(groups[0].items[5].to, "http://local.test");
assert.equal(unknownLink.pathname, "/analysis");
assert.deepEqual([unknownLink.searchParams.get("category"), unknownLink.searchParams.get("q"), unknownLink.searchParams.get("page"), unknownLink.searchParams.get("sort")],
  ["undetermined", "TX-1", null, "oldest"]);
assert.deepEqual([api.parseListSearch("?category=normal").invalid, api.parseListSearch("?category=normal").params.category], [false, "normal"], "normal category address (user 11/13)");
assert.equal(labels.signalFilterLabels.no, "이상 신호 없음", "normal decisions stay reachable via the signal filter");
assert.equal(new URL(groups[0].items[0].to, "http://local.test").searchParams.get("category"), "all", "result-view 전체 link is explicit");
assert.equal(new URL(riskGroups[0].items[0].to, "http://local.test").search, "", "risk-view 전체 is the default address");
assert.deepEqual(sidebarGroups(api.defaultListParams, undefined, "result")[0].items.map((i) => i.count), [undefined, undefined, undefined, undefined, undefined, undefined, undefined]);
assert.deepEqual(labels.workflowActions.detected.map(([to, label]) => [to, label]), [["open", "처리 보류"], ["resolved", "처리 완료"]], "wireframe dWorkflow");
assert.deepEqual(labels.workflowActions.open.map(([to, label]) => [to, label]), [["detected", "원래 상태로 복원"], ["resolved", "처리 완료"]]);
assert.deepEqual(labels.workflowActions.resolved.map(([to, label]) => [to, label]), [["detected", "원래 상태로 복원"]]);
assert.equal(labels.legacyRuleLabel("process-status"), "이상 규칙(상태가 S가 아님)");
assert.equal(labels.legacyRuleLabel("fixture_rule"), "이상 징후 규칙(과거 등록 Rule)", "U6 past registered Rule display");

// 9) Process Flow 응답·트리: 요청 판정(또는 그 작업)의 흐름만, Message는 같은 Process ID의 첫 노드 아래, 부모 없는 Message는 같은 단계 끝
const { flowNodes, flowTree, fieldsOf, ambiguousRowsOf, focusToneOf, gradeTone, attemptTonesOf, processToneOf } = await import("../src/features/analysis/components/ProcessFlowCard.tsx");
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
// 위→아래 트리: Transaction 아래 Process·부모 없는 Message, Process 아래 그 Message(flowNodes 순서 유지)
const shape = (items) => items.map((i) => [i.node.key, shape(i.children)]);
assert.deepEqual(shape(flowTree(flow)), [["tx", [["p1", [["m1", []]]], ["p2", []], ["m2", []]]]]);
assert.deepEqual(shape(flowTree({ ...flow, transaction: null })), [["p1", [["m1", []]]], ["p2", []], ["m2", []]], "no transaction: processes are the top row");
// 선택 노드 원본 정보: 모호 PROCESS(그래프 verification = ambiguous)는 시도 하나의 상태를 고르지 않는다
const statusRow = (node) => fieldsOf(node, flow).find(([field]) => field === "STATUS")[1];
assert.equal(statusRow({ key: "p1", kind: "process", level: 1, process: flow.processes[0] }), "F · Code E500");
assert.equal(statusRow({ key: "p1", kind: "process", level: 1, process: flow.processes[0], ambiguous: true }), "— 확인 불가", "ambiguous process STATUS = UNAVAILABLE");
// 트리 연결 모호: 그래프 모호 PROCESS 묶음의 시도 관측(행)마다 배지, 선택 표 STATUS는 행 자신의 값(그래프 병합 노드만 확인 불가)
const ambiguousGraph = { nodes: [
  { id: "process:TX-1:P01", kind: "process", attrs: { verification: "ambiguous", attempts: [{ observationId: "p1" }, { observationId: "p1b" }, { bad: 1 }] } },
  { id: "process:TX-1:P99", kind: "process", attrs: { verification: null, attempts: [{ observationId: "p2" }] } },
  { id: "transaction:TX-1", kind: "transaction", attrs: { verification: "ambiguous", attempts: [{ observationId: "tx" }] } },
] };
const rows = ambiguousRowsOf(ambiguousGraph);
assert.deepEqual([...rows].sort(), ["p1", "p1b"], "only attempts of ambiguous process nodes");
assert.deepEqual(ambiguousRowsOf(undefined).size, 0, "no graph (not loaded or failed) = no badge");
const marked = flowNodes(flow, rows).filter((n) => n.kind === "process").map((n) => [n.key, n.ambiguousRow === true, n.ambiguous === true]);
assert.deepEqual(marked, [["p1", true, false], ["p2", false, false]], "tree keeps one row per observation, badge only");
assert.equal(statusRow(flowNodes(flow, rows).find((n) => n.key === "p1")), "F · Code E500", "ambiguous tree row keeps its own STATUS");
assert.equal(flowTree(flow, rows)[0].children[0].node.ambiguousRow, true, "tree items carry the mark");
// 노드 바탕 = 판정 결과(사용자 11/12): 판정 대상은 판정 요약 결과와 같은 규칙, 다른 PROCESS는 시도 등급 저장값만(null = 흰 바탕)
assert.deepEqual([{ signal: "yes", grade: null }, { signal: "yes", grade: "anomaly" }, { signal: "yes", grade: "anomaly_sign" }, { signal: "no", grade: "normal" },
  { signal: "pending", grade: null }, { signal: "unknown", grade: "anomaly" }, undefined].map(focusToneOf),
["anomaly", "anomaly", "anomaly_sign", null, null, null, null], "focus tone follows the summary result (v3 anomaly without grade = 이상)");
for (const value of [{ signal: "yes", grade: null }, { signal: "yes", grade: "anomaly_sign" }, { signal: "no", grade: null }])
  assert.equal(focusToneOf(value) === null ? "white" : focusToneOf(value) === "anomaly_sign" ? "이상 징후" : "이상",
    labels.resultText({ ...value }) === "이상 징후" ? "이상 징후" : labels.resultText({ ...value }) === "이상" ? "이상" : "white", "tone agrees with resultText");
assert.deepEqual(["anomaly", "anomaly_sign", "normal", "undetermined", null, "danger"].map(gradeTone), ["anomaly", "anomaly_sign", null, null, null, null]);
const toneGraph = { nodes: [
  { id: "process:TX-1:P01", kind: "process", attrs: { verification: null, attempts: [{ observationId: "p1", grade: null }, { observationId: "p1r", grade: "anomaly" }] } },
  { id: "process:TX-1:P02", kind: "process", attrs: { verification: "ambiguous", attempts: [{ observationId: "p2", grade: "anomaly_sign" }, { observationId: "p2b", grade: "anomaly_sign" }] } },
  { id: "transaction:TX-1", kind: "transaction", attrs: { attempts: [{ observationId: "tx", grade: "anomaly" }] } },
] };
assert.deepEqual([...attemptTonesOf(toneGraph)], [["p1", null], ["p1r", "anomaly"], ["p2", "anomaly_sign"], ["p2b", "anomaly_sign"]], "tree: per-observation grade, process nodes only");
assert.equal(attemptTonesOf(undefined).size, 0, "no graph = white for non-focus nodes");
assert.deepEqual(toneGraph.nodes.slice(0, 2).map((n) => processToneOf(n.attrs)), ["anomaly", null], "graph: representative (last) attempt; ambiguous = white");
assert.equal(processToneOf({ attempts: [] }), null);
assert.deepEqual([labels.flowLegendLabels.anomaly, labels.flowLegendLabels.anomalySign, labels.flowLegendLabels.focus, labels.flowLegendLabels.notLogged],
  ["이상", "이상 징후", "판정 대상", "로그 없음"]);
assert.ok(!("normal" in labels.flowLegendLabels), "no 일반 legend item");
// 트리·그래프 문구(contracts/process-graph.md 어휘): 판단 보류는 인계 undetermined에만, 모호 PROCESS는 연결 모호
assert.deepEqual(labels.handoffLabels, { match: "인계 일치", mismatch: "인계 불일치", undetermined: "판단 보류" });
assert.deepEqual([labels.flowViewLabels.tree, labels.flowViewLabels.graph, labels.flowViewGroupLabel], ["로그 구성", "실행 순서", "Process Flow 보기"]);
assert.deepEqual([labels.graphLabels.notLogged, labels.graphLabels.ambiguous, labels.graphLabels.noMessages], ["로그 없음", "연결 모호", "메시지 0건"]);
assert.notEqual(labels.graphLabels.ambiguous, labels.handoffLabels.undetermined, "ambiguous node badge does not reuse 판단 보류");
assert.deepEqual([labels.graphLabels.zoomIn, labels.graphLabels.zoomOut, labels.graphLabels.fitView], ["확대", "축소", "전체 보기"]);
// 조사·조치 기록 파서·쓰기 오류 제목
assert.deepEqual(api.parseFeedbackList({ id: "d-1", items: [{ id: "f1", kind: "action", reason: "확인", actorId: "1", actorEmail: "a@example.test", createdAt: "2026-11-03T00:00:00Z" },
  { id: "f2", kind: "label", reason: "x" }] }).items.map((i) => i.id), ["f1"], "unknown feedback kinds dropped");
assert.equal(api.analysisWriteErrorTitle(new HttpErrorClass("x", 409, "workflow_state_conflict"), "업무 분류"), "업무 분류 실패 · workflow_state_conflict");
assert.deepEqual(labels.feedbackKindLabels, { action: "조치 기록", "suspected-false-positive": "오탐 의심", "insufficient-evidence": "근거 부족" });
// 목록 이상 원인(사용자 11/13): 서버 causes 파서(대분류가 틀리면 버림) + 문구(기존 표시 이름) + 첫 원인 외 N건
const cause = (overrides) => ({ category: "hard", methodId: null, ruleKey: null, code: null, observedStatus: null, fieldName: null, operator: null, threshold: null,
  unit: null, observedValue: null, modelName: null, score: null, scoreThreshold: null, ...overrides });
const causeItem = api.parseItem({ ...raw, causes: [cause({ methodId: "hard:process-status", code: "PROCESS_STATUS_F", observedStatus: "F" }), { category: "window" }, "x",
  cause({ category: "ml", methodId: "ml:iforest", modelName: "Isolation Forest", score: "0.8" })] });
assert.equal(causeItem.causes.length, 2, "unknown categories dropped");
assert.equal(causeItem.causes[1].score, null, "non-number score dropped");
assert.deepEqual(api.parseItem(raw).causes, [], "missing causes = no cause line (detail responses)");
const said = (c, responseCode = null) => labels.causeText(cause(c), responseCode);
assert.equal(said({ methodId: "hard:process-status", code: "PROCESS_STATUS_F", observedStatus: "F" }, "E500"), "상태 F 실패 · 응답 코드 E500");
assert.equal(said({ methodId: "hard:process-status", observedStatus: "H" }), "상태 H 부분성공", "v3 status rule without code");
assert.equal(said({ methodId: "hard:negative-duration", code: "HARD_NEGATIVE_DURATION" }), "시작 시각이 종료 시각보다 늦음");
assert.equal(said({ methodId: "hard:success-with-error" }), "성공 상태이나 오류 건수가 기록됨");
assert.equal(said({ methodId: "hard:count-conservation", code: "HARD_COUNT_CONSERVATION" }), "건수 합계 불일치");
assert.equal(said({ category: "soft", methodId: "soft:elapsed_ms-1a2b3c4d", fieldName: "처리 시간", operator: "gte", threshold: "5000", unit: "ms", observedValue: "8000" }),
  "처리 시간 8,000 ms (기준 ≥ 5,000 ms)");
assert.equal(said({ category: "soft", methodId: "soft:retry_count-1a2b3c4d", fieldName: "재시도 수", operator: "gt", threshold: "3", observedValue: "4" }), "재시도 수 4 (기준 > 3)");
assert.equal(said({ category: "soft", ruleKey: "fixture_rule" }), "이상 징후 규칙(과거 등록 Rule)", "v3 registered Rule");
assert.equal(said({ category: "ml", methodId: "ml:iforest", modelName: "Isolation Forest", score: 0.83781, scoreThreshold: "0.61" }), "Isolation Forest 원점수 0.8378 (판정선 0.61)");
assert.equal(said({ category: "ml", methodId: "ml:iforest", modelName: "Isolation Forest" }), "Isolation Forest", "v3 model without score");
assert.equal(labels.causesLine({ causes: causeItem.causes, responseCode: "E500" }), "상태 F 실패 · 응답 코드 E500 외 1건");
assert.equal(labels.causesLine({ causes: [], responseCode: null }), null, "no causes, no line");
// 판정 기록 + 관리자 읽기 전용 기록(사용자 11/13): 시각 순으로 끼우고 진행 중 줄 앞, 업무 분류 상태 줄은 없음(판정 요약에 있음)
const base = [{ key: "i1", at: "2026-11-03T01:00:00Z", tone: "provisional", text: "중간" }, { key: "final", at: "2026-11-03T01:05:00Z", tone: "final", text: "최종" }];
const extra = recordEntries([
  { id: "f2", kind: "suspected-false-positive", reason: "둘째\n줄", actorId: "a", actorEmail: null, createdAt: "2026-11-03T02:00:00Z" },
  { id: "f1", kind: "action", reason: "첫째", actorId: "a", actorEmail: "admin@example.test", createdAt: "2026-11-03T01:02:00Z" },
]);
assert.deepEqual(mergeTimeline(base, extra).map((e) => e.key), ["i1", "record-f1", "final", "record-f2"]);
assert.deepEqual(extra.map((e) => [e.text, e.note]), [["오탐 의심 · a", "둘째\n줄"], ["조치 기록 · admin@example.test", "첫째"]]);
assert.deepEqual(mergeTimeline([...base.slice(0, 1), { key: "progress", at: null, tone: "progress", text: "대기" }], extra).map((e) => e.key),
  ["i1", "record-f1", "record-f2", "progress"], "records sit before the in-progress line");
assert.equal(recordEntries([]).length, 0, "no records, no extra lines");
// 오류 제목
assert.equal(api.analysisErrorTitle(new HttpError("x", 404, "decision_not_found"), "판정 상세"), "판정 상세 · decision_not_found", "missing decision shows the error code as is");
assert.equal(api.analysisErrorTitle(new HttpError("x", 503, "analysis_storage_unavailable"), "판정 목록"), "판정 목록 미연결");
assert.equal(api.analysisErrorTitle(new HttpError("bad", 400, "invalid_q"), "판정 목록"), "bad");
// 실행 그래프(GET …/graph, contracts/process-graph.md): 어휘 밖 노드·간선은 버리고, attrs 원천 값(공백 포함)은 그대로 둔다.
const graphBody = { id: uuid, jobId: null, truncated: false, counts: { processes: 2, messages: 0, errors: 0 },
  graph: { layer: "instance", id: "TX:1", version: null, sourceId: "s-1", snapshotId: null, period: null, ruleSetRevision: null, generatedAt: "2026-11-12T00:00:00.000Z" },
  nodes: [{ id: "transaction:TX%3A1", kind: "transaction", attrs: { transactionId: "TX:1" } }, { id: "process:TX%3A1: A1 ", kind: "process", attrs: { processId: " A1 " } },
    { id: "process:TX%3A1:PR002", kind: "process", attrs: {} }, { id: "x", kind: "pattern_step", attrs: {} }, { kind: "process" }],
  edges: [{ id: "precedes:process:TX%3A1: A1 :process:TX%3A1:PR002", kind: "precedes", from: "process:TX%3A1: A1 ", to: "process:TX%3A1:PR002", verification: "verified", evidence: { handoff: "match", reason: null } },
    { id: "e2", kind: "branches_to", from: "a", to: "b", verification: null, evidence: null },
    { id: "e3", kind: "contains", from: "a", to: "b", verification: "unlinked", evidence: null },
    { id: "e4", kind: "hands_off", from: "a", to: "b", verification: null, evidence: "match" }] };
const parsedGraph = api.parseGraph(graphBody, uuid.toUpperCase());
assert.deepEqual(parsedGraph.nodes.map((n) => n.id), ["transaction:TX%3A1", "process:TX%3A1: A1 ", "process:TX%3A1:PR002"], "unknown node kinds dropped");
assert.equal(parsedGraph.nodes[1].attrs.processId, " A1 ", "attrs keep raw values");
assert.deepEqual(parsedGraph.edges.map((e) => [e.id.slice(0, 8), e.verification, e.evidence]),
  [["precedes", "verified", { handoff: "match", reason: null }], ["e4", null, null]], "unknown edge kinds and verification values dropped, non-object evidence = null");
assert.deepEqual([parsedGraph.graph.layer, parsedGraph.graph.id, parsedGraph.counts.processes], ["instance", "TX:1", 2]);
assert.throws(() => api.parseGraph({ ...graphBody, id: "other" }, uuid), /실행 순서/, "graph of another decision is rejected");
assert.throws(() => api.parseGraph({ ...graphBody, graph: { layer: "tree" } }, uuid), /실행 순서/);
assert.throws(() => api.parseGraph({ ...graphBody, nodes: null }, uuid), /실행 순서/);

console.log("analysis data QA passed: URL/query mapping, response parsers, evidence v3 and v4 reader fallback, result labels, page window, timeline, "
  + "result/risk/workflow sidebar, workflow actions, flow parser and tree, graph parser, feedback parser, error titles");
