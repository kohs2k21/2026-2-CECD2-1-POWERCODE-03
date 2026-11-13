import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 작업 목록·작업 상세 화면(로컬 가짜 fetch만): 목록 요청 필터(종류·상태 탭·커서), 행 표시(종류·대상·상태·결과 연결·실패 코드), 더 보기,
// 상세 작업 단계(시도 기록)·실패 코드·재시도 대기·결과 연결·조회 실패, 운영 설정 입구, 목록 → 상세 → 뒤로 가기.
register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
globalThis.document = { addEventListener() {}, removeEventListener() {} };
globalThis.HTMLFormElement = class {};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const { readFile } = await import("node:fs/promises");
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { NativeSelect } = await import("../src/components/ui/native-select.tsx");
const { jobStatusBadge, jobTarget } = await import("../src/features/detection/jobs/jobFormat.ts");

const catalog = JSON.parse(await readFile(new URL("../../contracts/method-catalog.json", import.meta.url), "utf8"));
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const uuid = (n) => `${String(n).repeat(8).slice(0, 8)}-1111-4111-8111-111111111111`;
const SET = uuid(5), VERSION = uuid(6), OTHER = uuid(7), COMPARISON = uuid(8), SNAPSHOT = uuid(9);
const base = { attemptCount: 0, maxAttempts: 3, nextRunAt: "2026-11-13T03:00:00Z", errorCode: null, resultConfigVersionId: null, finishedAt: null, result: null };
const train = { ...base, id: uuid(1), kind: "train", status: "succeeded", attemptCount: 1, createdAt: "2026-11-13T02:00:00Z", finishedAt: "2026-11-13T02:10:00Z",
  inputSpec: { kind: "train", modelKey: "eif", from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z", featureSetId: uuid(4) },
  result: { configVersionId: VERSION, evaluationId: null, evaluationSetId: null, comparisonId: null, itemCount: null, failedCount: null } };
const failed = { ...base, id: uuid(2), kind: "train", status: "failed", attemptCount: 2, errorCode: "insufficient_complete_training_rows", createdAt: "2026-11-13T01:00:00Z", finishedAt: "2026-11-13T01:05:00Z",
  inputSpec: { kind: "train", from: "2026-10-14T00:00:00Z", until: "2026-10-16T00:00:00Z" } };
const compare = { ...base, id: uuid(3), kind: "compare", status: "succeeded", attemptCount: 1, createdAt: "2026-11-12T23:00:00Z",
  inputSpec: { kind: "compare", evaluationSetId: SET, configVersionIds: [VERSION, OTHER], baselines: [{ modelKey: "iforest", configVersionId: OTHER }] },
  result: { configVersionId: null, evaluationId: null, evaluationSetId: null, comparisonId: COMPARISON, itemCount: 2, failedCount: 1 } };
const relation = { ...base, id: uuid("a"), kind: "recommend_relations", status: "queued", errorCode: "trainer_unavailable", attemptCount: 1, createdAt: "2026-11-12T22:00:00Z",
  inputSpec: { kind: "recommend_relations", snapshotId: SNAPSHOT } };

// 순수 형식: 대상 한 줄, 상태 배지.
assert.equal(jobTarget(train, { methods: catalog.methods }), "Extended Isolation Forest · 10/01 ~ 11/01");
assert.equal(jobTarget({ kind: "train", inputSpec: {} }, {}), "iforest", "omitted model key is the default IF");
assert.equal(jobTarget(compare, { setName: () => "검증 세트 10월" }), "검증 세트 10월 · 모델 2개");
assert.equal(jobTarget({ kind: "recommend_thresholds", inputSpec: { ruleSetRevision: 13, data: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" } } }), "룰 버전 13 · 10/01 ~ 11/01");
assert.deepEqual(jobStatusBadge(relation), { label: "재시도 대기", variant: "warning" });
assert.deepEqual(jobStatusBadge(compare, 1), { label: "완료 · 일부 실패", variant: "warning" });
assert.deepEqual(jobStatusBadge({ ...train, status: "blocked" }), { label: "blocked", variant: "outline" });

const attempt = (attemptNo, status, errorCode = null) => ({ attemptNo, status, startedAt: "2026-11-13T01:01:00Z", heartbeatAt: null, finishedAt: status === "running" ? null : "2026-11-13T01:02:00Z", errorCode });
const details = {
  [failed.id]: { ...failed, result: undefined, resultSummary: null, attempts: [attempt(1, "failed", "trainer_unavailable"), attempt(2, "failed", "insufficient_complete_training_rows")] },
  [compare.id]: { ...compare, result: undefined, resultSummary: { comparisonId: COMPARISON, failedCount: 1, itemCount: 2 }, attempts: [attempt(1, "succeeded")] },
  [relation.id]: { ...relation, result: undefined, resultSummary: null, attempts: [attempt(1, "failed", "trainer_unavailable")] },
};
const running = { ...train, id: uuid("b"), status: "running", finishedAt: null, result: undefined, resultSummary: null, attempts: [attempt(1, "running")] };
details[running.id] = running;
// AI 규칙 추천 실행 중 진행률(llm-recommendation-job.md 7차 '진행률' 절): resultSummary.progress. 형식이 틀린 값은 값 없는 표시로 남는다.
const recommending = { ...base, id: uuid("d"), kind: "recommend_thresholds", status: "running", attemptCount: 1, createdAt: "2026-11-13T04:00:00Z",
  inputSpec: { kind: "recommend_thresholds", ruleSetRevision: 13, data: { from: "2026-10-01T00:00:00Z", until: "2026-11-01T00:00:00Z" } },
  resultSummary: { progress: { processed: 1, total: 4 } }, attempts: [attempt(1, "running")] };
const badProgress = { ...recommending, id: uuid("e"), resultSummary: { progress: { processed: 5, total: 4 } } };
details[recommending.id] = recommending;
details[badProgress.id] = badProgress;

const requested = [];
let pageTwo = [relation];
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, params: Object.fromEntries(target.searchParams) });
  if (target.pathname === "/api/auth/me") return json({ user: { id: "admin-1", email: "admin@example.test", userType: "admin", createdAt: "2026-10-01T00:00:00Z" } });
  if (target.pathname === "/api/jobs/capabilities") return json({ methodCatalog: catalog, snapshot: { timeBasis: "persisted_at" } });
  if (target.pathname === "/api/evaluation-sets") return json({ items: [{ id: SET, sourceId: uuid(1), snapshotId: SNAPSHOT, purpose: "validation", displayName: "검증 세트 10월", status: "ready" }] });
  if (target.pathname === "/api/configurations") return json({ items: [{ id: VERSION, displayName: "V5", modelKey: "eif", active: false, executable: true }, { id: OTHER, displayName: "V4", modelKey: "iforest", active: true, executable: true }], pagination: { limit: 50, hasMore: false, nextCursor: null } });
  if (target.pathname === "/api/jobs") {
    const status = target.searchParams.get("status");
    if (status === "failed") return json({ items: [failed], pagination: { limit: 20, hasMore: false, nextCursor: null } });
    if (status === "queued,running") return json({ items: [], pagination: { limit: 20, hasMore: false, nextCursor: null } });
    if (target.searchParams.get("cursor") === "next-1") return json({ items: pageTwo, pagination: { limit: 20, hasMore: false, nextCursor: null } });
    return json({ items: [train, failed, compare], pagination: { limit: 20, hasMore: true, nextCursor: "next-1" } });
  }
  const detail = target.pathname.match(/^\/api\/jobs\/([^/]+)$/);
  if (detail) return details[detail[1]] ? json(details[detail[1]]) : json({ code: "job_not_found" }, 404);
  return json({ code: "unavailable" }, 503);
};

const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (path, state) => {
  storage.set("token", "token-admin");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const [pathname, search = ""] = path.split("?");
  const router = createMemoryRouter(appRoutes, { initialEntries: [state ? { pathname, search: search ? `?${search}` : "", state } : path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))), { createNodeMock: () => null });
  });
  const shown = () => text(renderer.toJSON());
  const link = (label) => renderer.root.findAll((node) => node.type === "a" && text(node.children).includes(label))[0];
  const row = (id) => text(renderer.root.findAll((node) => node.type === "tr" && node.props["data-job"] === id)[0]?.children);
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, link, row, cleanup };
};
const jobCalls = () => requested.filter((call) => call.path === "/api/jobs");

