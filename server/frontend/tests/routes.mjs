import assert from "node:assert/strict";
import { register } from "node:module";
import React from "react";
import { act, create } from "react-test-renderer";

register("./svg-loader.mjs", import.meta.url);
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
};
// Radix Select/ScrollArea DOM globals (react-test-renderer has no DOM).
globalThis.HTMLFormElement = class {};
globalThis.window.HTMLSelectElement = class {
  get value() {
    return "";
  }
  set value(_value) {}
};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const routes = await import("../src/app/routes.ts");
const {
  legacyRedirect,
  resolveOption,
  resolveStep,
  withSearchParam,
  activeTopNav,
  activeDetectionMenu,
  visibleTopNavItems,
  detectionMenu,
  isAppPath,
  safeReturnPath,
} = routes;

// 1) 옛 주소 넘김 표
const legacy = [
  ["/notifications", "?filter=tasks", "/analysis"],
  ["/analysis/process-decisions", "", "/analysis"],
  ["/detection", "", "/detection/operations"],
  ["/detection/pipeline", "", "/detection/operations"],
  ["/detection/versions", "?tab=history", "/detection/operations"],
  ["/detection/create", "?tab=rules", "/detection/rules"],
  ["/detection/create", "?tab=dataset", "/detection/models"],
  ["/detection/create", "", "/detection/models"],
  ["/detection/evaluation", "?tab=candidates", "/detection/evaluations"],
  ["/detection/collection", "?tab=status", "/operations?tab=collect"],
];
for (const [path, search, expected] of legacy) assert.equal(legacyRedirect(path, search), expected, path + search);
assert.equal(legacyRedirect("/detection/rules", ""), null, "new routes are not redirected");
assert.equal(legacyRedirect("/analysis", ""), null);

// 2) URL 선택값 정규화
assert.deepEqual(resolveOption(null, ["a", "b"]), { value: "a", invalid: false });
assert.deepEqual(resolveOption("b", ["a", "b"]), { value: "b", invalid: false });
assert.deepEqual(resolveOption("x", ["a", "b"]), { value: "a", invalid: true });
assert.deepEqual(resolveStep(null, 3), { value: 1, invalid: false });
assert.deepEqual(resolveStep("2", 3), { value: 2, invalid: false });
assert.deepEqual(resolveStep("9", 3), { value: 3, invalid: true });
assert.deepEqual(resolveStep("0", 3), { value: 1, invalid: true });
assert.deepEqual(resolveStep("x", 3), { value: 1, invalid: true });
assert.equal(withSearchParam("?q=a&tab=x", "tab", "all"), "?q=a&tab=all", "other query values are kept");
assert.equal(withSearchParam("?tab=x", "tab", null), "");
assert.equal(activeTopNav("/detection/models/v1"), "detection");
assert.equal(activeTopNav("/analysis/d/decision-1"), "analysis");
assert.equal(isAppPath("/analysis/d/decision-1"), true);
assert.equal(isAppPath("/analysis/process-decisions"), false);
assert.equal(safeReturnPath("/analysis/process-decisions"), "/analysis");
assert.equal(activeTopNav("/settings"), "settings");
assert.equal(activeDetectionMenu("/detection/operations/replace"), "operations");
assert.equal(activeDetectionMenu("/detection/evaluations/e-1"), "evaluations");
assert.deepEqual(detectionMenu.map((item) => item.label), ["운영 설정", "룰 설정", "머신러닝 모델", "평가 결과"]);
assert.deepEqual(visibleTopNavItems("user").map((item) => item.label), ["운영 현황", "이상 분석"]);
assert.deepEqual(visibleTopNavItems("admin").map((item) => item.label), ["운영 현황", "이상 분석", "탐지 설정"]);
assert.equal(isAppPath("/detection/pipeline"), false);
assert.equal(isAppPath("/notifications"), false);
assert.equal(safeReturnPath("/detection/collection"), "/operations?tab=collect");

