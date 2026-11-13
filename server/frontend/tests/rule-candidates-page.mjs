import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 규칙 추천(contracts/rule-candidates-ui.md) 화면 시험(로컬 가짜 fetch만): 응답 해석·충족률 표기·주소, 룰 설정 진입 카드,
// 목록(작업 머리·인터페이스 선택 주소·관계 후보 표 열·처리 시간 표 열·한계 문구·패턴 그래프 간선 이름), 결과 없음,
// 상세(관계: 건수·짝 경고 문장·짝 링크·반례 → 판정 상세·승인 꺼짐 사유 / 처리 시간: 근거·한계·초안), 조회 실패 code, 일반 사용자 접근 불가,
// 후보 작업 요청(확인창 저장 데이터셋 선택 → 접수·같은 키 재전송 → 진행 중 단추 꺼짐·5초 조회 → 새 결과, 데이터셋 없음·조회 실패·요청 실패 code).
register("./svg-loader.mjs", import.meta.url);
// 패턴 그래프(React Flow) 스타일 파일은 시험에서 빈 모듈로 둔다(이 시험 전용 로더).
register("data:text/javascript," + encodeURIComponent('export const load = (url, context, next) => url.endsWith(".css") ? { format: "module", source: "", shortCircuit: true } : next(url, context);'));
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.HTMLFormElement = class {};
// React Flow 정리(cleanupAutoPan)가 부르는 프레임 함수(시험 렌더러에는 없음).
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (handle) => clearTimeout(handle);
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { isAppPath, paths } = await import("../src/app/routes.ts");
const api = await import("../src/features/detection/rules/candidates/candidateApi.ts");
const labels = await import("../src/features/detection/rules/candidates/candidateLabels.ts");
const { buildPatternGraph } = await import("../src/features/detection/rules/candidates/PatternGraphView.tsx");
const { Dialog } = await import("../src/components/ui/dialog.tsx");
const { NativeSelect } = await import("../src/components/ui/native-select.tsx");
const { snapshotText } = await import("../src/features/detection/rules/candidates/CandidateJobRequest.tsx");

const JOB = "8cc86810-673b-4886-87b1-e026856fe636";
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const counts = (applicable, satisfied, undetermined = 0) => ({ applicable, satisfied, violated: applicable - satisfied, undetermined });
const job = { id: JOB, status: "succeeded", errorCode: null, createdAt: "2026-11-12T03:00:00.000000Z", finishedAt: "2026-11-12T03:01:00.000000Z", snapshotId: "snap-1" };
const approval = { available: false, reason: "rule_set_hash_v3_pending", contentHashVersion: 2 };
const relation = (index, kind, from, to, extra = {}) => ({ candidateId: `${JOB}.r${index}`, kind, status: "candidate", approvable: true, listedForPairing: false,
  pairApprovable: kind === "message_handoff" ? true : null, interfaceId: "IF-A", scope: kind === "required_step" ? { interfaceId: "IF-A" } : { adapterType: "HTTP" },
  from: { processId: from }, to: to ? { processId: to } : null, match: null, counts: counts(1000, 999), satisfactionRate: 0.999, evaluatedAt: kind === "required_step" ? "transaction" : "successor",
  patternEdgeId: null, nonStandardProcessId: false, ...extra });
/** 옛 Trainer 한계 문구(내부 경로 포함) — 화면은 고정 문장으로 바꾼다. */
const OLD_LIMITATION = "methods/registry.py sourceMissingPolicy END_TIME=not_applicable";
/** '/'가 있어도 경로가 아닌 문구(S/F 상태·건/분 단위) — 그대로 보인다. */
const SLASH_LIMITATION = "S/F 거래 모두 포함, 처리량은 건/분 기준이다.";
const threshold = (index, processId, extra = {}) => ({ candidateId: `${JOB}.t${index}`, field: "elapsed_ms", status: "candidate", reason: null, scope: { processId, adapterType: "HTTP" },
  interfaceIds: ["IF-A"], operator: "gt", threshold: 812.5, displayThreshold: "812.5", unit: "ms", source: "data_p95",
  evidence: { n: 120, rows: 140, referenceRows: 130, endTimeMissingReferenceRows: 10, unavailableReferenceRows: 0, p95: 812.5, min: 3, max: 990, quantileMethod: "linear" },
  limitation: "END_TIME이 없으면 평가하지 않는다. 끝나지 않고 멈춘 단계는 잡지 못한다.", nonStandardProcessId: false, ...extra });