// 1) 목록: 기본 = 화면 6종류·상태 없음·20개, 행 표시, 더 보기(커서), 상태 탭·종류 선택은 주소와 요청을 바꾼다.
{
  const app = await mount("/detection/operations/jobs");
  await wait(() => app.row(compare.id).includes("검증 세트 10월"), "list rows with names");
  assert.deepEqual(jobCalls()[0].params, { kind: "train,evaluate,evaluation_set,compare,recommend_thresholds,recommend_relations", limit: "20" });
  assert.ok(app.row(train.id).startsWith("11111111학습Extended Isolation Forest · 10/01 ~ 11/01"), app.row(train.id));
  assert.ok(app.row(train.id).endsWith("완료버전 66666666 · V5"), app.row(train.id));
  assert.equal(app.link("버전 66666666").props.href, `/detection/models/${VERSION}`);
  assert.ok(app.row(failed.id).endsWith("실패insufficient_complete_training_rows"), "failure code shown raw");
  assert.ok(app.row(compare.id).includes("비교 평가") && app.row(compare.id).includes("완료 · 일부 실패"));
  assert.equal(app.link("비교 88888888").props.href, `/detection/evaluations/${COMPARISON}`);
  for (const title of ["작업", "종류", "대상", "요청", "상태", "결과"]) assert.ok(app.shown().includes(title));
  const more = app.renderer.root.findAll((node) => node.type === "button" && text(node.children) === "더 보기")[0];
  await act(async () => more.props.onClick());
  await wait(() => app.row(relation.id).includes("재시도 대기"), "second page appended");
  assert.ok(app.row(relation.id).includes("규칙 추천스냅샷 99999999") && app.row(relation.id).endsWith("trainer_unavailable"));
  assert.equal(jobCalls().at(-1).params.cursor, "next-1");
  assert.ok(app.row(train.id), "first page rows stay after more");

  await act(async () => { await app.router.navigate(app.link("실패").props.href); });
  await wait(() => app.row(failed.id) && !app.row(train.id), "failed tab");
  assert.equal(app.router.state.location.search, "?status=failed");
  assert.equal(jobCalls().at(-1).params.status, "failed");
  await act(async () => { await app.router.navigate(app.link("진행 중").props.href); });
  await wait(() => app.shown().includes("이 상태의 작업이 없습니다."), "empty running tab");
  assert.equal(jobCalls().at(-1).params.status, "queued,running");

  const select = app.renderer.root.findByType(NativeSelect);
  await act(async () => select.props.onChange({ target: { value: "train" } }));
  await wait(() => jobCalls().at(-1).params.kind === "train", "kind filter");
  assert.equal(app.router.state.location.search, "?status=running&kind=train");
  await app.cleanup();
}