// 3) 실제 라우트 표 렌더(로컬 가짜 fetch만, 외부 요청 없음)
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
let currentUser;
const requested = [];
globalThis.fetch = async (url) => {
  requested.push(String(url));
  if (String(url).endsWith("/api/auth/me"))
    return new Response(JSON.stringify({ user: currentUser }), { headers: { "content-type": "application/json" } });
  return new Response(JSON.stringify({ code: "unavailable" }), { status: 503, headers: { "content-type": "application/json" } });
};
const text = (node) =>
  !node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join(" ") : text(node.children);
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 5)));

const mount = async (path, role = "admin") => {
  currentUser = { id: `${role}-1`, email: `${role}@example.test`, userType: role, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${role}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))),
    );
  });
  for (let i = 0; i < 20 && text(renderer.toJSON()).includes("로그인 상태를 확인하는 중"); i++) await tick();
  await tick();
  const h1 = () => renderer.root.findAllByType("h1").map((node) => text(node.children)).join(" | ");
  const links = () => renderer.root.findAllByType("a");
  const cleanup = async () => {
    await act(async () => renderer.unmount());
    router.dispose();
    client.clear();
  };
  return { renderer, router, h1, links, cleanup };
};
const location = (router) => router.state.location.pathname + router.state.location.search;

const pages = [
  ["/operations", "운영 현황"],
  ["/analysis", "이상 분석"],
  ["/analysis/d/decision-1", "이상 분석"],
  ["/settings?section=profile", "사용자 환경"],
  ["/detection/operations", "운영 설정"],
  ["/detection/rules", "룰 설정"],
  ["/detection/models", "머신러닝 모델"],
  ["/detection/evaluations", "평가 결과"],
  ["/detection/operations/replace", "모델 교체"],
  ["/detection/operations/jobs", "작업 목록"],
  ["/detection/operations/jobs/job-1", "작업 상세"],
  ["/detection/models/model-1", "모델 버전 상세"],
  ["/detection/evaluations/eval-1", "평가 상세"],
];
for (const [path, title] of pages) {
  const app = await mount(path);
  assert.ok(app.h1().includes(title), `${path} renders ${title}; got ${app.h1()}`);
  assert.equal(location(app.router), path, `${path} stays on its address`);
  const h1s = app.renderer.root.findAllByType("h1");
  assert.ok(h1s.length === 1 && h1s[0].props.className === "sr-only", `${path} keeps only a visually hidden screen title`);
  await app.cleanup();
}

// 설정: 다른 화면과 같은 구조(복귀 버튼·보이는 제목 없음), 저장 미지원은 내용 오른쪽 위, 상단 설정 링크에 복귀 주소 없음
{
  const app = await mount("/settings?section=profile");
  const anchors = () => app.renderer.root.findAll((node) => node.type === "a");
  const textOf = (node) => (typeof node === "string" ? node : (node.children ?? []).map(textOf).join(""));
  assert.equal(anchors().filter((node) => textOf(node).includes("이전 화면으로 돌아가기")).length, 0, "no back button");
  const save = app.renderer.root.findAll((node) => node.type === "button" && textOf(node) === "저장 미지원")[0];
  assert.equal(save.props.disabled, true, "the unsupported save stays");
  assert.ok(save.parent.parent.props.className?.includes("justify-end") || save.parent.props.className?.includes("justify-end"), "aligned to the right");
  assert.equal(anchors().find((node) => node.props["aria-label"] === "설정").props.href, "/settings?section=profile", "the current section stays");
  await app.cleanup();
  // 옛 주소 `?returnTo=`: 화면이 그대로 열리고 복귀 버튼은 없다
  const legacy = await mount("/settings?returnTo=%2Fanalysis");
  assert.ok(legacy.h1().includes("알림 설정"));
  assert.equal(legacy.renderer.root.findAll((node) => node.type === "a" && textOf(node).includes("이전 화면으로 돌아가기")).length, 0);
  await legacy.cleanup();
  const other = await mount("/operations");
  assert.equal(other.renderer.root.findAll((node) => node.type === "a" && node.props["aria-label"] === "설정")[0].props.href, "/settings", "no return address");
  await other.cleanup();
}

// 옛 주소: replace 이동(뒤로 가기에 옛 주소가 남지 않음)
for (const [path, search, expected] of legacy) {
  const app = await mount(path + search);
  assert.equal(location(app.router), expected, `${path}${search} → ${expected}`);
  assert.equal(app.router.state.historyAction, "REPLACE", `${path} uses replace`);
  await app.cleanup();
}
{
  const app = await mount("/");
  assert.equal(location(app.router), "/operations");
  await app.cleanup();
}