const step = (pid) => ({ id: `pattern_step:IF-A:${pid}:HTTP`, kind: "pattern_step", attrs: { interfaceId: "IF-A", processId: pid, adapterType: "HTTP", transactions: 1000 } });
const head = { job, snapshotId: "snap-1", sourceId: "s-1", period: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z", timeBasis: "persisted_at" },
  generatedAt: "2026-11-12T03:01:00.000Z", experimental: true, parameters: { minApplicable: 30, candidatePercent: 99, reviewPercent: 95, maxCounterexamples: 5 } };
const listBody = (interfaceId = "IF-A", latestJob = job) => ({
  latestJob, approval,
  result: { ...head,
    interfaces: [{ interfaceId: "IF-A", transactions: 1000, relationCandidates: 4, approvable: 3, thresholdCandidates: 1 }, { interfaceId: "IF-B", transactions: 50, relationCandidates: 0, approvable: 0, thresholdCandidates: 0 }],
    selectedInterfaceId: interfaceId,
    graph: interfaceId === "IF-A" ? { graph: { layer: "pattern" }, nodes: [step("P1"), step("P2"), step("P3")], edges: [
      { id: "precedes:1", kind: "precedes", from: step("P1").id, to: step("P2").id, evidence: { status: "candidate", satisfactionRate: 0.9996, applicable: 1000, observedTransactions: 1000 } },
      { id: "precedes:2", kind: "precedes", from: step("P1").id, to: step("P3").id, evidence: { status: "insufficient_evidence", satisfactionRate: 0.0004, applicable: 2, observedTransactions: 2 } },
      { id: "hands_off:1", kind: "hands_off", from: step("P1").id, to: step("P2").id, evidence: { status: "review_needed", satisfactionRate: 0.97 } }] } : { graph: {}, nodes: [], edges: [] },
    relationCandidates: interfaceId === "IF-A" ? [relation(0, "required_step", "P1", null), relation(1, "precedes", "P1", "P2", { status: "not_candidate", approvable: false, listedForPairing: true, counts: counts(1000, 900, 3), satisfactionRate: 0.9 }),
      relation(2, "message_handoff", "P1", "P2", { status: "review_needed", pairApprovable: false, counts: counts(1000, 970), satisfactionRate: 0.97 }),
      relation(3, "precedes", "P1", "P3", { status: "insufficient_evidence", approvable: false, scope: null, counts: counts(2, 2), satisfactionRate: 1 })] : [],
    thresholdCandidates: interfaceId === "IF-A" ? [threshold(0, "P1"), threshold(1, "P2", { status: "insufficient_evidence", threshold: null, displayThreshold: null, operator: null,
      interfaceIds: ["IF-A", "IF-B"], scope: { processId: "P2" } })] : [],
    thresholdsWithoutInterface: 2,
    thresholdLimitations: ["END_TIME이 없으면 평가하지 않는다. 끝나지 않고 멈춘 단계는 잡지 못한다.", OLD_LIMITATION, "END_TIME 정책: workers/elapsed.py", SLASH_LIMITATION],
    totals: { relationCandidates: 4, approvable: 3, thresholdCandidates: 2, thresholdApprovable: 1 } },
});
const relationDetail = (index, extra = {}) => ({ ...head, isLatest: true, approval, type: "relation",
  relation: { ...listBody().result.relationCandidates[index], violationReasons: { message_id_mismatch: 30 }, undeterminedReasons: { no_in_message: 2, odd_code: 1 },
    violationsByTransactionStatus: { S: 29, F: 1 } },
  counterexamples: [{ transactionId: "TX:1", reason: "message_id_mismatch", transactionStatus: "S", decisionId: "d-1" }, { transactionId: "TX-2", reason: "message_id_mismatch", transactionStatus: "F", decisionId: null }],
  pairedPrecedes: { candidateId: `${JOB}.r1`, status: "not_candidate", approvable: false, counts: counts(1000, 900), satisfactionRate: 0.9 },
  ruleDraft: { kind: "message_handoff", scope: { interfaceId: "IF-A", adapterType: "HTTP" }, from: { processId: "P1" }, to: { processId: "P2" }, match: { from: "OUT", to: "IN", key: "message_id" }, enabled: true },
  ...extra });

