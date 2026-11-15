import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 평가 결과 화면 통합(가짜 fetch, evaluations-fake-api.mjs): 평가 API 없는 서버의 오류 표시, 목록·탭·상세·새 비교 평가 3단계·평가 세트 생성.
// 요청 실패는 성공으로 꾸미지 않는다. v1 서버의 교체 추천 없음은 미연결로 보인다.
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

const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, Link, RouterProvider } = await import("react-router-dom");
const { RUNNING_POLL_MS } = await import("../src/lib/query.ts");
const { Dialog } = await import("../src/components/ui/dialog.tsx");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
const { useDraftStore } = await import("../src/stores/draftStore.ts");
const { Checkbox } = await import("../src/components/ui/checkbox.tsx");
const { RadioGroup, RadioGroupItem } = await import("../src/components/ui/radio-group.tsx");
const api = await import("../src/features/detection/evaluations/evaluationsApi.ts");
const draftModule = await import("../src/features/detection/evaluations/compareDraft.ts");
const sample = await import("./evaluations-sample.ts");
const { configurationOf, evaluationResponse } = await import("./evaluations-fake-api.mjs");
const catalog = JSON.parse(await readFile(new URL("../../contracts/method-catalog.json", import.meta.url), "utf8"));

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const requested = [];
let currentUser;
const fake = { up: true, posts: {}, requests: [] };
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  const method = options.method ?? "GET";
  requested.push({ path: target.pathname, method });
  if (target.pathname === "/api/auth/me") return json({ user: currentUser });
  const evaluation = evaluationResponse(sample, fake, method, target, options.body ? JSON.parse(options.body) : undefined);
  if (evaluation) return evaluation;
  if (target.pathname === "/api/configurations") return json({ items: sample.compareVersions.map(configurationOf), pagination: { limit: 100, hasMore: false, nextCursor: null } });
  if (target.pathname === "/api/jobs/capabilities") return json({ methodCatalog: catalog, snapshot: { timeBasis: "persisted_at" } });
  if (target.pathname === "/api/operational-state") return json({ ruleSet: { revision: sample.ruleRevision } });
  return json({ code: "unavailable" }, 503);
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (path, { up = true, keepDrafts = false, userType = "admin" } = {}) => {
  fake.up = up;
  fake.posts = {};
  currentUser = { id: `${userType}-1`, email: `${userType}@example.test`, userType, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${userType}`);
  if (!keepDrafts) useDraftStore.getState().reset();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))), { createNodeMock: () => null });
  });
  for (let i = 0; i < 20 && text(renderer.toJSON()).includes("로그인 상태를 확인하는 중"); i++) await tick();
  const shown = () => text(renderer.toJSON());
  const button = (label) => renderer.root.findAll((node) => node.type === "button" && text(node.children) === label)[0];
  const click = async (label) => {
    assert.notEqual(button(label)?.props.disabled, true, `${label} is enabled`);
    await act(async () => button(label).props.onClick());
  };
  const link = (label) => renderer.root.findAll((node) => node.type === "a" && text(node.children).includes(label))[0];
  const go = async (label) => act(async () => { await router.navigate(link(label).props.href); });
  const rows = () => renderer.root.findAll((node) => node.type === "tr" && node.props["data-slot"] === "table-row");
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, shown, button, click, link, go, rows, cleanup };
};
const search = (app) => app.router.state.location.search;
const pickSet = (app, id) => act(async () => app.renderer.root.findByType(RadioGroup).props.onValueChange(id));
const pickVersion = (app, label, checked = true) => act(async () => app.renderer.root.findAllByType(Checkbox).find((node) => node.props["aria-label"] === label).props.onCheckedChange(checked));
const box = (app, label) => app.renderer.root.findAllByType(Checkbox).find((node) => node.props["aria-label"] === label);

const [SET_ESB, , , SET_QUEUED, SET_FAILED] = sample.evaluationSets.map((set) => set.id);
const [C_RUNNING, C_LATEST, , C_FAILED, C_ADBENCH, C_ESB_OLD] = sample.comparisons.map((row) => row.comparisonId);
const short = (id) => id.slice(0, 8);
const rowOf = (app, id) => app.rows().find((row) => text(row.children).startsWith(short(id)));
const rowText = (app, id) => text(rowOf(app, id)?.children);
const linkProps = (app, label) => app.renderer.root.findAllByType(Link).find((node) => text(node.children).includes(label))?.props;
const stored = () => useDraftStore.getState().drafts["/detection/evaluations"];
const fieldOf = (app, id) => app.renderer.root.findAll((node) => ["input", "select"].includes(node.type) && node.props.id === id)[0];
const change = (app, id, value) => act(async () => fieldOf(app, id).props.onChange({ target: { value } }));
const openDialog = (app) => app.renderer.root.findAllByType(Dialog).find((node) => node.props.open);

// 평가 API가 없는 서버(503): 오류 상태만, 예시 데이터나 빈 성공으로 바꾸지 않는다.
{
  requested.length = 0;
  const app = await mount("/detection/evaluations", { up: false });
  await wait(() => app.shown().includes("평가 조회 실패 · service_unavailable"), "production list fails");
  await wait(() => app.shown().includes("평가 세트 조회 실패 · service_unavailable"), "production sets fail");
  assert.ok(!app.shown().includes("ESB 합성"), "no sample when the API fails");
  assert.ok(requested.some((call) => call.path === "/api/comparisons"), "reads the comparison API");
  await app.cleanup();
  const detail = await mount(`/detection/evaluations/${C_LATEST}`, { up: false });
  await wait(() => detail.shown().includes("평가 조회 실패 · service_unavailable"), "production detail fails");
  assert.ok(!detail.shown().includes("모델 교체 추천"));
  await detail.cleanup();
  await assert.rejects(api.requestComparison({ kind: "compare" }), (error) => error.code === "service_unavailable");
  await assert.rejects(api.readSnapshots(), (error) => error.code === "service_unavailable");
  const stepped = await mount("/detection/evaluations?step=3", { up: false });
  await wait(() => stepped.shown().includes("평가 세트 조회 실패 · service_unavailable"), "sets failed");
  await tick(30);
  assert.equal(search(stepped), "?step=3", "failed reads do not rewrite the step");
  await stepped.cleanup();
}

const { detectionToc } = await import("../src/features/detection/detectionToc.ts");
/** 목차 영역 id → 화면 영역 제목(없으면 null). 영역 제목은 목차 이름과 같아야 한다. */
const tocSections = (app, menu) => detectionToc[menu].map(({ id }) => {
  const card = app.renderer.root.findAll((node) => node.type === "div" && node.props.id === id && node.props["data-slot"] === "section-card")[0];
  return [id, card ? text(card.findByType("h2").children) : null];
});
// 목록: 두 탭, 잘못된 탭 정규화, 상태·추천 표시, 커서 더 보기.
{
  const app = await mount("/detection/evaluations");
  await wait(() => app.rows().length > 0, "current rows");
  assert.equal(app.rows().filter((row) => row.findAll((node) => node.type === "a").length).length, 3, "current tab hides failed and old comparisons");
  assert.deepEqual(tocSections(app, "evaluations"), [["evaluations-list", "비교 평가"], ["evaluations-new", "새 비교 평가"]], "TOC targets carry the area titles");
  const shown = app.shown();
  assert.ok(shown.includes("진행 중") && shown.includes("완료 · 일부 실패"));
  assert.ok(shown.includes(`교체 추천 ${short("7be19a34")} · 실험 7 · 현재 추천`));
  assert.ok(shown.includes("eif · 해당 없음"));
  const noneText = rowOf(app, C_LATEST).findAll((node) => node.type === "span" && text(node.children) === "eif · 해당 없음")[0];
  assert.equal(noneText.props["data-slot"], undefined, "no recommendation is plain text, not a badge");
  assert.ok(shown.includes("3개 (기준 운영 4d2f7c10 · V4)"));
  assert.equal(app.link("현재 운영 모델 기준").props["aria-current"], "page");
  // 비교 행 → 평가 상세: 뒤로가기가 비교 평가 영역으로 돌아온다(탭 쿼리 유지)
  assert.equal(linkProps(app, short(C_LATEST)).state.returnTo, "/detection/evaluations#evaluations-list");
  await app.go("전체 평가");
  await wait(() => app.shown().includes("이미 적용됨"), "all rows");
  assert.equal(search(app), "?tab=all");
  assert.equal(linkProps(app, short(C_LATEST)).state.returnTo, "/detection/evaluations?tab=all#evaluations-list");
  assert.ok(rowText(app, C_FAILED).includes("실패") && !rowText(app, C_FAILED).includes("일부 실패"), "failed comparison row");
  assert.ok(rowText(app, C_LATEST).includes("완료 · 일부 실패"));
  assert.ok(!app.shown().includes(short(C_ESB_OLD)), "second page not loaded");
  await app.click("더 보기");
  await wait(() => app.shown().includes(short(C_ESB_OLD)), "more loaded");
  assert.ok(app.shown().includes("지난 추천"));
  assert.equal(app.button("더 보기"), undefined, "last page");
  assert.ok(!/리비전|\br\d+\b/.test(app.shown()), "version wording");
  await app.cleanup();

  const invalid = await mount("/detection/evaluations?tab=wrong");
  await wait(() => !search(invalid).includes("wrong"), "tab normalized");
  await invalid.cleanup();

  const saved = sample.comparisons.splice(0);
  const empty = await mount("/detection/evaluations");
  await wait(() => empty.shown().includes("현재 운영 모델 기준 평가가 없습니다."), "empty current");
  await empty.go("전체 평가");
  await wait(() => empty.shown().includes("비교 평가가 없습니다."), "empty all");
  await empty.cleanup();
  sample.comparisons.push(...saved);
}

// 진행 중 평가가 있어 5초마다 다시 읽어도 `더 보기`로 읽은 행이 남는다. 주기만 줄여 시간을 앞당긴다(가짜 간격).
{
  const realSetInterval = globalThis.setInterval;
  globalThis.setInterval = (fn, ms, ...args) => realSetInterval(fn, ms === RUNNING_POLL_MS ? 20 : ms, ...args);
  const saved = [...sample.comparisons];
  const replaceRow = (id, patch) => {
    const index = sample.comparisons.findIndex((row) => row.comparisonId === id);
    sample.comparisons[index] = { ...sample.comparisons[index], ...patch };
  };

  const app = await mount("/detection/evaluations?tab=all");
  await wait(() => app.button("더 보기"), "first page");
  await app.click("더 보기");
  await wait(() => rowOf(app, C_ESB_OLD), "second page");
  await tick(120);
  assert.ok(rowOf(app, C_ESB_OLD), "loaded row survives polling");
  assert.equal(app.button("더 보기"), undefined, "cursor survives polling");
  assert.equal(app.rows().filter((row) => row.findAll((node) => node.type === "a").length).length, 6);
  replaceRow(C_ESB_OLD, { itemCount: 7 });
  await wait(() => rowText(app, C_ESB_OLD).includes("7개"), "polling refreshes the loaded page too");
  replaceRow(C_RUNNING, { status: "succeeded", completedAt: "2026-11-05T03:00:00Z" });
  await wait(() => !app.shown().includes("진행 중"), "running finished");
  replaceRow(C_ESB_OLD, { itemCount: 9 });
  await tick(120);
  assert.ok(rowText(app, C_ESB_OLD).includes("7개"), "polling stops once nothing is running");
  await app.cleanup();

  // 진행 중 항목이 뒤쪽 쪽에만 있어도 상태가 갱신된다.
  sample.comparisons.splice(0, sample.comparisons.length, ...saved.slice(1), saved[0]);
  const later = await mount("/detection/evaluations?tab=all");
  await wait(() => later.button("더 보기"), "first page");
  assert.ok(!later.shown().includes("진행 중"));
  await later.click("더 보기");
  await wait(() => rowText(later, C_RUNNING).includes("진행 중"), "running row on the later page");
  replaceRow(C_RUNNING, { status: "succeeded", completedAt: "2026-11-05T03:00:00Z" });
  await wait(() => rowText(later, C_RUNNING).includes("완료"), "later running row refreshed");
  await later.cleanup();

  sample.comparisons.splice(0, sample.comparisons.length, ...saved);
  globalThis.setInterval = realSetInterval;
}

// 새 비교 평가: 앞 단계 없는 주소 정규화, 세트 선택, 운영 버전 자동 포함, 요청 확인.
{
  const app = await mount("/detection/evaluations?step=3");
  await wait(() => search(app) === "?step=1", "step normalized without draft");
  await wait(() => app.shown().includes("평가 세트 선택"), "set step");
  const radio = (id) => app.renderer.root.findAllByType(RadioGroupItem).find((node) => node.props.value === id);
  assert.equal(radio(SET_QUEUED).props.disabled, true, "queued set cannot be picked");
  assert.equal(radio(SET_FAILED).props.disabled, true, "failed set cannot be picked");
  assert.equal(radio(SET_ESB).props.disabled, false);
  assert.equal(app.button("다음 · 비교 모델").props.disabled, true);
  assert.ok(app.shown().includes("P02, P04 · 강도 2") && app.shown().includes("2,000행 (200)") && app.shown().includes("seed 42"));
  assert.ok(app.shown().includes("원본 분할만"));

  await pickSet(app, SET_ESB);
  await app.go("다음 · 비교 모델");
  await wait(() => app.shown().includes("비교 모델 · 현재 운영 포함 최대 8개"), "target step");
  assert.equal(search(app), "?step=2");
  assert.ok(app.shown().includes("평가 세트: ESB 합성 E1 · 주입 PROCESS 단위"));
  assert.equal(app.button("다음 · 요청").props.disabled, true);
  assert.equal(box(app, "c5e07b42 · EIF 실험 2 선택").props.disabled, true, "not executable");
  assert.equal(box(app, "4d2f7c10 · V4 현재 운영").props.checked, false);
  assert.ok(app.shown().includes("선택 0 / 8"));

  await pickVersion(app, "7be19a34 · 실험 7 선택");
  assert.equal(box(app, "4d2f7c10 · V4 현재 운영").props.checked, true, "same modelKey active joins");
  assert.ok(app.shown().includes("현재 운영 · 항상 포함") && app.shown().includes("선택 2 / 8"));
  await app.go("다음 · 요청");
  await wait(() => app.shown().includes("요청 확인"), "request step");
  assert.ok(app.shown().includes("4d2f7c10 · V4(현재 운영), 7be19a34 · 실험 7"));
  assert.ok(app.shown().includes("운영 룰 버전 13 고정") && app.shown().includes("2개"));
  assert.ok(!app.shown().includes("확인 필요 · 비교 평가 요청"), "connected");

  // 서버 거절은 코드 그대로 보이고 초안이 남는다. 본문은 고른 후보만(운영 버전은 서버가 넣는다).
  fake.posts.compare = () => json({ code: "evaluation_set_not_ready" }, 409);
  await app.click("비교 평가 요청");
  await wait(() => app.shown().includes("비교 평가 요청 실패 · evaluation_set_not_ready"), "request failure shown");
  assert.equal(app.router.state.location.pathname, "/detection/evaluations");
  assert.deepEqual(fake.requests.filter((call) => call.method === "POST").at(-1).body,
    { kind: "compare", sourceId: sample.evaluationSets[0].sourceId, evaluationSetId: SET_ESB, configVersionIds: ["7be19a34-0c5d-4f82-b3a1-92d6e4f07c18"] });
  assert.deepEqual(useDraftStore.getState().drafts["/detection/evaluations"].value.versionIds, ["7be19a34-0c5d-4f82-b3a1-92d6e4f07c18"]);
  await app.cleanup();

  // 초안이 있으면 같은 단계로 돌아온다. 접수되면 초안을 비우고 작업 ID로 평가 상세를 연다(완료 전 상태를 다시 읽음).
  const back = await mount("/detection/evaluations?step=3", { keepDrafts: true });
  await wait(() => back.shown().includes("요청 확인"), "draft restored");
  const runningJob = sample.comparisons[0].jobId;
  fake.posts.compare = () => json({ id: runningJob, status: "queued", kind: "compare", inputSpec: {} }, 201);
  await back.click("비교 평가 요청");
  await wait(() => back.router.state.location.pathname === `/detection/evaluations/${runningJob}`, "opens the comparison by job id");
  await wait(() => back.shown().includes("진행 중"), "pending comparison shown");
  assert.equal(useDraftStore.getState().drafts["/detection/evaluations"], undefined, "draft discarded");
  await back.cleanup();
  draftModule.writeCompareDraft((current) => ({ ...current, setId: SET_FAILED, versionIds: [] }));
  const stale = await mount("/detection/evaluations?step=2", { keepDrafts: true });
  await wait(() => search(stale) === "?step=1", "unusable set");
  await stale.cleanup();
}

// 상한·요청 본문: 활성은 같은 종류일 때만 세고, 요청에는 고른 후보만 간다(활성은 서버가 넣는다).
{
  const version = (id, modelKey, active = false) => ({ id, displayName: null, modelKey, modelName: modelKey, active, executable: true, latestEvaluation: null });
  const versions = [version("a", "iforest", true), version("b", "eif", true), version("c", "iforest"), { ...version("d", "iforest"), executable: false }];
  const picked = draftModule.pickedVersions(versions, { setId: "s", versionIds: ["a", "c", "d"] });
  assert.deepEqual(picked.map((item) => item.id), ["c"]);
  assert.deepEqual(draftModule.includedActive(versions, picked).map((item) => item.id), ["a"]);
  assert.deepEqual(draftModule.compareRequest({ id: "s", sourceId: "src" }, picked), { kind: "compare", sourceId: "src", evaluationSetId: "s", configVersionIds: ["c"] });

  const app = await mount("/detection/evaluations?step=2");
  await wait(() => app.shown().includes("평가 세트 선택"), "set step");
  await pickSet(app, SET_ESB);
  await app.go("다음 · 비교 모델");
  await wait(() => app.shown().includes("선택 0 / 8"), "target");
  const extra = Array.from({ length: 8 }, (_, index) => ({ ...version(`x${index}`, "iforest"), displayName: `추가 ${index}` }));
  sample.compareVersions.push(...extra);
  await app.cleanup();
  draftModule.writeCompareDraft((current) => ({ ...current, setId: SET_ESB, versionIds: extra.map((item) => item.id) }));
  const over = await mount("/detection/evaluations?step=3", { keepDrafts: true });
  await wait(() => search(over) === "?step=2", "over limit stays on step 2");
  await wait(() => over.shown().includes("현재 운영 포함 최대 8개까지 비교합니다."), "limit message");
  assert.ok(over.shown().includes("선택 9 / 8"));
  assert.equal(over.button("다음 · 요청").props.disabled, true);
  await over.cleanup();
  sample.compareVersions.splice(-8);
}

// 새 평가 세트 생성: 원본 분할만(합성 v1 미지원), 기준 저장 데이터셋 기본값, 단계 이동·이탈 확인, 접수.
{
  const app = await mount("/detection/evaluations");
  await wait(() => app.button("새 평가 세트 생성"), "set table");
  await app.click("새 평가 세트 생성");
  await wait(() => fieldOf(app, "set-name"), "form");
  await wait(() => app.shown().includes("4d2f7c10 · V4, 7be19a34 · 실험 7 학습 데이터"), "snapshot options");
  assert.ok(app.shown().includes("기준 저장 데이터셋") && app.shown().includes("2a91e6d7 · V3 학습 데이터"));
  assert.equal(fieldOf(app, "set-snapshot").props.value, sample.snapshots[0].id, "active version training snapshot by default");
  assert.equal(fieldOf(app, "set-profile").props.value, "original");
  assert.ok(app.shown().includes("검증 분할 · 합성 없음"));
  assert.equal(app.button("평가 세트 생성").props.disabled, false, "original split can be created");
  assert.equal(stored().dirty, false, "opening the form is not an edit");

  await change(app, "set-name", "세트 A");
  await change(app, "set-snapshot", sample.snapshots[1].id);
  await change(app, "set-profile", "esb");
  assert.ok(app.shown().includes("확인 필요 · 평가 세트 생성: synthetic_set_unsupported"));
  assert.equal(app.button("평가 세트 생성").props.disabled, true, "synthetic sets are not supported in v1");
  await change(app, "set-repetitions", "4");
  assert.equal(stored().dirty, true, "input makes the draft dirty");

  // 단계를 오가도 입력이 남는다.
  await pickSet(app, SET_ESB);
  await app.go("다음 · 비교 모델");
  await wait(() => app.shown().includes("선택 0 / 8"), "target step");
  assert.equal(fieldOf(app, "set-name"), undefined);
  await app.go("이전");
  await wait(() => fieldOf(app, "set-name"), "form back");
  assert.equal(fieldOf(app, "set-name").props.value, "세트 A");
  assert.equal(fieldOf(app, "set-profile").props.value, "esb");
  assert.equal(fieldOf(app, "set-snapshot").props.value, sample.snapshots[1].id);
  assert.equal(fieldOf(app, "set-repetitions").props.value, "4");

  await change(app, "set-profile", "adbench");
  assert.ok(app.shown().includes("local · 국소 밀도 이탈"));
  assert.equal(fieldOf(app, "set-severity").props.disabled, true);
  const firstScenario = app.renderer.root.findAllByType(Checkbox)[0];
  await act(async () => firstScenario.props.onCheckedChange(false));
  assert.ok(app.shown().includes("시나리오를 1개 이상 선택해 주세요."));

  // 닫아도 입력은 남고, 다시 열면 그대로다.
  await app.click("닫기");
  assert.ok(!app.shown().includes("합성 이상 생성기"));
  await app.click("새 평가 세트 생성");
  await wait(() => fieldOf(app, "set-name"), "reopened");
  assert.equal(fieldOf(app, "set-name").props.value, "세트 A");

  // 서버 거절은 코드 그대로, 접수되면 세트가 대기로 목록에 들어가고 초안이 그 세트를 고른다.
  await change(app, "set-profile", "original");
  fake.posts.evaluationSet = () => json({ code: "snapshot_not_ready" }, 409);
  await app.click("평가 세트 생성");
  await wait(() => app.shown().includes("평가 세트 생성 실패 · snapshot_not_ready"), "set request failure");
  const NEW_SET = "0f0e0d0c-0b0a-4900-8800-777766665555";
  fake.posts.evaluationSet = (body) => {
    sample.evaluationSets.unshift({ id: NEW_SET, sourceId: body.sourceId, snapshotId: body.snapshotId, purpose: "validation", displayName: body.displayName, status: "queued", synthetic: null, generation: null, createdAt: "2026-11-13T02:00:00Z", readyAt: null });
    return json({ id: NEW_SET, jobId: "0f0e0d0c-0b0a-4900-8800-777766664444", status: "queued" }, 201);
  };
  await app.click("평가 세트 생성");
  await wait(() => stored()?.value.setId === NEW_SET, "draft picks the new set");
  assert.deepEqual(fake.requests.filter((call) => call.method === "POST").at(-1).body,
    { sourceId: sample.snapshots[1].sourceId, snapshotId: sample.snapshots[1].id, purpose: "validation", displayName: "세트 A", synthetic: null });
  await wait(() => app.shown().includes(`세트 A대기${short(NEW_SET)}`), "queued set listed");
  assert.equal(fieldOf(app, "set-name"), undefined, "form closed");

  // 이탈 확인(고른 세트가 초안에 있다).
  await act(async () => { await app.router.navigate("/detection/rules"); });
  await wait(() => openDialog(app), "leave confirmation");
  assert.equal(app.router.state.location.pathname, "/detection/evaluations");
  await app.cleanup();
  sample.evaluationSets.shift();

  // 세트 입력만 있어도 확인이 뜬다.
  const only = await mount("/detection/evaluations");
  await wait(() => only.button("새 평가 세트 생성"), "set table");
  await only.click("새 평가 세트 생성");
  await wait(() => fieldOf(only, "set-name"), "form");
  await change(only, "set-name", "세트 B");
  await act(async () => { await only.router.navigate("/detection/rules"); });
  await wait(() => openDialog(only), "leave confirmation for form input");
  await only.cleanup();

  // 입력 없이 연 상태는 확인 없이 나간다.
  const clean = await mount("/detection/evaluations");
  await wait(() => clean.button("새 평가 세트 생성"), "set table");
  await clean.click("새 평가 세트 생성");
  await act(async () => { await clean.router.navigate("/detection/rules"); });
  await wait(() => clean.router.state.location.pathname === "/detection/rules", "left freely");
  await clean.cleanup();

  // 저장 데이터셋 조회 실패는 양식 안에 그대로 보인다.
  const failed = await mount("/detection/evaluations");
  await wait(() => failed.button("새 평가 세트 생성"), "set table");
  fake.up = false;
  await failed.click("새 평가 세트 생성");
  await wait(() => failed.shown().includes("저장 데이터셋 조회 실패 · service_unavailable"), "snapshot failure");
  assert.equal(fieldOf(failed, "set-snapshot").props.disabled, true);
  await failed.cleanup();
}

// 상세: ESB 결과(정의 불가, 항목 실패, 추천 두 건), 지표는 서버 값 그대로.
{
  const app = await mount(`/detection/evaluations/${C_LATEST}`);
  await wait(() => app.shown().includes("비교 평가 b84e6d15"), "detail");
  await wait(() => app.shown().includes("ESB 합성 E1"), "set joined");
  await wait(() => app.shown().includes("4d2f7c10 · V4 · 현재 운영"), "baseline");
  const shown = app.shown();
  assert.ok(shown.includes("2,000행 · 200행") && shown.includes("운영 룰 버전 13 고정 · 모든 모델에 같은 룰"));
  assert.ok(shown.includes("모델 교체 추천 · 해당 없음 · EIF 계열은 현재 운영 중인 버전이 없어 추천하지 않습니다"));
  assert.ok(shown.includes("후보 c5e07b42 · EIF 실험 2 · item_failed:method_not_executable:ml:eif"), "excluded candidate shows the server code");
  assert.ok(shown.includes("행당 처리 시간 P95 41 ms · 한도 76 ms") && !shown.includes("한도 76 ms · 미측정"), "cost within the limit");
  assert.ok(shown.includes("후보 7be19a34 · 실험 7 · selected"));
  assert.ok(shown.includes("모델 교체 추천 4d2f7c10 · V4 → 7be19a34 · 실험 7 · 시스템 추천 · 자동 교체 안 함"));
  assert.ok(shown.includes("실험 7이 현재 운영 V4보다 모델 재현율 12/18(0.667) > 9/18(0.500)"));
  assert.equal(app.link("수락하고 교체").props.href, "/detection/operations/replace");
  assert.deepEqual(linkProps(app, "수락하고 교체").state, {
    returnTo: `/detection/evaluations/${C_LATEST}`,
    acceptedRecommendation: { comparisonJobId: sample.comparisons[1].jobId, modelKey: "iforest", targetVersionId: "7be19a34-0c5d-4f82-b3a1-92d6e4f07c18" },
  });
  assert.deepEqual(linkProps(app, sample.comparisons[1].jobId.slice(0, 8)).state, { returnTo: `/detection/evaluations/${C_LATEST}` });
  assert.ok(shown.includes("상태: measured / unavailable / not_applicable / failed"));
  assert.ok(shown.includes("주입 이상 18") && shown.includes("비주입 원본(라벨 없음)") && shown.includes("FP 정의 불가") && shown.includes("TN 정의 불가"));
  assert.ok(shown.includes("50% (커버리지 90%) 완료 기준"));
  assert.ok(shown.includes("정의 불가 · 정상 확정 라벨 없음"));
  assert.ok(shown.includes("평가 실패 · method_not_executable:ml:eif · 결과 없음"));
  assert.ok(shown.includes("실패 · 결과 없음"));
  assert.ok(shown.includes("6 / 3 / 4 / 1,175 · 비교 가능 1,188"));
  assert.ok(shown.includes("38 ms") && shown.includes("212 행/s") && shown.includes("미측정 · 기간을 지정한 별도 운영 보고에서 조회"));
  assert.ok(shown.includes("룰과 함께 판정 · 운영 룰 버전 13 고정"));
  // 룰+모델 두 기준(Trainer #206 composite.decidable): 값이 다른 지표는 두 행, 모든 항목이 같은 지표는 한 행으로 합친다.
  assert.ok(shown.includes("재현율 · 룰+모델 · 완료 기준55.56% (커버리지 90%)65% (커버리지 100%)실패 · 결과 없음재현율 · 룰+모델 · 판정 가능 기준57.89% (커버리지 95%)65% (커버리지 100%)실패 · 결과 없음"), "composite recall on both bases");
  assert.ok(shown.includes("오탐률 · 룰+모델 · 완료 기준 · 판정 가능 기준정의 불가정의 불가실패 · 결과 없음"), "equal composite rows merge");
  assert.ok(!shown.includes("오탐률 · 룰+모델 · 판정 가능 기준"));
  assert.ok(shown.includes("경보 · 룰+모델 · 완료 기준31건29건실패 · 결과 없음경보 · 룰+모델 · 판정 가능 기준33건29건실패 · 결과 없음"), "composite alerts on both bases");
  assert.ok(shown.includes("룰·머신러닝 모델 비교 · 둘 다 이상 / 룰만 / 모델만 / 둘 다 정상"), "rule/model cross table shown with the envelope");
  // 모델 단독: 다른 지표는 두 행, 같은 지표(정밀도·오탐률)는 한 행. 카드는 같은 값을 한 번만 쓴다.
  assert.ok(shown.includes("재현율 · 완료 기준50% (커버리지 90%)66.67% (커버리지 100%)실패 · 결과 없음재현율 · 판정 가능 기준52.63% (커버리지 95%)66.67% (커버리지 100%)실패 · 결과 없음"));
  assert.ok(shown.includes("정밀도 · 완료 기준 · 판정 가능 기준정의 불가정의 불가실패 · 결과 없음") && !shown.includes("정밀도 · 판정 가능 기준"));
  assert.ok(shown.includes("50% (커버리지 90%) 완료 기준 · 52.63% (커버리지 95%) 판정 가능 기준"), "card keeps both values when they differ");
  assert.ok(shown.includes("66.67% (커버리지 100%) 완료 기준 · 판정 가능 기준"), "card writes an equal value once");
  assert.ok(shown.includes("판정 실패 행완료 기준 12행 · 판정 가능 기준 6행") && shown.includes("판정 실패 행완료 기준 · 판정 가능 기준 0행"));
  // 지표 비교 · 모델 단독의 ml.unavailableCount 행: 카드와 같은 이름·단위(사용자 11/14).
  assert.ok(shown.includes("판정 실패 행12행0행"), "metric table names ml.unavailableCount as failed judgment rows");
  assert.ok(!shown.includes("모델 미판정 행") && !shown.includes("12건"), "old row name and unit are gone");
  assert.ok(!/리비전|\br\d+\b/.test(shown), "version wording");
  await app.cleanup();
}

// 상세: 진행 중·실패·없는 평가·적용된 추천·지난 추천.
{
  const running = await mount(`/detection/evaluations/${C_RUNNING}`);
  await wait(() => running.shown().includes("진행 중"), "running");
  assert.ok(!running.shown().includes("지표 비교"), "no metrics while running");
  await running.cleanup();

  const failed = await mount(`/detection/evaluations/${C_FAILED}`);
  await wait(() => failed.shown().includes("비교 평가 실패 · comparison_all_items_failed · 결과가 없으므로 지표를 표시하지 않습니다."), "failed");
  await wait(() => failed.shown().includes("63c8d0f2 · 실험 6 실패 · training_evaluation_overlap"), "each failed item's code");
  assert.ok(!failed.shown().includes("지표 비교"));
  await failed.cleanup();

  const missing = await mount("/detection/evaluations/not-a-comparison");
  await wait(() => missing.shown().includes("평가 조회 실패 · invalid_comparison_id"), "invalid id");
  await missing.cleanup();

  // v1 서버: 교체 추천 없음은 빈 성공이 아니라 미연결 + 사유 코드. 지표는 모델 단독만.
  const v1 = await mount(`/detection/evaluations/${sample.C_V1}`);
  await wait(() => v1.shown().includes("모델 교체 추천 미연결 · not_implemented_v1"), "recommendations not implemented");
  assert.equal(v1.link("수락하고 교체"), undefined);
  assert.ok(v1.shown().includes("재현율70% 완료 기준정밀도"), "model metrics shown without coverage or decidable basis");
  assert.ok(!v1.shown().includes("판정 가능 기준"), "no decidable rows or sentence when the server has none");
  assert.ok(v1.shown().includes("판정 실패 행완료 기준 0행"), "ml.unavailableCount is named as failed judgments");
  // 예전 결과(11/14 필드 없음): 새 줄·표를 숨긴다(오류 아님).
  for (const hidden of ["평가 제외 행", "이상 판정 건수", "이상 판정 비율", "이상 점수", "행당 처리 시간", "버전 간 판정 일치", "정확도가 아니라"]) assert.ok(!v1.shown().includes(hidden), `${hidden} hidden for old results`);
  assert.ok(v1.shown().includes("정밀도미측정 · no_predicted_anomalies오탐률"), "other null reasons show the code");
  assert.equal(v1.shown().split("정상 확정 라벨 없음").length - 1, 1, "missing-normal-label wording only where that is the reason");
  assert.ok(v1.shown().includes("평가 정책구성 정책 · ml_only구성 정책 · ml_only"), "each item's evaluation policy");
  assert.ok(v1.shown().includes("운영 룰 버전 13 고정 · 항목별 평가 정책"), "v1 records the rule set version, items use their own policy");
  assert.ok(!v1.shown().includes("룰과 함께 판정") && !v1.shown().includes("기타 지표"), "no empty rule/cost tables for v1");
  await v1.cleanup();

  // 11/14 결과: 평가 제외 행(사유 이름)·판정 실패 행 두 줄, 무라벨 지표·행당 처리 시간, 버전 간 판정 일치(쌍마다, 8자 ID).
  const v2 = await mount(`/detection/evaluations/${sample.C_V2}`);
  await wait(() => v2.shown().includes("버전 간 판정 일치"), "pairwise agreement section");
  await wait(() => v2.shown().includes("7be19a34 · 실험 7"), "versions joined");
  const v2Shown = v2.shown();
  assert.ok(v2Shown.includes("판정 실패 행완료 기준 0행평가 제외 행3행 · 입력 변수 계산 불가(예: 처리 건수 0) 3행"), "excluded rows and failed judgments are two lines");
  assert.ok(v2Shown.includes("평가 제외 행0행이 모델로 교체"), "excluded rows right above the replace button, 0 shown as 0");
  assert.ok(v2Shown.includes("이상 판정 건수34건이상 판정 비율2.84% (커버리지 100%)"));
  assert.ok(v2Shown.includes("이상 점수 중앙값 / 95분위 / 99분위0.518 / 0.565 / 0.571"));
  assert.ok(v2Shown.includes("행당 처리 시간 중앙값15 ms행당 처리 시간 P9516 ms"));
  // 전 행 제외 항목: 미측정이어도 제외 행 수와 사유 코드를 보인다.
  assert.ok(v2Shown.includes("미측정 · unavailable이상 판정 건수미측정 · ml_results_unavailable이상 판정 비율미측정 · ml_results_unavailable이상 점수 중앙값 / 95분위 / 99분위미측정 · ml_results_unavailable"));
  assert.ok(v2Shown.includes("행당 처리 시간 중앙값미측정 · no_rows_measured") && v2Shown.includes("평가 제외 행1,200행 · 입력 변수 계산 불가(예: 처리 건수 0) 1,200행"));
  assert.ok(v2Shown.includes("버전 간 판정 일치정확도가 아니라 두 버전의 판정이 얼마나 같은지입니다"));
  const pairs = v2.renderer.root.findAll((node) => node.type === "table" && node.props["data-agreement"]);
  assert.equal(pairs.length, 2, "one table per pair");
  assert.equal(text(pairs[0].children), "둘 다 이상4d2f7c10만 이상7be19a34만 이상둘 다 정상판정 일치율30441,15999.33% (커버리지 99.75%)");
  assert.ok(text(pairs[1].children).includes("63c8d0f2만 이상") && text(pairs[1].children).includes("미측정 · no_comparable_rows"));
  assert.ok(!/리비전|r\d+/.test(v2Shown), "version wording");
  await v2.cleanup();

  // 두 기준이 모든 항목에서 같으면(ml.decidable single_method_equals_complete_only·같은 composite) 지표마다 한 행.
  const same = await mount(`/detection/evaluations/${sample.comparisons[2].comparisonId}`);
  await wait(() => same.shown().includes("룰과 함께 판정") && same.shown().includes("4d2f7c10 · V4"), "same-basis detail");
  const sameShown = same.shown();
  for (const label of ["재현율 · 룰+모델", "오탐률 · 룰+모델", "경보 · 룰+모델", "재현율", "정밀도", "오탐률"]) {
    assert.ok(sameShown.includes(`${label} · 완료 기준 · 판정 가능 기준`), `${label} merged`);
    assert.ok(!sameShown.includes(`${label} · 판정 가능 기준`), `${label} has no separate decidable row`);
  }
  assert.ok(sameShown.includes("재현율 · 룰+모델 · 완료 기준 · 판정 가능 기준75% (커버리지 100%)70% (커버리지 100%)"));
  assert.ok(sameShown.includes("경보 · 룰+모델 · 완료 기준 · 판정 가능 기준22건23건"));
  await same.cleanup();

  // 룰+모델 종합은 있고 evaluationEnvelope가 없는 결과: 룰·모델 교차표 행과 기타 지표를 그리지 않는다(Trainer Q7 합의).
  const noEnvelope = await mount(`/detection/evaluations/${sample.C_V3}`);
  await wait(() => noEnvelope.shown().includes("룰과 함께 판정 · 운영 룰 버전 13 고정") && noEnvelope.shown().includes("7be19a34 · 실험 7"), "composite without envelope");
  const noEnvelopeShown = noEnvelope.shown();
  assert.ok(noEnvelopeShown.includes("재현율 · 룰+모델 · 완료 기준45% (커버리지 90%)70% (커버리지 90%)재현율 · 룰+모델 · 판정 가능 기준50% (커버리지 95%)75% (커버리지 95%)"));
  assert.ok(noEnvelopeShown.includes("오탐률 · 룰+모델 · 완료 기준 · 판정 가능 기준정의 불가정의 불가"));
  assert.ok(noEnvelopeShown.includes("경보 · 룰+모델 · 완료 기준40건44건경보 · 룰+모델 · 판정 가능 기준42건46건"));
  assert.ok(!noEnvelopeShown.includes("룰·머신러닝 모델 비교") && !noEnvelopeShown.includes("기타 지표"), "no cross-table row or cost table without the envelope");
  assert.ok(!noEnvelopeShown.includes("미측정 · "), "no unmeasured placeholder rows");
  assert.ok(!noEnvelopeShown.includes("재현율 · 판정 가능 기준") && noEnvelopeShown.includes("재현율 · 완료 기준"), "model-only table without ml.decidable keeps one basis");
  await noEnvelope.cleanup();

  const applied = await mount(`/detection/evaluations/${C_ADBENCH}`);
  await wait(() => applied.shown().includes("이미 적용됨"), "applied");
  await wait(() => applied.shown().includes("ADBench 합성 A1 · ADBench 수치 합성(합성 샘플 단위)"), "kind");
  assert.ok(applied.shown().includes("이 추천은 적용되었습니다."));
  assert.equal(applied.link("수락하고 교체"), undefined);
  assert.ok(applied.shown().includes("FP 54") && applied.shown().includes("합성 정상 2,700"));
  assert.ok(applied.shown().includes("평가 당시 운영"));
  assert.ok(applied.shown().includes("미측정 · ADBench 행에는 룰 입력 필드가 없음"));
  await applied.cleanup();

  const stale = await mount(`/detection/evaluations/${C_ESB_OLD}`);
  await wait(() => stale.shown().includes("지난 추천"), "stale");
  assert.equal(stale.button("수락하고 교체").props.disabled, true);
  assert.equal(stale.link("+ 새 비교 평가").props.href, "/detection/evaluations?step=1");
  await stale.cleanup();
}

// 교체 추천 분류 4종(11/15): 목록 칸·상세 이름표, 후보별 제외 사유 코드, 비용 미측정, 수락은 교체 추천만. 예전 결과는 기존 표시.
{
  const saved = sample.comparisons.splice(0);
  const latestRow = saved.find((row) => row.comparisonId === C_LATEST);
  const draftShape = { ...latestRow, comparisonId: "1e2d3c4b-5a69-4788-9a0b-c1d2e3f4a5b6", recommendations: latestRow.recommendations.map(({ decision, ...rest }) => rest) };
  const oldRow = { ...latestRow, comparisonId: sample.C_V1, jobId: "ab120000-1111-4222-8333-444455556666", recommendations: null, recommendationsReason: "not_implemented_v1" };
  sample.comparisons.push(sample.extraRow(sample.C_V4), sample.extraRow(sample.C_V6), saved[2], oldRow, draftShape);
  const list = await mount("/detection/evaluations?tab=all");
  await wait(() => rowOf(list, sample.C_V4) && rowText(list, sample.C_V6).includes("실험 7"), "decision rows");
  assert.equal(list.rows().filter((row) => row.findAll((node) => node.type === "a").length).length, 5);
  assert.ok(rowText(list, sample.C_V4).endsWith("근거 부족"), "insufficient_evidence");
  assert.ok(rowText(list, sample.C_V6).endsWith(`교체 추천 ${short("7be19a34")} · 실험 7 · 현재 추천`), "recommend_replace");
  assert.ok(rowText(list, saved[2].comparisonId).endsWith("현재 유지"), "keep_current");
  assert.ok(rowText(list, sample.C_V1).endsWith("미연결 · not_implemented_v1"), "old result keeps the unconnected cell");
  const draftText = rowText(list, draftShape.comparisonId);
  assert.ok(draftText.includes(`iforest · 추천 ${short("7be19a34")} · 실험 7 · 현재 추천`) && draftText.includes("eif · 추천 없음"), "no decision keeps the previous wording");
  await list.cleanup();
  sample.comparisons.splice(0, sample.comparisons.length, ...saved);

  const keep = await mount(`/detection/evaluations/${saved[2].comparisonId}`);
  await wait(() => keep.shown().includes("모델 교체 추천 · 현재 유지 · 현재 운영 버전보다 모든 근거 지표에서 같거나 나은 후보가 없습니다."), "keep_current label + stored summary");
  await wait(() => keep.shown().includes("후보 63c8d0f2 · 실험 6 · worse_on:ml.completeOnly.recall"), "keep_current candidate");
  assert.equal(keep.link("수락하고 교체"), undefined, "no accept without a recommendation");
  await keep.cleanup();

  const weak = await mount(`/detection/evaluations/${sample.C_V4}`);
  await wait(() => weak.shown().includes("모델 교체 추천 · 근거 부족 · 실행 가능하고 판정 불가 행이 없는 후보가 없습니다."), "insufficient_evidence");
  await wait(() => weak.shown().includes("후보 7be19a34 · 실험 7 · common_rows_empty"), "versions joined");
  assert.ok(weak.shown().includes("후보 63c8d0f2 · 실험 6 · item_failed:training_evaluation_overlap"));
  assert.equal(weak.link("수락하고 교체"), undefined);
  assert.equal(weak.button("수락하고 교체"), undefined);
  assert.equal(weak.renderer.root.findAll((node) => node.type === "p" && node.props["data-rec-cost"]).length, 0, "no cost line without a recommendation");
  await weak.cleanup();

  const notValidation = await mount(`/detection/evaluations/${sample.C_V5}`);
  await wait(() => notValidation.shown().includes("후보 7be19a34 · 실험 7 · evaluation_set_not_validation"), "set purpose exclusion code");
  await notValidation.cleanup();

  const unmeasured = await mount(`/detection/evaluations/${sample.C_V6}`);
  await wait(() => unmeasured.shown().includes("모델 교체 추천 4d2f7c10 · V4 → 7be19a34 · 실험 7 · 시스템 추천 · 자동 교체 안 함"), "recommend_replace block");
  const u = unmeasured.shown();
  assert.ok(u.includes("실험 7이 현재 운영 V4보다 모델 재현율 14/20(0.700) > 9/20(0.450)"), "stored summary as is");
  assert.ok(u.includes("행당 처리 시간 P95 16 ms · 한도 — · 미측정 · unmeasured"), "unmeasured cost is not shown as passed");
  assert.ok(!u.includes("(한도 내)"));
  assert.ok(u.includes("후보 63c8d0f2 · 실험 6 · eligible_not_selected") && u.includes("후보 7be19a34 · 실험 7 · selected"));
  assert.deepEqual(linkProps(unmeasured, "수락하고 교체").state.acceptedRecommendation, { comparisonJobId: sample.comparisonDetail(sample.C_V6).jobId, modelKey: "iforest", targetVersionId: "7be19a34-0c5d-4f82-b3a1-92d6e4f07c18" });
  for (const hidden of ["minLabeled", "checker_agreement_not_accuracy", "latencyFloorMs"]) assert.ok(!u.includes(hidden), `${hidden} not drawn`);
  await unmeasured.cleanup();
}

console.log("evaluations page QA passed");