// 탐지 설정 사이드바 4메뉴·aria-current·하위 화면 뒤로가기 아이콘
{
  const app = await mount("/detection/rules");
  const side = app.renderer.root.findByProps({ id: "detection-menu" });
  const toc = side.findAll((node) => node.props["data-slot"] === "side-nav-sub");
  const menu = side.findAllByType("a").filter((node) => !toc.some((list) => list.findAllByType("a").includes(node)));
  assert.deepEqual(menu.map((node) => text(node.children)), ["운영 설정", "룰 설정", "머신러닝 모델", "평가 결과"]);
  assert.deepEqual(menu.map((node) => node.props["aria-current"] ?? null), [null, "page", null, null]);
  assert.equal(app.links().filter((node) => String(node.props["aria-label"] ?? "").startsWith("뒤로")).length, 0, "menu pages have no back icon");
  // 현재 메뉴(룰 설정)만 아래에 화면 영역 목차를 펼친다. 같은 화면 안 이동이라 replace, 주소는 #영역 id
  assert.equal(toc.length, 1, "only the current menu shows its table of contents");
  const tocLinks = toc[0].findAllByType("a");
  assert.deepEqual(tocLinks.map((node) => text(node.children)), ["이상 규칙", "이상 징후 규칙", "관계 규칙", "프로토콜 설정", "응답 코드", "규칙 추천", "변경 기록"]);
  assert.deepEqual(tocLinks.map((node) => node.props.href), [
    "/detection/rules#rules-hard", "/detection/rules#rules-soft", "/detection/rules#rules-relation", "/detection/rules#rules-protocols",
    "/detection/rules#rules-response-codes", "/detection/rules#rules-candidates", "/detection/rules#rules-history",
  ]);
  const order = side.findAll((node) => node.type === "a" || node.type === "ul");
  assert.equal(order.indexOf(toc[0]), order.indexOf(menu[1]) + 1, "the list sits right under its menu item");
  const top = app.renderer.root.findByProps({ "aria-label": "주요 화면" }).findAllByType("a");
  assert.deepEqual(top.map((node) => text(node.children).trim()), ["운영 현황", "이상 분석", "탐지 설정"]);
  assert.deepEqual(top.map((node) => node.props["aria-current"] ?? null), [null, null, "page"], "only the current top menu item is marked");
  assert.ok(top.every((node) => node.findAllByType("svg").length === 1), "each top menu item keeps its icon");
  assert.ok(String(top[2].props.className).includes("rounded-full") && String(top[2].props.className).includes("aria-[current=page]:bg-accent"), "pill item with active style");
  await app.cleanup();
}
{
  const app = await mount("/detection/models/model-1");
  const back = app.links().find((node) => node.props["aria-label"] === "뒤로 · 머신러닝 모델");
  assert.ok(back, "detail page shows the back icon to its menu");
  assert.equal(back.props.href, "/detection/models");
  // 뒤로가기 줄은 스크롤해도 머리글 아래 고정, 본문 열은 flex(grid면 sticky가 자기 행 안에서만 움직인다)
  const bar = app.renderer.root.findByProps({ "data-detection-back": true });
  assert.ok(["sticky", "top-(--top-nav-offset)", "z-(--z-sticky)", "bg-background"].every((name) => bar.props.className.split(" ").includes(name)), bar.props.className);
  let column = bar.parent;
  while (typeof column.type !== "string") column = column.parent;
  assert.ok(column.props.className.split(" ").includes("flex-col") && !column.props.className.split(" ").includes("grid"), "sticky bar parent is a flex column");
  // 하위 화면에서도 그 메뉴 목차를 보이고, 누르면 메뉴 첫 화면의 영역으로 이동(push — 뒤로 가기로 하위 화면에 돌아온다)
  const toc = app.renderer.root.findByProps({ "data-slot": "side-nav-sub" }).findAllByType("a");
  assert.deepEqual(toc.map((node) => [text(node.children), node.props.href]), [["버전", "/detection/models#models-versions"], ["새 학습", "/detection/models#models-train"]]);
  assert.ok(toc.every((node) => node.props["aria-current"] === undefined), "no current section on a sub page");
  await app.cleanup();
}
{
  // 첫 화면 목차는 지금 쿼리(탭·단계)를 지킨다
  const app = await mount("/detection/models?tab=all");
  const toc = app.renderer.root.findByProps({ "data-slot": "side-nav-sub" }).findAllByType("a");
  assert.deepEqual(toc.map((node) => node.props.href), ["/detection/models?tab=all#models-versions", "/detection/models?tab=all#models-train"]);
  await app.cleanup();
}
// 화면 이동 스크롤(useDetectionScroll): 경로가 바뀌면 맨 위, 해시면 그 영역, 쿼리만 바뀌면 그대로, 브라우저 뒤로(POP)는 건드리지 않음
{
  const calls = [];
  const sections = new Map();
  const fakeSection = (id) => ({ id, contains: () => false, compareDocumentPosition: () => 0, scrollIntoView: (options) => calls.push(["into", id, options.block]) });
  globalThis.window.scrollTo = (x, y) => calls.push(["top", x, y]);
  globalThis.Node = { DOCUMENT_POSITION_PRECEDING: 2 };
  globalThis.document = { getElementById: (id) => sections.get(id) ?? null, querySelectorAll: () => [] };
  try {
    const app = await mount("/detection/rules");
    assert.deepEqual(calls, [], "first load (POP) leaves the browser position");
    await act(async () => app.router.navigate("/detection/rules/response-codes"));
    assert.deepEqual(calls.splice(0), [["top", 0, 0]], "entering a sub page starts at the top");
    await act(async () => app.router.navigate("/detection/rules/response-codes?q=1"));
    assert.deepEqual(calls.splice(0), [], "query-only changes keep the position");
    sections.set("rules-history", fakeSection("rules-history"));
    await act(async () => app.router.navigate("/detection/rules#rules-history"));
    assert.deepEqual(calls.splice(0), [["top", 0, 0], ["into", "rules-history", "start"]], "TOC from a sub page clears the old position, then scrolls to the section");
    await act(async () => app.router.navigate("/detection/rules#rules-response-codes", { replace: true }));
    await tick();
    assert.deepEqual(calls.splice(0), [], "a missing (conditional) section does nothing");
    await act(async () => app.router.navigate("/detection/rules"));
    assert.deepEqual(calls.splice(0), [["top", 0, 0]], "the menu item without a hash goes back to the top");
    await act(async () => app.router.navigate(-1));
    assert.deepEqual(calls.splice(0), [], "browser back is left to the browser");
    // 늦게 생기는 영역: 조회가 끝나 대상이 생기면 그때 스크롤
    await act(async () => app.router.navigate("/detection/rules#rules-soft"));
    assert.deepEqual(calls.splice(0), []);
    sections.set("rules-soft", fakeSection("rules-soft"));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 150)));
    assert.deepEqual(calls.splice(0), [["into", "rules-soft", "start"]], "waits for the section to appear");
    await app.cleanup();
    // 하위 목록의 고정 뒤로가기 → 룰 설정의 그 카드 영역: 경로가 바뀐 해시 이동이라 맨 위로 올린 뒤 영역으로
    sections.set("rules-response-codes", fakeSection("rules-response-codes"));
    const list = await mount("/detection/rules/response-codes");
    const back = list.links().find((node) => node.props["aria-label"] === "뒤로 · 룰 설정");
    assert.equal(back.props.href, "/detection/rules#rules-response-codes");
    calls.length = 0;
    await act(async () => list.router.navigate(back.props.href));
    assert.deepEqual(calls.splice(0), [["top", 0, 0], ["into", "rules-response-codes", "start"]], "back returns to the card area");
    await list.cleanup();
  } finally {
    delete globalThis.window.scrollTo;
    delete globalThis.document;
    delete globalThis.Node;
  }
}