// 1) 응답 해석·표기·주소
{
  assert.throws(() => api.parseCandidateList({ latestJob: null, approval: { available: "no" }, result: null }), /승인 상태 응답/);
  assert.deepEqual(api.parseCandidateList({ latestJob: null, approval, result: null }), { latestJob: null, approval, result: null });
  assert.throws(() => api.parseRelation({ ...relation(0, "precedes", "P1", null) }), /관계 후보 응답/, "precedes needs to");
  assert.throws(() => api.parseRelation({ ...relation(0, "allowed_branch", "P1", "P2") }), /관계 후보 응답/);
  assert.throws(() => api.parseThreshold({ ...threshold(0, "P1"), evidence: { n: 1 } }), /처리 시간 후보 응답/);
  // API는 Trainer 고정소수 문자열("704")을 number로 바꿔 낸다(4절). 문자열이 그대로 오면 조용히 비우지 않고 형식 오류.
  assert.equal(api.parseThreshold(threshold(0, "P1", { threshold: 704, displayThreshold: "704" })).threshold, 704);
  assert.equal(api.parseThreshold(threshold(1, "P2", { status: "insufficient_evidence", threshold: null, displayThreshold: null, operator: null })).threshold, null);
  for (const bad of ["704", "1e3", undefined]) assert.throws(() => api.parseThreshold(threshold(0, "P1", { threshold: bad })), /처리 시간 후보 응답/, `threshold ${bad}`);
  const parsed = api.parseCandidateList(listBody());
  assert.deepEqual([parsed.result.relationCandidates.length, parsed.result.thresholdCandidates[1].scope, parsed.result.graph.edges.length], [4, { processId: "P2" }, 3]);
  assert.deepEqual([0.9996, 0.999, 0.97, 0.0004, 1, 0, null].map(labels.rateText), ["99.9%", "99.9%", "97.0%", "0.1%", "100%", "0%", "—"], "floor to one decimal, never round up to 100%");
  assert.equal(labels.pathText({ kind: "required_step", from: { processId: "P1" }, to: null }), "P1");
  assert.equal(labels.adapterText({ kind: "precedes", scope: null }), "모든 어댑터");
  assert.equal(labels.thresholdText({ operator: "gt", displayThreshold: "812.5", unit: "ms" }), "> 812.5 ms");
  assert.equal(labels.reasonText("odd_code"), "odd_code", "unknown reasons are shown as-is");
  assert.equal(labels.END_TIME_LIMITATION, "종료 시각(END_TIME)이 없는 실행은 처리 시간 계산에서 제외됩니다. 늦게 끝난 단계는 잡지만, 끝나지 않고 멈춘 단계는 잡지 못합니다.",
    "Trainer limitation sentence in full");
  for (const kept of ["S/F 거래 모두 포함", "처리량 건/분", "IN/OUT 메시지", "A/B", "p95 / n", "END_TIME/START_TIME"]) assert.equal(labels.limitationText(kept), kept, kept);
  for (const path of ["methods/registry.py", "see elapsed.py", "trainer/relation_candidates", "정책: workers/elapsed", "a_b/c.d"]) {
    assert.equal(labels.limitationText(path), labels.END_TIME_LIMITATION, path);
  }
  assert.equal(labels.PAIR_WARNING, "짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다.", "Detector relation-rules.md 4절 문장 그대로");
  const graph = buildPatternGraph(parsed.result.graph.nodes, parsed.result.graph.edges);
  assert.deepEqual(graph.edges.map((e) => [e.id, e.data.label]), [["precedes:1", "99.9%"], ["precedes:2", "0.1%"]], "precedes edges only, labelled with satisfaction rate");
  assert.ok(graph.nodes[0].position.x < graph.nodes[1].position.x, "left to right");
  assert.equal(paths.detectionRuleCandidate(`${JOB}.r2`), `/detection/rules/candidates/${JOB}.r2`);
  assert.ok(isAppPath("/detection/rules/candidates") && isAppPath(`/detection/rules/candidates/${JOB}.t0`));
}