// 2) 잘못된 탭·종류 값은 기본값으로 바뀐다.
{
  const app = await mount("/detection/operations/jobs?status=bogus&kind=detect");
  await wait(() => app.router.state.location.search === "?status=all&kind=all", "normalized");
  await app.cleanup();
}

// 3) 목록 → 상세: 실패 학습 = 작업 단계(시도 2번)·실패 코드·새 학습, 뒤로 가기는 목록(필터 유지).
{
  const app = await mount("/detection/operations/jobs?status=failed");
  await wait(() => app.link("22222222"), "failed row");
  const entry = app.link("22222222").props;
  await act(async () => { await app.router.navigate(entry.href, { state: { returnTo: "/detection/operations/jobs?status=failed" } }); });
  await wait(() => app.shown().includes("학습 작업 22222222"), "detail");
  assert.ok(app.shown().includes("실패 · insufficient_complete_training_rows"));
  const stages = text(app.renderer.root.findAll((node) => node.type === "ol" && node.props["aria-label"] === "작업 단계")[0].children);
  assert.ok(/^✓접수 · .*✕시도 1 · 실패 · 시작 .* · trainer_unavailable✕시도 2 · 실패 · .* · insufficient_complete_training_rows✕실패 · /.test(stages), stages);
  assert.ok(app.shown().includes("요청 파라미터코드 기본값") && app.shown().includes("시도2/3"));
  assert.equal(app.link("새 학습").props.href, "/detection/models?step=1");
  const back = app.renderer.root.findAll((node) => node.type === "a" && String(node.props["aria-label"]).startsWith("뒤로"))[0];
  assert.equal(back.props.href, "/detection/operations/jobs?status=failed");
  await app.cleanup();
}