// 작업 영역 단계 도구(useUrlStep): 잘못된 step은 replace 정규화, 단계 이동 주소는 push라 뒤로 가기와 일치
{
  const { useUrlStep, useUrlOption } = await import("../src/hooks/useUrlState.ts");
  const { workStepCount, urlOptions } = routes;
  let probe;
  const Probe = () => {
    probe = { step: useUrlStep(workStepCount.train), tab: useUrlOption("tab", urlOptions.modelsTab) };
    return null;
  };
  const router = createMemoryRouter([{ path: "/detection/models", element: React.createElement(Probe) }], {
    initialEntries: ["/detection/models?tab=all&step=9"],
  });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(RouterProvider, { router }));
  });
  await tick();
  assert.equal(location(router), "/detection/models?tab=all&step=3");
  assert.equal(router.state.historyAction, "REPLACE");
  await act(async () => router.navigate("/detection/models?tab=bogus"));
  await tick();
  assert.equal(location(router), "/detection/models?tab=active", "invalid list tab is normalized");
  assert.equal(probe.step.value, 1, "missing step means step 1");
  assert.equal(probe.step.hrefFor(2), "/detection/models?tab=active&step=2", "other query values are kept");
  await act(async () => router.navigate(probe.step.hrefFor(2)));
  assert.equal(router.state.historyAction, "PUSH");
  assert.equal(probe.step.value, 2);
  await act(async () => router.navigate(-1));
  assert.equal(probe.step.value, 1, "browser back returns to the previous step");
  await act(async () => renderer.unmount());
  router.dispose();
}
{
  const app = await mount("/detection/operations");
  const replace = app.links().find((node) => text(node.children) === "모델 교체");
  assert.ok(replace, "replace command stays in the body after the title row was removed");
  await act(async () => app.router.navigate(replace.props.href, { state: { returnTo: "/detection/operations" } }));
  const back = app.links().find((node) => node.props["aria-label"] === "뒤로 · 운영 설정");
  assert.ok(back, "replace flow shows the back icon");
  assert.equal(back.props.href, "/detection/operations");
  await app.cleanup();
}