let routes = {};
const requested = [];
globalThis.fetch = async (url, init = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, search: target.search, method: init.method ?? "GET" });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  if (target.pathname === "/api/pipeline/events") return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "text/event-stream" } });
  const handler = routes[target.pathname];
  if (handler) return handler(target, init);
  return json({ code: "rule_set_storage_unavailable" }, 503);
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
let currentUser;
const mount = async (path, userType = "admin") => {
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))));
  });
  const shown = () => text(renderer.toJSON());
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, cleanup };
};
const at = (router) => router.state.location.pathname + router.state.location.search;
const byData = (app, key) => app.renderer.root.findAll((node) => node.props?.[key] !== undefined && typeof node.type === "string");
const rowsOf = (app, key) => byData(app, key).map((row) => row.findAllByType("td").map((td) => text(td.children)));
const headsOf = (app, label) => app.renderer.root.findAll((n) => n.type === "table" && n.props["aria-label"] === label)[0].findAllByType("th").map((th) => text(th.children));

// 2) 룰 설정 진입 카드 → 목록
{
  routes = {
    "/api/rule-set": () => json({ code: "rule_set_storage_unavailable" }, 503),
    "/api/rule-set/candidates": (target) => json(listBody(target.searchParams.get("interfaceId") ?? "IF-A", { ...job, id: "99999999-2222-4333-8444-555555555555", status: "failed", errorCode: "relation_snapshot_not_ready" })),
  };
  const app = await mount("/detection/rules");
  await wait(() => app.shown().includes("현재 운영 룰 조회 실패"), "rules page rendered");
  assert.ok(!app.shown().includes("후보 보기"), "entry card is part of the editor, hidden while the rule set fails");
  await app.cleanup();
}
{
  const app = await mount(paths.detectionRuleCandidates);
  await wait(() => byData(app, "data-candidate-row").length === 4, "relation rows rendered");
  assert.ok(!requested.filter((r) => r.path === "/api/rule-set/candidates").at(-1).search.includes("interfaceId"), "no interface in address → server default");
  const jobLine = text(byData(app, "data-candidate-job")[0].children);
  assert.ok(jobLine.includes("관계 후보 4건(승인 가능 3)") && jobLine.includes("처리 시간 후보 2건(후보 1)") && jobLine.includes("최근 작업 실패 · relation_snapshot_not_ready"), jobLine);
  assert.deepEqual(headsOf(app, "관계 규칙 후보 표"), ["종류", "경로·단계", "어댑터", "적용", "충족", "위반", "보류", "충족률", "상태", "승인 가능"]);
  assert.deepEqual(rowsOf(app, "data-candidate-row"), [
    ["필수 단계", "P1", "—", "1,000", "999", "1", "—", "99.9%", "후보", "가능"],
    ["선행 관계", "P1 → P2", "HTTP", "1,000", "900", "100", "3", "90.0%", "기준 미달인계 짝", "불가"],
    ["메시지 인계", "P1 → P2", "HTTP", "1,000", "970", "30", "—", "97.0%", "검토 필요", "가능"],
    ["선행 관계", "P1 → P3", "모든 어댑터", "2", "2", "—", "—", "100%", "근거 부족", "불가"],
  ]);
  assert.deepEqual(headsOf(app, "처리 시간(이상 징후) 후보 표"), ["단계", "어댑터", "제안 임계값", "근거 건수", "END_TIME 없음", "상태"]);
  assert.deepEqual(rowsOf(app, "data-threshold-row"), [
    ["P1", "HTTP", "> 812.5 ms기준 표본 p95(코드 계산)", "120", "10", "후보"],
    ["P2인터페이스 2곳 공통", "모든 어댑터", "—", "120", "10", "근거 부족"],
  ]);
  assert.deepEqual(byData(app, "data-threshold-limitation").map((n) => text(n.children)),
    ["한계(Trainer) · END_TIME이 없으면 평가하지 않는다. 끝나지 않고 멈춘 단계는 잡지 못한다.", `한계(Trainer) · ${labels.END_TIME_LIMITATION}`, `한계(Trainer) · ${SLASH_LIMITATION}`],
    "Trainer sentence as-is; texts with internal paths replaced by one fixed sentence; S/F·건/분 kept");
  assert.ok(!app.shown().includes("registry.py") && !app.shown().includes("workers/"), "no internal path on screen");
  assert.ok(text(byData(app, "data-threshold-unassigned")[0].children).includes("2건"));
  const link = byData(app, "data-candidate-row")[2].findAllByType("a")[0];
  assert.equal(link.props.href, `/detection/rules/candidates/${JOB}.r2`);
  const trigger = app.renderer.root.findAll((n) => n.type === "button" && n.props["data-slot"] === "combobox-trigger")[0];
  assert.equal(text(trigger.children), "IF-A · 승인 가능 3", "searchable interface picker shows the selection");
  const { filterComboboxOptions } = await import("../src/components/ui/combobox.tsx");
  const many = Array.from({ length: 300 }, (_, i) => ({ value: `IF-ORD-${String(i).padStart(3, "0")}`, label: `IF-ORD-${String(i).padStart(3, "0")}` }));
  assert.deepEqual(filterComboboxOptions(many, "  ord-29 ").map((o) => o.value), Array.from({ length: 10 }, (_, i) => `IF-ORD-29${i}`),
    "case-insensitive partial match, trimmed, original order");
  assert.equal(filterComboboxOptions(many, "").length, 300, "blank search lists all");
  assert.equal(filterComboboxOptions(many, "%").length, 0, "no wildcard");
  const select = app.renderer.root.findAll((n) => typeof n.props?.onValueChange === "function" && n.props.value === "IF-A")[0];
  await act(async () => select.props.onValueChange("IF-B"));
  await wait(() => at(app.router) === "/detection/rules/candidates?interface=IF-B", "interface in address");
  await wait(() => requested.filter((r) => r.path === "/api/rule-set/candidates").at(-1).search === "?interfaceId=IF-B", "interface sent to API");
  await wait(() => app.shown().includes("이 인터페이스의 관계 규칙 후보가 없습니다."), "empty interface");
  await app.cleanup();
}
// 3) 결과 없음·조회 실패
{
  routes = { "/api/rule-set/candidates": () => json({ latestJob: null, approval, result: null }) };
  const app = await mount(paths.detectionRuleCandidates);
  await wait(() => app.shown().includes("후보 작업 결과가 없습니다."), "explicit empty state");
  await app.cleanup();
  routes = { "/api/rule-set/candidates": () => json({ code: "rule_candidate_contract_unsupported" }, 503) };
  const failed = await mount(paths.detectionRuleCandidates);
  await wait(() => failed.shown().includes("규칙 추천 조회 실패 · rule_candidate_contract_unsupported"), "error code shown, not an empty list");
  await failed.cleanup();
}
// 4) 상세 — 관계(메시지 인계): 건수·짝 경고·짝 링크·사유·반례·승인 꺼짐·초안
{
  routes = { [`/api/rule-set/candidates/${JOB}.r2`]: () => json(relationDetail(2)) };
  const app = await mount(`/detection/rules/candidates/${JOB}.r2`);
  await wait(() => byData(app, "data-counterexample").length === 2, "counterexamples rendered");
  const shown = app.shown();
  assert.ok(shown.includes("메시지 인계 P1 → P2") && shown.includes("검토 필요"));
  assert.ok(shown.includes("뒤 단계 도착 때 판정 · 거래 행 도착 때 최종"));
  assert.equal(text(byData(app, "data-pair-warning")[0].children), labels.PAIR_WARNING);
  assert.equal(byData(app, "data-paired")[0].findAllByType("a")[0].props.href, `/detection/rules/candidates/${JOB}.r1`);
  assert.deepEqual(byData(app, "data-tally").map((n) => text(n.children)), ["메시지 ID 불일치 30건", "후행 IN 메시지 없음 2건", "odd_code 1건", "S 29건", "F 1건"]);
  assert.deepEqual(rowsOf(app, "data-counterexample"), [["TX:1", "메시지 ID 불일치", "S", "실행 순서 보기"], ["TX-2", "메시지 ID 불일치", "F", "판정 없음"]]);
  assert.equal(byData(app, "data-counterexample")[0].findAllByType("a")[0].props.href, "/analysis/d/d-1");
  const approve = app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "승인")[0];
  assert.equal(approve.props.disabled, true, "approve is off until rule set hash v3");
  assert.ok(text(byData(app, "data-approve-bar")[0].children).includes("룰 세트 형식 3"), "exact reason shown");
  assert.ok(shown.includes('{"interfaceId":"IF-A","adapterType":"HTTP"}') && shown.includes('{"from":"OUT","to":"IN","key":"message_id"}'), "draft condition shown");
  const back = app.renderer.root.findAll((n) => n.type === "a" && String(n.props["aria-label"] ?? "").startsWith("뒤로"))[0];
  assert.equal(back.props.href, "/detection/rules", "detail opened directly: back to the rules menu");
  await app.cleanup();
}
// 5) 상세 — 기준 미달 후보·이전 작업·처리 시간
{
  routes = { [`/api/rule-set/candidates/${JOB}.r1`]: () => json(relationDetail(1, { isLatest: false, counterexamples: [], pairedPrecedes: null })) };
  const app = await mount(`/detection/rules/candidates/${JOB}.r1`);
  await wait(() => app.shown().includes("위반 거래가 없습니다."), "no counterexamples");
  assert.ok(byData(app, "data-not-latest").length === 1 && byData(app, "data-pair-warning").length === 0);
  assert.ok(text(byData(app, "data-approve-bar")[0].children).includes("승인 불가 · 후보 기준 미달"));
  await app.cleanup();
  routes = { [`/api/rule-set/candidates/${JOB}.t0`]: () => json({ ...head, isLatest: true, approval, type: "threshold", threshold: { ...threshold(0, "P1", { limitation: OLD_LIMITATION, limitationRefs: ["methods/registry.py"] }),
      evidence: { ...threshold(0, "P1").evidence, note: "referenceRows excludes END_TIME null" } },
    ruleDraft: { field: "elapsed_ms", operator: "gt", threshold: 812.5, unit: "ms", enabled: true, scope: { processId: "P1", adapterType: "HTTP", interfaceId: "IF-A" } } }) };
  const t = await mount(`/detection/rules/candidates/${JOB}.t0`);
  await wait(() => t.shown().includes("처리 시간 P1"), "threshold detail");
  const s = t.shown();
  assert.ok(s.includes("> 812.5 ms") && s.includes("기준 표본 p95(코드 계산)") && s.includes("END_TIME 없음(기준 표본)10건") && s.includes("3 ms · 990 ms · 812.5 ms"), s.slice(0, 400));
  assert.equal(text(byData(t, "data-threshold-limitation")[0].children), `한계(Trainer) · ${labels.END_TIME_LIMITATION}`, "old limitation with a path: fixed sentence");
  assert.ok(!s.includes("registry.py") && !s.includes("referenceRows excludes"), "limitationRefs and evidence.note never shown");
  assert.equal(t.renderer.root.findAll((n) => n.type === "button" && text(n.children) === "승인")[0].props.disabled, true);
  await t.cleanup();
  routes = {};
  const missing = await mount(`/detection/rules/candidates/${JOB}.t9`);
  await wait(() => missing.shown().includes("후보 상세 조회 실패 · rule_set_storage_unavailable"), "detail error code");
  await missing.cleanup();
}
// 6) 일반 사용자: 관리자 권한 안내(AdminGate), 후보 API를 부르지 않음
{
  requested.length = 0;
  routes = { "/api/rule-set/candidates": () => json(listBody()) };
  const app = await mount(paths.detectionRuleCandidates, "user");
  await wait(() => app.shown().includes("관리자 권한이 필요합니다"), "admin gate for a user");
  assert.ok(!requested.some((r) => r.path.startsWith("/api/rule-set/candidates")), "no candidate request for a user");
  await app.cleanup();
}
// 7) 후보 작업 요청
const elements = (node) => (Array.isArray(node) ? node.flatMap(elements) : React.isValidElement(node) ? [node, ...elements(node.props.children)] : []);
/** 확인창은 포털이라 시험 렌더러에 없다. 열린 Dialog 요소 트리에서 찾는다(operations-page.mjs와 같은 방식). */
const openDialog = (app) => app.renderer.root.findAllByType(Dialog).find((node) => node.props.open);
const dialogPart = (app, match) => elements(openDialog(app)?.props.children ?? []).find(match);
const dialogButton = (app, label) => dialogPart(app, (node) => typeof node.props.onClick === "function" && node.props.children === label);
const pageButton = (app, label) => app.renderer.root.findAll((n) => n.type === "button" && text(n.children) === label)[0];
const elementText = (node) => (typeof node === "string" || typeof node === "number" ? String(node) : Array.isArray(node) ? node.map(elementText).join("")
  : React.isValidElement(node) ? elementText(node.props.children) : "");
