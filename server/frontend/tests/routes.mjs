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
// 알림 페이지(휴대폰 첫 화면): 옛 주소 넘김에서 빠지고 앱 주소가 됐다(navigation.md).
assert.equal(legacyRedirect("/notifications", "?filter=tasks"), null, "/notifications is a page again");
assert.equal(isAppPath("/notifications"), true);
assert.equal(isAppPath("/notifications/x"), false);
assert.equal(safeReturnPath("/notifications?view=all"), "/notifications?view=all");
assert.equal(routes.homePath(true), "/notifications");
assert.equal(routes.homePath(false), "/operations");
assert.equal(safeReturnPath("/detection/collection"), "/operations?tab=collect");

// 3) 실제 라우트 표 렌더(로컬 가짜 fetch만, 외부 요청 없음)
const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
const { AuthSessionProvider } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { appRoutes } = await import("../src/app/appRoutes.tsx");
let currentUser;
const requested = [];
// 일부 시험(화면 전환·목차 스크롤)이 룰 설정 화면을 그리려고 잠시 넣는 응답(없으면 503).
let extraFetch = null;
globalThis.fetch = async (url) => {
  requested.push(String(url));
  const extra = extraFetch?.(new URL(String(url), "http://local.test"));
  if (extra) return new Response(JSON.stringify(extra), { headers: { "content-type": "application/json" } });
  if (String(url).endsWith("/api/auth/me"))
    return new Response(JSON.stringify({ user: currentUser }), { headers: { "content-type": "application/json" } });
  return new Response(JSON.stringify({ code: "unavailable" }), { status: 503, headers: { "content-type": "application/json" } });
};
const text = (node) =>
  !node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join(" ") : text(node.children);
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 5)));