// 일반 사용자: 탐지 설정 메뉴 숨김 + 직접 진입 차단
{
  const app = await mount("/detection/rules", "user");
  assert.ok(text(app.renderer.toJSON()).includes("관리자 권한이 필요합니다"));
  const top = app.renderer.root.findByProps({ "aria-label": "주요 화면" }).findAllByType("a");
  assert.deepEqual(top.map((node) => text(node.children).trim()), ["운영 현황", "이상 분석"]);
  await app.cleanup();
}
{
  const app = await mount("/unknown/path");
  assert.ok(app.h1().includes("화면을 찾을 수 없습니다"));
  await app.cleanup();
}
// 로그인 화면(사용자 11/04): INZENT 로고 + 제목 1개 "ESB 이상 징후 탐지 시스템", 그 밖의 안내 문구 없음, 입력 양식 유지
{
  storage.delete("token");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/login"] });
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))),
    );
  });
  await tick();
  const shown = text(renderer.toJSON());
  assert.deepEqual(renderer.root.findAllByType("h1").map((node) => text(node.children).trim()), ["ESB 이상 징후 탐지 시스템"], "exactly one title");
  const logo = renderer.root.findAllByType("img");
  assert.deepEqual(logo.map((node) => [node.props.src, node.props.alt]), [["test-logo.svg", "INZENT"]], "INZENT logo");
  assert.ok(!/ESB Anomaly Detection|이상 징후 탐지 대시보드|사전 등록된 계정/.test(shown), "no eyebrow, old title or description");
  assert.deepEqual(renderer.root.findAllByType("input").map((node) => node.props.id), ["login-email", "login-password"]);
  assert.ok(renderer.root.findAll((node) => node.type === "button" && node.props.type === "submit" && text(node.children).trim() === "로그인").length === 1);
  await act(async () => renderer.unmount());
  router.dispose();
  client.clear();
}
assert.ok(
  requested.every((url) => url.startsWith("/api/")),
  "only same-origin API paths are requested: " + [...new Set(requested)].join(", "),
);
console.log(
  "routes QA passed: wireframe route table, legacy addresses replaced, top/side menus with aria-current, admin gate, back icon on sub-pages, step/tab URL normalization and history, 404, login page logo and single title",
);
