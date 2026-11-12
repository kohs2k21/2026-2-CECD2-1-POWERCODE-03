import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

// 평가 결과 화면 통합(API 미연결): 운영 모드의 오류 표시, 개발 모드 예시 데이터로 목록·탭·상세·새 비교 평가 3단계. 요청은 성공으로 꾸미지 않는다.
register("./svg-loader.mjs", import.meta.url);
register("./dev-mode-loader.mjs", import.meta.url);
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
const sample = await import("../src/features/detection/evaluations/evaluationsSample.ts");

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const requested = [];
let currentUser;
globalThis.fetch = async (url, options = {}) => {
  const target = new URL(String(url), "http://local.test");
  requested.push({ path: target.pathname, method: options.method ?? "GET" });
  return target.pathname === "/api/auth/me" ? json({ user: currentUser }) : json({ code: "unavailable" }, 503);
};
const rawText = (node) => (!node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(rawText).join("") : rawText(node.children));
const text = (node) => rawText(node).replace(/\s+/g, " ").trim();
const tick = (ms = 10) => act(async () => new Promise((resolve) => setTimeout(resolve, ms)));
const wait = async (check, label, tries = 150) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const mount = async (path, { dev = true, keepDrafts = false, userType = "admin" } = {}) => {
  globalThis.__EVALUATIONS_DEV__ = dev;
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

// 운영 모드: 예시 데이터 없이 오류 상태만. 서버에도 요청하지 않는다(연결 전).
{
  requested.length = 0;
  const app = await mount("/detection/evaluations", { dev: false });
  await wait(() => app.shown().includes("평가 조회 실패 · service_unavailable"), "production list fails");
  await wait(() => app.shown().includes("평가 세트 조회 실패 · service_unavailable"), "production sets fail");
  assert.ok(app.shown().includes("모델 버전 조회 실패 · service_unavailable"));
  assert.ok(!app.shown().includes("ESB 합성"), "no sample in production");
  assert.deepEqual(requested.filter((call) => call.path !== "/api/auth/me" && /comparison|evaluation/.test(call.path)), []);
  await app.cleanup();
  const detail = await mount(`/detection/evaluations/${C_LATEST}`, { dev: false });
  await wait(() => detail.shown().includes("평가 조회 실패 · service_unavailable"), "production detail fails");
  assert.ok(!detail.shown().includes("모델 교체 추천"));
  await detail.cleanup();
  await assert.rejects(api.requestComparison({}), (error) => error.code === "service_unavailable");
  await assert.rejects(api.readSnapshots(), (error) => error.code === "service_unavailable");
  const stepped = await mount("/detection/evaluations?step=3", { dev: false });
  await wait(() => stepped.shown().includes("평가 세트 조회 실패 · service_unavailable"), "sets failed");
  await tick(30);
  assert.equal(search(stepped), "?step=3", "failed reads do not rewrite the step");
  await stepped.cleanup();
}

// 목록: 두 탭, 잘못된 탭 정규화, 상태·추천 표시, 커서 더 보기.
{
  const app = await mount("/detection/evaluations");
  await wait(() => app.rows().length > 0, "current rows");
  assert.equal(app.rows().filter((row) => row.findAll((node) => node.type === "a").length).length, 3, "current tab hides failed and old comparisons");
  const shown = app.shown();
  assert.ok(shown.includes("진행 중") && shown.includes("완료 · 일부 실패"));
  assert.ok(shown.includes(`추천 ${short("7be19a34")} · 실험 7 · 현재 추천`));
  assert.ok(shown.includes("eif · 추천 없음"));
  const noneText = rowOf(app, C_LATEST).findAll((node) => node.type === "span" && text(node.children) === "eif · 추천 없음")[0];
  assert.equal(noneText.props["data-slot"], undefined, "no recommendation is plain text, not a badge");
  assert.ok(shown.includes("3개 (기준 운영 4d2f7c10 · V4)"));
  assert.equal(app.link("현재 운영 모델 기준").props["aria-current"], "page");
  await app.go("전체 평가");
  await wait(() => app.shown().includes("이미 적용됨"), "all rows");
  assert.equal(search(app), "?tab=all");
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
  assert.ok(app.shown().includes("확인 필요 · 비교 평가 요청: service_unavailable"));
  assert.equal(app.button("비교 평가 요청").props.disabled, true, "not connected");

  // 연결 전 요청 경로: 기능을 켜도 실패가 그대로 보이고 초안이 남는다.
  api.evaluationCapabilities.createComparison = true;
  await tick();
  await act(async () => { await app.router.navigate("/detection/evaluations?step=2"); });
  await act(async () => { await app.router.navigate("/detection/evaluations?step=3"); });
  await wait(() => app.button("비교 평가 요청")?.props.disabled === false, "enabled");
  await app.click("비교 평가 요청");
  await wait(() => app.shown().includes("비교 평가 요청 실패 · service_unavailable"), "request failure shown");
  assert.equal(app.router.state.location.pathname, "/detection/evaluations");
  assert.deepEqual(useDraftStore.getState().drafts["/detection/evaluations"].value.versionIds, ["7be19a34-0c5d-4f82-b3a1-92d6e4f07c18"]);
  api.evaluationCapabilities.createComparison = false;
  await app.cleanup();

  // 초안이 있으면 같은 단계로 돌아오고, 세트가 고를 수 없게 된 주소는 1단계로.
  const back = await mount("/detection/evaluations?step=3", { keepDrafts: true });
  await wait(() => back.shown().includes("요청 확인"), "draft restored");
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

// 새 평가 세트 생성: 입력 상한 표시, 기준 저장 데이터셋, 연결 전 생성 비활성, 단계 이동·이탈 확인.
{
  const app = await mount("/detection/evaluations");
  await wait(() => app.button("새 평가 세트 생성"), "set table");
  await app.click("새 평가 세트 생성");
  await wait(() => app.shown().includes("새 평가 세트 생성 · 합성 이상 생성기"), "form");
  assert.ok(app.shown().includes("확인 필요 · 평가 세트 생성: service_unavailable"));
  assert.equal(app.button("평가 세트 생성").props.disabled, true);
  await wait(() => app.shown().includes("고정 학습 참조 데이터"), "snapshot options");
  assert.ok(app.shown().includes("기준 저장 데이터셋") && app.shown().includes("정상성 확인 구간"));
  assert.equal(fieldOf(app, "set-snapshot").props.value, sample.snapshots[0].id, "first dataset by default");
  assert.equal(stored().dirty, false, "opening the form is not an edit");

  await change(app, "set-name", "세트 A");
  await change(app, "set-snapshot", sample.snapshots[1].id);
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

  // 이탈 확인.
  await act(async () => { await app.router.navigate("/detection/rules"); });
  await wait(() => openDialog(app), "leave confirmation");
  assert.equal(app.router.state.location.pathname, "/detection/evaluations");
  await app.cleanup();

  // 세트 입력만 있어도 확인이 뜬다.
  const only = await mount("/detection/evaluations");
  await wait(() => only.button("새 평가 세트 생성"), "set table");
  await only.click("새 평가 세트 생성");
  await wait(() => fieldOf(only, "set-seed"), "form");
  await change(only, "set-seed", "7");
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
  globalThis.__EVALUATIONS_DEV__ = false;
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
  assert.ok(shown.includes("모델 교체 추천 없음 · EIF 계열은 현재 운영 중인 버전이 없어 추천하지 않습니다"));
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
  assert.ok(!failed.shown().includes("지표 비교"));
  await failed.cleanup();

  const missing = await mount("/detection/evaluations/not-a-comparison");
  await wait(() => missing.shown().includes("평가 조회 실패 · http_404"), "missing");
  await missing.cleanup();

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

console.log("evaluations page QA passed");