const mount = async (path, role = "admin", createOptions) => {
  currentUser = { id: `${role}-1`, email: `${role}@example.test`, userType: role, createdAt: "2026-10-01T00:00:00Z" };
  storage.set("token", `token-${role}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] });
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(RouterProvider, { router }))),
      createOptions,
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

// 설정: 다른 화면과 같은 구조(복귀 버튼·보이는 제목 없음), 저장 미지원은 첫 카드 제목 줄 오른쪽(빈 줄 없음, 사용자 11/28), 상단 설정 링크에 복귀 주소 없음
{
  const app = await mount("/settings?section=profile");
  const anchors = () => app.renderer.root.findAll((node) => node.type === "a");
  const textOf = (node) => (typeof node === "string" ? node : (node.children ?? []).map(textOf).join(""));
  assert.equal(anchors().filter((node) => textOf(node).includes("이전 화면으로 돌아가기")).length, 0, "no back button");
  const save = app.renderer.root.findAll((node) => node.type === "button" && textOf(node) === "저장 미지원")[0];
  assert.equal(save.props.disabled, true, "the unsupported save stays");
  let slot = save.parent;
  while (slot && !slot.props?.["data-slot"]) slot = slot.parent;
  assert.equal(slot.props["data-slot"], "card-action", "in the first card's title row (right side)");
  const firstCard = app.renderer.root.findAll((node) => node.props?.["data-slot"] === "section-card")[0];
  assert.ok(firstCard.findAll((node) => node === save).length === 1, "on the first card");
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
  // PC 폭: 알림 버튼은 작은 창(버튼), 주요 화면 메뉴·설정은 보인다(휴대폰 폭에서만 숨김 클래스).
  assert.equal(app.links().filter((node) => node.props.href === "/notifications").length, 0, "desktop bell is not a link");
  assert.ok(app.renderer.root.findByProps({ "aria-label": "주요 화면" }).props.className.split(" ").includes("max-md:hidden"));
  await app.cleanup();
}
// 휴대폰 폭(matchMedia 일치): 첫 화면 = 알림 페이지(replace), 알림 버튼 = 알림 페이지 링크(그 페이지에서 aria-current), 설정 링크는 휴대폰에서 숨김.
{
  const query = { matches: true, addEventListener() {}, removeEventListener() {} };
  globalThis.window.matchMedia = () => query;
  try {
    const app = await mount("/");
    assert.equal(location(app.router), "/notifications");
    assert.equal(app.router.state.historyAction, "REPLACE");
    assert.equal(app.h1(), "알림", "the page shows its title");
    const bell = app.links().find((node) => node.props.href === "/notifications");
    assert.ok(bell && bell.props["aria-label"].startsWith("알림"), "bell links to the alerts page");
    assert.equal(bell.props["aria-current"], "page");
    const settings = app.links().find((node) => node.props["aria-label"] === "설정");
    assert.ok(settings.parent && String(settings.props.className).includes("max-md:hidden"), "settings is hidden on phones");
    await app.cleanup();
    const other = await mount("/analysis/d/decision-1");
    const otherBell = other.links().find((node) => node.props.href === "/notifications");
    assert.equal(otherBell.props["aria-current"], undefined, "not current on other screens");
    await other.cleanup();
  } finally {
    delete globalThis.window.matchMedia;
  }
}

// 탐지 설정 사이드바 4메뉴·aria-current·하위 화면 뒤로가기 아이콘
{
  const app = await mount("/detection/rules");
  const side = app.renderer.root.findByProps({ id: "detection-menu" });
  const toc = side.findAll((node) => node.props["data-slot"] === "side-nav-sub");
  const menu = side.findAllByType("a").filter((node) => !toc.some((list) => list.findAllByType("a").includes(node)));
  // 현재 항목은 배경 표시(빈 span, 미끄러지는 현재 표시)가 앞에 붙어 text()의 공백 연결이 앞 공백을 만든다.
  assert.deepEqual(menu.map((node) => text(node.children).trim()), ["운영 설정", "룰 설정", "머신러닝 모델", "평가 결과"]);
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

// 부드러운 스크롤(scrollToSection, ui.md "움직임" 스크롤): 프레임마다 목적지를 다시 재서 도착, 이동 중 목적지가 밀려도 맞춤, 사용자 입력이면 그 자리에서 멈춤.
{
  const { scrollToSection } = await import("../src/features/detection/useDetectionScroll.ts");
  const listeners = new Map();
  const win = {
    scrollY: 0,
    innerHeight: 800,
    scrollTo: (_x, y) => { win.scrollY = y; },
    requestAnimationFrame: (callback) => setTimeout(callback, 8),
    cancelAnimationFrame: (id) => clearTimeout(id),
    addEventListener: (name, fn) => listeners.set(name, [...(listeners.get(name) ?? []), fn]),
    removeEventListener: (name, fn) => listeners.set(name, (listeners.get(name) ?? []).filter((item) => item !== fn)),
  };
  let sectionTop = 3000; // 문서 기준 위치
  const target = { id: "far", contains: () => false, compareDocumentPosition: () => 0, getBoundingClientRect: () => ({ top: sectionTop - win.scrollY }) };
  const saved = { window: globalThis.window, document: globalThis.document, getComputedStyle: globalThis.getComputedStyle, Node: globalThis.Node };
  globalThis.window = win;
  globalThis.Node = { DOCUMENT_POSITION_PRECEDING: 2 };
  globalThis.document = { documentElement: { scrollHeight: 6000 }, getElementById: (id) => (id === "far" ? target : null), querySelectorAll: () => [] };
  globalThis.getComputedStyle = () => ({ scrollPaddingTop: "100px" });
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // 실제 시간에 기대지 않게: 조건이 될 때까지(상한 3초) 기다린다.
  const until = async (check, limit = 3000) => {
    const end = Date.now() + limit;
    while (!check() && Date.now() < end) await sleep(5);
    return check();
  };
  try {
    const positions = [];
    const stop = scrollToSection("far");
    await until(() => {
      positions.push(win.scrollY);
      return Math.round(win.scrollY) === 2900;
    });
    assert.ok(positions.some((value) => value > 0 && value < 2800), `moves in steps, not a jump (${positions.slice(0, 5).join(",")})`);
    assert.ok(positions.every((value, index) => index === 0 || value >= positions[index - 1]), "keeps moving forward");
    assert.equal(Math.round(win.scrollY), 2900, "arrives under the sticky header (scroll-padding-top)");
    // 도착 뒤 위쪽 내용이 늦게 생겨 목적지가 밀리면 맞춘다(settle)
    sectionTop = 3300;
    assert.ok(await until(() => Math.round(win.scrollY) === 3200, 600), "follows a late shift after arriving");
    stop();
    // 사용자 입력이면 그 자리에서 멈춘다
    win.scrollY = 0;
    sectionTop = 3000;
    let cancelled = 0;
    scrollToSection("far", { onCancel: () => (cancelled += 1) });
    await until(() => win.scrollY > 0);
    (listeners.get("wheel") ?? []).forEach((fn) => fn());
    const stoppedAt = win.scrollY;
    await sleep(100);
    assert.ok(cancelled === 1 && win.scrollY === stoppedAt && stoppedAt < 2900, "wheel stops the scroll where it is");
    // 스크롤 막대 끌기(입력 이벤트 없음): 목적지·문서 높이가 그대로인데 위치만 달라지면 그 자리에서 멈춘다
    win.scrollY = 0;
    cancelled = 0;
    scrollToSection("far", { onCancel: () => (cancelled += 1) });
    await until(() => win.scrollY > 0);
    win.scrollY = 5000; // 사용자가 막대를 끌어 이동 궤적(0~2900)에서 먼 곳으로 옮김
    assert.ok(await until(() => cancelled === 1, 500), "dragging the scrollbar stops the scroll");
    await sleep(50);
    assert.equal(win.scrollY, 5000, "left where the user put it");
    // 문서 높이가 바뀌어 위치가 달라진 것(내용이 줄어 끝에 맞춤 등)은 사용자 이동이 아니다
    win.scrollY = 0;
    cancelled = 0;
    const stopShrink = scrollToSection("far", { onCancel: () => (cancelled += 1) });
    await until(() => win.scrollY > 0);
    globalThis.document.documentElement.scrollHeight = 5000;
    win.scrollY += 40;
    await until(() => Math.round(win.scrollY) === 2900 || cancelled > 0);
    assert.equal(cancelled, 0, "a page height change is not a user move");
    assert.equal(Math.round(win.scrollY), 2900, "still arrives");
    stopShrink();
    globalThis.document.documentElement.scrollHeight = 6000;
    // 동작 줄이기: 즉시 그 자리로(맞춤은 그대로)
    win.scrollY = 0;
    const stopReduced = scrollToSection("far", { reduce: true });
    assert.equal(Math.round(win.scrollY), 2900, "reduced motion jumps at once");
    stopReduced();
  } finally {
    globalThis.window = saved.window;
    globalThis.document = saved.document;
    globalThis.getComputedStyle = saved.getComputedStyle;
    globalThis.Node = saved.Node;
    if (saved.window === undefined) delete globalThis.window;
    if (saved.document === undefined) delete globalThis.document;
    if (saved.Node === undefined) delete globalThis.Node;
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
// 화면 전환(ui.md "움직임" 화면 전환, PageFade): 효과가 켜지는 환경(문서·프레임 함수·Element.animate, 본문 상자는 가짜 DOM)에서 실제 appRoutes로.
// 이전 화면은 키가 바뀐 커밋에서 바로 내려가고 DOM 정적 사본만 남는다. 가짜 애니메이션은 짧게 끝난다(onfinish 호출) — 사본 제거·새 화면 투명 해제·대기 처리 순서를 본다.
{
  const saved = { document: globalThis.document, Element: globalThis.Element, getComputedStyle: globalThis.getComputedStyle,
    raf: globalThis.window.requestAnimationFrame, caf: globalThis.window.cancelAnimationFrame, scrollTo: globalThis.window.scrollTo };
  const seq = [];
  const copies = [];
  // 끝나는 가짜 애니메이션: finishAll()이 진행 중인 것을 끝낸다(onfinish 호출). settle()도 끝낸다.
  let running = [];
  const animation = (label) => {
    const run = { cancelled: false, onfinish: null, cancel() { run.cancelled = true; } };
    running.push(run);
    seq.push(`animate ${label}`);
    return run;
  };
  const finishAll = async () => {
    for (let round = 0; round < 3; round++) {
      const now = running;
      running = [];
      await act(async () => now.forEach((run) => !run.cancelled && run.onfinish?.()));
    }
  };
  const fakeCopy = (from) => {
    const copy = { from, style: {}, removed: false, removeAttribute() {}, setAttribute() {}, querySelectorAll: () => [], animate: () => animation("copy-out"),
      remove() { copy.removed = true; seq.push("copy removed"); } };
    return copy;
  };
  // 본문 상자는 PageFade마다 따로(바깥·안쪽 한쪽 투명 잔류가 가려지지 않게)
  const hosts = [];
  const makeHost = () => {
    const host = { style: {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }), cloneNode: () => fakeCopy(host), animate: () => animation("host-in") };
    hosts.push(host);
    return host;
  };
  const transparent = () => hosts.filter((host) => host.style.opacity === "0").length;
  // 작업 영역(step 주소): areaMoves면 실제처럼 그 영역으로 위치를 바꾸고 scroll 이벤트를 보낸다.
  let areaMoves = false;
  const area = { scrollIntoView: () => {
    seq.push("area");
    if (!areaMoves) return;
    areaMoves = false; // 한 번만(대기 중 첫 작업 영역 스크롤) — 조회가 끝나 단계가 다시 그려질 때의 두 번째 호출은 시점이 일정하지 않다
    globalThis.window.scrollY = 900;
    setTimeout(() => fire("scroll"), 0);
  } };
  const isWorkArea = (element) => element.type === "div" && element.props.children?.props?.id && ["evaluations-new", "models-train"].includes(element.props.children.props.id);
  const nodeMock = { createNodeMock: (element) => (element.type === "div" && element.props["data-page-fade"] !== undefined ? makeHost() : isWorkArea(element) ? area : null) };
  // 목차 영역(룰 설정 화면의 SectionCard id): 지금 그려진 화면(viewing)에 그 id가 있으면 가짜 요소. 문서 기준 위치 = 순서 × 300, 화면 기준 top = 문서 기준 − scrollY
  const sectionIds = ["rules-hard", "rules-soft", "rules-relation", "rules-protocols", "rules-response-codes", "rules-candidates", "rules-history"];
  // 평가 결과 화면: 비교 평가 목록 300, 새 비교 평가(작업 영역) 900
  const positions = { ...Object.fromEntries(sectionIds.map((id, index) => [id, index * 300])), "evaluations-list": 300, "evaluations-new": 900 };
  let viewing = null;
  const sections = new Map(Object.keys(positions).map((id) => [id, { id, contains: () => false, compareDocumentPosition: () => 0,
    getBoundingClientRect: () => ({ top: positions[id] - globalThis.window.scrollY, height: 280 }) }]));
  const sectionById = (id) => (viewing && sections.has(id) && viewing.root.findAll((node) => node.type === "div" && node.props.id === id).length ? sections.get(id) : null);
  globalThis.Element = class {};
  globalThis.Element.prototype.animate = () => animation("element");
  globalThis.document = { documentElement: { scrollHeight: 2600 }, head: { appendChild() {} }, createElement: () => ({}),
    body: { appendChild: (node) => { copies.push(node); seq.push("copy added"); } }, getElementById: (id) => sectionById(id), querySelectorAll: () => [] };
  // 창 스크롤: 위치를 바꾸면 scroll 이벤트를 다음 차례에 보낸다(브라우저처럼)
  const winListeners = new Map();
  const savedWin = { addEventListener: globalThis.window.addEventListener, removeEventListener: globalThis.window.removeEventListener, scrollY: globalThis.window.scrollY, innerHeight: globalThis.window.innerHeight };
  globalThis.window.addEventListener = (name, fn) => winListeners.set(name, [...(winListeners.get(name) ?? []), fn]);
  globalThis.window.removeEventListener = (name, fn) => winListeners.set(name, (winListeners.get(name) ?? []).filter((item) => item !== fn));
  const fire = (name) => (winListeners.get(name) ?? []).slice().forEach((fn) => fn());
  globalThis.window.scrollY = 0;
  globalThis.window.innerHeight = 500;
  globalThis.getComputedStyle = () => ({ opacity: "1", scrollPaddingTop: "0px" });
  globalThis.window.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 16);
  globalThis.window.cancelAnimationFrame = (id) => clearTimeout(id);
  globalThis.window.scrollTo = (_x, y) => {
    seq.push(y === 0 ? "top 0" : "scroll");
    globalThis.window.scrollY = y;
    setTimeout(() => fire("scroll"), 0);
  };
  // 룰 설정 화면 응답(tests/rules-page.mjs 모양을 줄임): 목차 영역이 그려지게
  const ruleSetFetch = (target) => {
    const actor = { actorId: "1", displayName: "admin@example.test" };
    if (target.pathname === "/api/rule-set") return { contractVersion: "rule-set-v1", revision: 12, contentHash: "a".repeat(64), contentHashVersion: 2, savedAt: "2026-11-02T08:12:33Z",
      savedBy: actor, restoredFromRevision: null, catalogVersion: "method-catalog-v2", counts: { hardEnabled: 1, hardTotal: 1, softEnabled: 0, softTotal: 0 },
      hardRules: [{ id: "hard:negative-duration", methodVersion: "negative-duration-v1", displayName: "시작 시각이 종료 시각보다 늦음", conditionText: "START_TIME > END_TIME", severity: "critical", enabled: true, editable: ["enabled"] }],
      softRules: [] };
    if (target.pathname === "/api/rule-set/history") return { items: [], pagination: { limit: 50, hasMore: false, nextCursor: null } };
    if (target.pathname === "/api/jobs/capabilities") return { methodCatalog: { softRuleFields: [] } };
    return null;
  };
  const settle = async () => {
    for (let i = 0; i < 8; i++) await tick();
    await finishAll();
  };
  try {
    // 1) 옛 주소 넘김(replace): 효과 없이 바로, 넘긴 주소 그대로
    {
      copies.length = 0;
      const app = await mount("/detection/collection", "admin", nodeMock);
      await settle();
      assert.equal(location(app.router), "/operations?tab=collect", "legacy address keeps its tab");
      assert.equal(copies.length, 0, "replace navigation has no fade copy");
      await app.cleanup();
    }
    // 2) 머신러닝 모델 › 전체 버전 → 상단 운영 현황 → 뒤로. 사본이 지워지고 새 화면 투명이 풀리며, 기다리던 처리는 사본이 지워진 뒤 실행된다.
    {
      const { afterPageSwap } = await import("../src/components/layout/PageFade.tsx");
      const app = await mount("/detection/models?tab=all", "admin", nodeMock);
      copies.length = 0;
      seq.length = 0;
      await act(async () => app.router.navigate("/operations"));
      afterPageSwap(() => seq.push("after swap"));
      await Promise.resolve(); // 대기 판단(마이크로태스크)이 먼저 돈다
      assert.equal(copies.length, 1, "push navigation fades through a static copy");
      assert.equal(transparent(), 1, "new screen is transparent while the copy fades");
      assert.equal(app.h1().includes("머신러닝 모델"), false, "the old screen is not mounted while its copy fades");
      await finishAll();
      await act(async () => Promise.resolve());
      assert.equal(copies[0].removed, true, "the copy is removed after fading");
      assert.equal(transparent(), 0, "the new screen is no longer transparent");
      assert.ok(seq.indexOf("copy removed") >= 0 && seq.indexOf("copy removed") < seq.indexOf("after swap"), `waiting work runs after the copy is gone: ${seq.join(" > ")}`);
      assert.ok(seq.indexOf("after swap") < seq.indexOf("animate host-in"), `then the new screen fades in: ${seq.join(" > ")}`);
      assert.equal(location(app.router), "/operations", "nothing from the old screen rewrites the new address");
      await act(async () => app.router.navigate(-1));
      await settle();
      assert.equal(location(app.router), "/detection/models?tab=all", "back returns to the same tab");
      await app.cleanup();
    }
    // 3) 규칙 추천 › 실행 흐름 규칙 → 머신러닝 모델 → 뒤로
    {
      const app = await mount("/detection/rules/candidates?tab=flow", "admin", nodeMock);
      await act(async () => app.router.navigate("/detection/models"));
      await settle();
      assert.equal(location(app.router), "/detection/models");
      await act(async () => app.router.navigate(-1));
      await settle();
      assert.equal(location(app.router), "/detection/rules/candidates?tab=flow", "back keeps the flow tab");
      await app.cleanup();
    }
    // 4) 이상 분석 3쪽 → 운영 현황: 이전 목록이 다시 조회하지 않는다
    {
      const app = await mount("/analysis?category=anomaly&page=3", "admin", nodeMock);
      await settle();
      const before = requested.filter((url) => url.startsWith("/api/analysis/decisions")).length;
      await act(async () => app.router.navigate("/operations"));
      await settle();
      assert.equal(requested.filter((url) => url.startsWith("/api/analysis/decisions")).length, before, "no list read from the old screen");
      assert.equal(location(app.router), "/operations");
      await app.cleanup();
    }
    // 5) 탐지 설정 안 경로 이동의 스크롤: 맨 위 올리기는 사본을 기다리지 않고 바로, 작업 영역 스크롤(step 주소)이 그 뒤에 이긴다.
    //    push 직후 replace가 와도 맨 위 올리기는 이미 끝났다.
    {
      const app = await mount("/detection/rules", "admin", nodeMock);
      await settle();
      seq.length = 0;
      await act(async () => app.router.navigate("/detection/evaluations?step=1"));
      const top = seq.indexOf("top 0");
      assert.ok(top >= 0 && (seq.indexOf("copy removed") < 0 || top < seq.indexOf("copy removed")), `top scroll does not wait for the copy: ${seq.join(" > ")}`);
      assert.ok(seq.indexOf("area") > top, `the work area scroll wins after it: ${seq.join(" > ")}`);
      await settle();
      assert.equal(seq.filter((item) => item === "top 0").length, 1, "no late top scroll after the copy is gone");
      await act(async () => app.router.navigate("/detection/rules"));
      await settle();
      seq.length = 0;
      await act(async () => {
        await app.router.navigate("/detection/operations/replace?step=2");
        await app.router.navigate("/detection/operations/replace?step=1", { replace: true });
      });
      await settle();
      assert.ok(seq.includes("top 0"), `push then replace still starts at the top: ${seq.join(" > ")}`);
      await app.cleanup();
    }
    // 7) 하위 화면에서 스크롤된 채 목차로 영역 이동(경로가 바뀜 = 본문 전환): 맨 위 올리기는 바로, 그 scroll 이벤트가 사본이 지워지기 전에 와도
    //    목차 고정이 풀리지 않는다. 사본이 지워진 뒤 부드러운 스크롤이 시작되고 도착할 때까지 누른 항목이 현재 표시.
    {
      extraFetch = ruleSetFetch;
      const app = await mount("/detection/rules/candidates", "admin", nodeMock);
      viewing = app.renderer;
      await settle();
      globalThis.window.scrollY = 400;
      seq.length = 0;
      copies.length = 0;
      const current = () => app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-current"] === "location").map((n) => n.props.href);
      await act(async () => app.router.navigate("/detection/rules#rules-history"));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 60))); // 맨 위 올리기의 scroll 이벤트가 이 사이에 온다
      assert.equal(globalThis.window.scrollY, 0, "top first");
      assert.equal(copies.length === 1 && copies[0].removed, false, "copy still fading");
      assert.deepEqual(current(), ["/detection/rules#rules-history"], "pinned while waiting for the copy");
      assert.equal(seq.includes("scroll"), false, "the smooth scroll waits for the copy");
      await finishAll();
      for (let i = 0; i < 200 && !seq.includes("scroll"); i++) await tick();
      assert.ok(seq.indexOf("copy removed") >= 0 && seq.indexOf("copy removed") < seq.indexOf("scroll"), `smooth scroll starts after the copy is gone: ${seq.join(" > ")}`);
      const goal = Math.min(6 * 300, 2600 - 500);
      for (let i = 0; i < 400 && Math.abs(globalThis.window.scrollY - goal) > 1; i++) {
        await tick();
        assert.deepEqual(current(), ["/detection/rules#rules-history"], `pinned on the way (scrollY ${Math.round(globalThis.window.scrollY)})`);
      }
      assert.ok(Math.abs(globalThis.window.scrollY - goal) <= 1, `arrives (${globalThis.window.scrollY})`);
      assert.deepEqual(current(), ["/detection/rules#rules-history"], "pinned at the end");
      await app.cleanup();
      extraFetch = null;
      viewing = null;
    }
    // 8) 같은 커밋의 바깥 전환(운영 현황 → 탐지 설정 목차 주소): 부드러운 스크롤은 바깥 사본이 지워진 뒤 시작한다
    {
      extraFetch = ruleSetFetch;
      const app = await mount("/operations", "admin", nodeMock);
      viewing = app.renderer;
      await settle();
      globalThis.window.scrollY = 0;
      seq.length = 0;
      await act(async () => app.router.navigate("/detection/rules#rules-history"));
      // 대상 영역이 생기고(조회 뒤) 영역 찾기 간격(100ms)이 지나도록 기다린다 — 기다리지 않는 구현이면 이 사이에 움직인다
      await act(async () => new Promise((resolve) => setTimeout(resolve, 400)));
      assert.ok(viewing.root.findAll((node) => node.type === "div" && node.props.id === "rules-history").length > 0, "target section exists");
      assert.equal(seq.includes("scroll"), false, `waits for the outer copy: ${seq.join(" > ")}`);
      await finishAll();
      for (let i = 0; i < 200 && !seq.includes("scroll"); i++) await tick();
      assert.ok(seq.indexOf("copy removed") >= 0 && seq.indexOf("copy removed") < seq.indexOf("scroll"), `then scrolls: ${seq.join(" > ")}`);
      await app.cleanup();
      extraFetch = null;
      viewing = null;
    }
    // 9) 사본이 사라지는 중에 화면 전체가 내려가면(로그아웃·계정 전환 등) 사본을 바로 지운다
    {
      const app = await mount("/detection/models?tab=all", "admin", nodeMock);
      copies.length = 0;
      await act(async () => app.router.navigate("/operations"));
      assert.equal(copies.length === 1 && copies[0].removed, false, "copy fading");
      await app.cleanup();
      assert.equal(copies[0].removed, true, "removed at once on unmount");
    }
    // 10) 안쪽 사본이 사라지는 중에 바깥 전환이 오면 안쪽 사본은 끝까지 사라진 뒤 지워지고, 남은 투명 상자·사본이 없다
    {
      const app = await mount("/detection/rules", "admin", nodeMock);
      await settle();
      copies.length = 0;
      await act(async () => app.router.navigate("/detection/models"));
      assert.equal(copies.length, 1, "inner copy");
      await act(async () => app.router.navigate("/operations"));
      assert.equal(copies.length, 2, "outer copy too");
      assert.equal(copies[0].removed, false, "inner copy keeps fading after its box is gone");
      await finishAll();
      await settle();
      assert.ok(copies.every((copy) => copy.removed), "all copies removed in the end");
      // 안쪽 상자(탐지 설정 본문)는 바깥 전환으로 내려갔다. 남아 있는 바깥 상자(앱 본문)는 투명이 풀려야 한다.
      assert.notEqual(copies[1].from, copies[0].from, "inner and outer boxes are separate");
      assert.equal(copies[1].from.style.opacity, "", "the outer box is visible again");
      await app.cleanup();
    }
    // 11) 하위 화면(평가 상세)에서 `?step=1#영역`으로 돌아감: 새 화면의 작업 영역 스크롤(step)이 대기 중에 위치를 바꾸고 scroll 이벤트를 보내도
    //     목차 고정·부드러운 스크롤이 풀리지 않고, 사본이 지워진 뒤 해시 영역(비교 평가 목록)에 도착한다.
    {
      const app = await mount("/detection/rules/candidates", "admin", nodeMock);
      viewing = app.renderer;
      await settle();
      globalThis.window.scrollY = 400;
      areaMoves = true;
      seq.length = 0;
      const current = () => app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-current"] === "location").map((n) => n.props.href);
      await act(async () => app.router.navigate("/detection/evaluations?step=1#evaluations-list"));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 150)));
      assert.ok(seq.includes("area") && !areaMoves, `the work area scrolled while waiting: ${seq.join(" > ")}`);
      assert.deepEqual(current(), ["/detection/evaluations?step=1#evaluations-list"], "still pinned after the work area scroll");
      await finishAll();
      for (let i = 0; i < 400 && Math.abs(globalThis.window.scrollY - 300) > 1; i++) await tick();
      assert.equal(Math.round(globalThis.window.scrollY), 300, "arrives at the hash section, not the work area");
      assert.deepEqual(current(), ["/detection/evaluations?step=1#evaluations-list"], "the hash section is current");
      areaMoves = false;
      await app.cleanup();
      viewing = null;
    }
    // 12) 대기 중 휠: 멈추고 고정이 풀린다(사본이 지워져도 부드러운 스크롤이 시작되지 않음)
    {
      extraFetch = ruleSetFetch;
      const app = await mount("/detection/rules/candidates", "admin", nodeMock);
      viewing = app.renderer;
      await settle();
      globalThis.window.scrollY = 400;
      seq.length = 0;
      const current = () => app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-current"] === "location").map((n) => n.props.href);
      await act(async () => app.router.navigate("/detection/rules#rules-history"));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 60)));
      await act(async () => fire("wheel"));
      await finishAll();
      await act(async () => new Promise((resolve) => setTimeout(resolve, 200)));
      assert.equal(seq.includes("scroll"), false, `no smooth scroll after the wheel: ${seq.join(" > ")}`);
      assert.notDeepEqual(current(), ["/detection/rules#rules-history"], "pin released by the wheel");
      await app.cleanup();
      extraFetch = null;
      viewing = null;
    }
    // 13) 대기 중 다른 이동으로 정리되면 "움직이는 중" 표시도 내린다: 다음 이동이 끝난 뒤 사용자 스크롤(막대 끌기 — 입력 이벤트 없음)에 현재 표시가 따라온다
    {
      extraFetch = ruleSetFetch;
      const app = await mount("/detection/rules/candidates", "admin", nodeMock);
      viewing = app.renderer;
      await settle();
      globalThis.window.scrollY = 400;
      const current = () => app.renderer.root.findAll((n) => n.type === "a" && n.props["aria-current"] === "location").map((n) => n.props.href);
      await act(async () => app.router.navigate("/detection/rules#rules-history")); // 사본 대기 중
      await act(async () => app.router.navigate("/detection/rules#rules-soft", { replace: true })); // 같은 화면 목차 — 앞 대기를 정리
      await finishAll();
      for (let i = 0; i < 400 && Math.abs(globalThis.window.scrollY - 300) > 1; i++) await tick();
      assert.deepEqual(current(), ["/detection/rules#rules-soft"], "arrived and pinned");
      await act(async () => new Promise((resolve) => setTimeout(resolve, 1400))); // 도착 뒤 맞춤(1.2초)이 끝날 때까지
      globalThis.window.scrollY = 1800; // 막대 끌기(입력 이벤트 없음)
      await act(async () => fire("scroll"));
      for (let i = 0; i < 20 && current()[0] !== "/detection/rules#rules-history"; i++) await tick();
      assert.deepEqual(current(), ["/detection/rules#rules-history"], "the current section follows the user's scroll");
      await app.cleanup();
      extraFetch = null;
      viewing = null;
    }
    // 6) 이전 화면에서 나오는 이동이 구조적으로 없다: 키가 바뀐 커밋에서 이전 화면이 내려가고, 그 뒤 이전 화면 effect는 다시 돌지 않는다
    //    (이탈 확인 "버리고 이동"으로 초안이 지워지거나 조회 응답이 늦게 와도 이전 화면이 단계 정규화로 주소를 되돌리지 못한다)
    {
      const { PageFade } = await import("../src/components/layout/PageFade.tsx");
      const { createMemoryRouter: memory, RouterProvider: Provider, useNavigate } = await import("react-router-dom");
      const log = [];
      let late;
      const Old = () => {
        const navigate = useNavigate();
        const [answer, setAnswer] = React.useState(0);
        late = setAnswer;
        React.useEffect(() => {
          log.push(`old effect ${answer}`);
          if (answer > 0) navigate("/old?step=1", { replace: true });
        }, [answer, navigate]);
        React.useEffect(() => () => log.push("old unmount"), []);
        return "old";
      };
      let setScreen;
      const Root = () => {
        const [screen, set] = React.useState("old");
        setScreen = set;
        return React.createElement(PageFade, { transitionKey: screen }, screen === "old" ? React.createElement(Old) : "new");
      };
      copies.length = 0;
      const router = memory([{ path: "*", element: React.createElement(Root) }], { initialEntries: ["/old?step=2"] });
      let view;
      await act(async () => {
        view = create(React.createElement(Provider, { router }), nodeMock);
      });
      await act(async () => {
        await router.navigate("/new");
        setScreen("new");
      });
      assert.equal(copies.length, 1, "copy shown while fading");
      assert.ok(log.includes("old unmount"), "old screen unmounted in the same commit");
      const count = log.length;
      await act(async () => late?.(1));
      await settle();
      assert.equal(log.length, count, "no old-screen effect after the switch");
      assert.equal(router.state.location.pathname, "/new", "the old screen cannot navigate back");
      await act(async () => view.unmount());
      router.dispose();
    }
  } finally {
    globalThis.document = saved.document;
    if (saved.document === undefined) delete globalThis.document;
    globalThis.Element = saved.Element;
    if (saved.Element === undefined) delete globalThis.Element;
    globalThis.getComputedStyle = saved.getComputedStyle;
    if (saved.getComputedStyle === undefined) delete globalThis.getComputedStyle;
    globalThis.window.requestAnimationFrame = saved.raf;
    globalThis.window.cancelAnimationFrame = saved.caf;
    globalThis.window.scrollTo = saved.scrollTo;
    globalThis.window.addEventListener = savedWin.addEventListener;
    globalThis.window.removeEventListener = savedWin.removeEventListener;
    globalThis.window.scrollY = savedWin.scrollY;
    globalThis.window.innerHeight = savedWin.innerHeight;
    if (!saved.raf) delete globalThis.window.requestAnimationFrame;
    if (!saved.caf) delete globalThis.window.cancelAnimationFrame;
    if (!saved.scrollTo) delete globalThis.window.scrollTo;
  }
}

assert.ok(
  requested.every((url) => url.startsWith("/api/")),
  "only same-origin API paths are requested: " + [...new Set(requested)].join(", "),
);
console.log(
  "routes QA passed: wireframe route table, legacy addresses replaced, top/side menus with aria-current, admin gate, back icon on sub-pages, step/tab URL normalization and history, 404, login page logo and single title, page fade through a static copy (old screen unmounted at once: no rewrite, no extra read, no navigation; replace without fade; copy removed, new screen visible, waiting work after the copy; top scroll at once, work area scroll wins)",
);