const SNAP_NEW = "11111111-2222-4333-8444-555555555555";
const NEW_JOB = "22222222-3333-4444-8555-666666666666";
const snapshots = [
  { id: SNAP_NEW, sourceId: "s-1", createdAt: "2026-11-13T01:00:00.000000Z", readyAt: "2026-11-13T01:05:00.000000Z", from: "2026-10-13T00:00:00Z", until: "2026-11-13T00:00:00Z" },
  { id: "snap-1", sourceId: "s-1", createdAt: "2026-11-01T01:00:00.000000Z", readyAt: null, from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" },
];
{
  let phase = "done";
  const posted = [];
  const newJob = { ...job, id: NEW_JOB, snapshotId: SNAP_NEW };
  routes = {
    "/api/rule-set/candidates": () => json(phase === "done" ? listBody()
      : phase === "queued" ? listBody("IF-A", { ...newJob, status: "queued", finishedAt: null })
        : { ...listBody("IF-A", newJob), result: { ...listBody().result, job: newJob, snapshotId: SNAP_NEW } }),
    "/api/rule-set/candidates/snapshots": () => json({ items: snapshots }),
    "/api/rule-set/candidates/jobs": (_target, init) => {
      posted.push({ method: init.method, body: JSON.parse(init.body), key: new Headers(init.headers).get("Idempotency-Key") });
      if (posted.length === 1) return json({ code: "snapshot_not_ready" }, 409);
      phase = "queued";
      return json({ id: NEW_JOB, status: "queued", kind: "recommend_relations", inputSpec: { kind: "recommend_relations", snapshotId: SNAP_NEW, sourceId: "s-1" } }, 201);
    },
  };
  requested.length = 0;
  const app = await mount(paths.detectionRuleCandidates);
  await wait(() => byData(app, "data-candidate-row").length === 4, "list rendered");
  assert.ok(!requested.some((r) => r.path === "/api/rule-set/candidates/snapshots"), "saved datasets are read only when the dialog opens");
  assert.equal(byData(app, "data-latest-job").length, 0, "latest job is the result job");
  await act(async () => pageButton(app, labels.JOB_REQUEST).props.onClick());
  await wait(() => Boolean(dialogPart(app, (n) => n.type === NativeSelect)), "dialog lists saved datasets");
  assert.ok(dialogPart(app, (n) => n.props.children === labels.JOB_REQUEST_DESCRIPTION), "description sentence");
  const select = dialogPart(app, (n) => n.type === NativeSelect);
  assert.equal(select.props.value, SNAP_NEW, "newest dataset is the default");
  const options = elements(select.props.children).map((o) => o.props.children);
  assert.deepEqual(options, [snapshotText(snapshots[0], "snap-1"), snapshotText(snapshots[1], "snap-1")]);
  assert.ok(options[1].endsWith(" · 현재 결과") && !options[0].includes("현재 결과"), "dataset of the shown result is marked");
  // 첫 요청 409 → 확인창에 코드, 닫히지 않음. 다시 요청은 같은 키(서버 멱등).
  await act(async () => dialogButton(app, labels.JOB_REQUEST_SUBMIT).props.onClick());
  await wait(() => Boolean(dialogPart(app, (n) => n.props["data-candidate-request-error"] !== undefined)), "request error shown");
  assert.equal(elementText(dialogPart(app, (n) => n.props["data-candidate-request-error"] !== undefined)), `${labels.JOB_REQUEST} 실패 · snapshot_not_ready`);
  await act(async () => dialogButton(app, labels.JOB_REQUEST_SUBMIT).props.onClick());
  await wait(() => !openDialog(app), "dialog closes after acceptance");
  assert.deepEqual(posted.map((p) => [p.method, p.body]), [["POST", { snapshotId: SNAP_NEW }], ["POST", { snapshotId: SNAP_NEW }]], "snapshotId only (Trainer defaults)");
  assert.ok(posted[0].key && posted[0].key === posted[1].key, "same key for the resend in one dialog");
  await wait(() => byData(app, "data-latest-job").length === 1, "list re-read: queued job shown");
  const latest = byData(app, "data-latest-job")[0];
  assert.ok(text(latest.children).startsWith("최근 작업 대기"), text(latest.children));
  assert.equal(latest.findAllByType("a")[0].props.href, `/detection/operations/jobs/${NEW_JOB}`, "job detail link");
  assert.equal(pageButton(app, labels.JOB_REQUEST).props.disabled, true, "no second request while a candidate job runs");
  assert.equal(text(byData(app, "data-candidate-running")[0].children), labels.JOB_REQUEST_RUNNING);
  // 진행 중이면 5초마다 목록을 다시 읽는다 → 끝나면 새 결과가 목록이 된다.
  phase = "succeeded";
  await wait(() => byData(app, "data-latest-job").length === 0 && byData(app, "data-candidate-running").length === 0, "new result after polling", 800);
  assert.equal(pageButton(app, labels.JOB_REQUEST).props.disabled, false, "request is available again");
  await app.cleanup();
}
{
  routes = { "/api/rule-set/candidates": () => json({ latestJob: null, approval, result: null }), "/api/rule-set/candidates/snapshots": () => json({ items: [] }) };
  const app = await mount(paths.detectionRuleCandidates);
  await wait(() => app.shown().includes("후보 작업 결과가 없습니다."), "no result yet");
  await act(async () => pageButton(app, labels.JOB_REQUEST).props.onClick());
  await wait(() => Boolean(dialogPart(app, (n) => n.props["data-snapshots-empty"] !== undefined)), "no saved dataset");
  assert.ok(dialogPart(app, (n) => n.props.children === labels.SNAPSHOTS_EMPTY), "empty sentence");
  const link = dialogPart(app, (n) => n.props.to !== undefined);
  assert.deepEqual([link.props.to, link.props.children], ["/detection/models?step=1", labels.SNAPSHOTS_EMPTY_LINK], "points to 새 학습");
  assert.equal(dialogButton(app, labels.JOB_REQUEST_SUBMIT).props.disabled, true, "nothing to request");
  await act(async () => dialogButton(app, "취소").props.onClick());
  await wait(() => !openDialog(app), "cancel closes");
  await app.cleanup();
  routes = { "/api/rule-set/candidates": () => json({ latestJob: null, approval, result: null }), "/api/rule-set/candidates/snapshots": () => json({ code: "rule_set_storage_unavailable" }, 503) };
  const failed = await mount(paths.detectionRuleCandidates);
  await wait(() => Boolean(pageButton(failed, labels.JOB_REQUEST)), "button");
  await act(async () => pageButton(failed, labels.JOB_REQUEST).props.onClick());
  await wait(() => Boolean(dialogPart(failed, (n) => n.props.title === "저장 데이터셋 조회 실패 · rule_set_storage_unavailable")), "saved dataset list error code");
  assert.equal(dialogButton(failed, labels.JOB_REQUEST_SUBMIT).props.disabled, true);
  await failed.cleanup();
}
console.log("rule candidates page tests passed");
process.exit(0);