// 4) 상세: 비교 평가(일부 실패·결과 연결·입력 이름), 재시도 대기, 실행 중(진행 표시), 없는 작업.
{
  const app = await mount(`/detection/operations/jobs/${compare.id}`);
  await wait(() => app.shown().includes("기준 운영 모델77777777 · V4"), "compare input names");
  assert.ok(app.shown().includes("완료 · 일부 실패") && app.shown().includes("비교 모델66666666 · V5, 77777777 · V4"));
  assert.equal(app.link("결과 보기 · 비교 88888888").props.href, `/detection/evaluations/${COMPARISON}`);
  await app.cleanup();

  const retry = await mount(`/detection/operations/jobs/${relation.id}`);
  await wait(() => retry.shown().includes("재시도 대기 · trainer_unavailable · 시도 1/3"), "retry pending");
  assert.ok(retry.shown().includes("재시도 대기 · 다음 실행"));
  assert.equal(retry.link("결과 보기"), undefined, "no result before success");
  await retry.cleanup();

  const busy = await mount(`/detection/operations/jobs/${running.id}`);
  await wait(() => busy.shown().includes("진행 중"), "running");
  // 값 없는 진행 표시: progressbar + 이름, aria-valuenow 없음.
  const progress = busy.renderer.root.findAll((node) => node.type === "div" && node.props["aria-label"] === "작업 진행 중");
  assert.equal(progress.length, 1);
  assert.equal(progress[0].props.role, "progressbar");
  assert.equal(progress[0].props["aria-valuenow"], undefined);
  assert.ok(busy.shown().includes("시도 1 · 실행 중"));
  await busy.cleanup();

  // AI 규칙 추천 진행률: 같은 이름의 progressbar에 값만 연결하고 숫자 문구는 두지 않는다.
  const recommend = await mount(`/detection/operations/jobs/${recommending.id}`);
  await wait(() => recommend.shown().includes("AI 규칙 추천 작업 dddddddd"), "recommendation running");
  const bar = recommend.renderer.root.findAll((node) => node.type === "div" && node.props["aria-label"] === "작업 진행 중");
  assert.equal(bar.length, 1);
  assert.deepEqual([bar[0].props.role, bar[0].props["aria-valuenow"], bar[0].props["aria-valuemin"], bar[0].props["aria-valuemax"]], ["progressbar", 1, 0, 4]);
  assert.ok(!recommend.shown().includes("1/4"), "no progress number text");
  await recommend.cleanup();
  const bad = await mount(`/detection/operations/jobs/${badProgress.id}`);
  await wait(() => bad.shown().includes("AI 규칙 추천 작업 eeeeeeee"), "bad progress");
  const badBar = bad.renderer.root.findAll((node) => node.type === "div" && node.props["aria-label"] === "작업 진행 중");
  assert.deepEqual([badBar.length, badBar[0].props.role, badBar[0].props["aria-valuenow"]], [1, "progressbar", undefined], "invalid progress keeps the value-less bar");
  await bad.cleanup();

  const missing = await mount(`/detection/operations/jobs/${uuid("c")}`);
  await wait(() => missing.shown().includes("작업 조회 실패 · job_not_found"), "not found");
  await missing.cleanup();
}

// 5) 운영 설정 입구: 다른 조회가 실패해도 작업 목록 링크는 보인다(복귀 주소 = 운영 설정).
{
  const app = await mount("/detection/operations");
  await wait(() => app.link("작업 목록"), "entry");
  assert.equal(app.link("작업 목록").props.href, "/detection/operations/jobs");
  assert.ok(app.shown().includes("운영과 별도로 실행"));
  await app.cleanup();
}

console.log("jobs page tests passed: list filters, paging, rows, detail stages, failure codes, result links, entry");
