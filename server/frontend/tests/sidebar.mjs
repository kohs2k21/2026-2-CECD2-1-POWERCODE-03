import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { SideNav } from "../src/components/layout/SideNav.tsx";
import { LinkTabs } from "../src/components/layout/LinkTabs.tsx";
import { detectionBackTarget } from "../src/features/detection/DetectionLayout.tsx";
import { cn, fontSizeTokens } from "../src/lib/utils.ts";
import { readFileSync } from "node:fs";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const text = (node) =>
  !node ? "" : typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join("") : text(node.children);

// 1) 사이드바 한 벌: 그룹 제목·항목 Link·aria-current·건수, 좁은 화면 메뉴 버튼
const Probe = () =>
  React.createElement(SideNav, {
    label: "판정 분류",
    heading: "분류",
    menuId: "side-test",
    activeId: "Warning",
    groups: [
      { title: "이상 로그", items: [
        { id: "All", label: "전체", to: "/analysis?category=All", count: 3 },
        { id: "Warning", label: "주의", to: "/analysis?category=Warning", count: 0 },
      ] },
      { title: "분류함 (확인 불가)", items: [{ id: "Open", label: "보류", to: "/analysis?category=Open" }] },
    ],
  });
const router = createMemoryRouter([{ path: "/analysis", element: React.createElement(Probe) }], {
  initialEntries: ["/analysis"],
});
let renderer;
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router }));
});
const nav = renderer.root.findByProps({ id: "side-test" });
assert.equal(nav.type, "nav");
assert.equal(nav.props["aria-label"], "판정 분류", "one labelled navigation landmark");
assert.deepEqual(nav.findAllByType("h3").map((node) => text(node.children)), ["이상 로그", "분류함 (확인 불가)"]);
// 제목 h2(13/600/18 흐린 글자 = 그룹 제목과 같은 모양), 그룹은 그 아래 h3. 메뉴 글자 크기 토큰이 색 클래스와 함께 남는다.
const heading = nav.findByType("h2");
assert.equal(text(heading.children), "분류");
assert.equal(heading.props.className, "px-3 text-group text-muted-foreground");
assert.ok(nav.findAllByType("h3").every((node) => node.props.className === heading.props.className), "heading and group titles share one style");
const links = nav.findAllByType("a");
assert.ok(links.every((node) => node.props.className.split(" ").includes("text-menu")), "menu size token survives class merging");
assert.deepEqual(links.map((node) => node.props.className.split(" ").includes("font-semibold")), [false, true, false], "only the current item is semibold");
assert.deepEqual(links.map((node) => node.props.href), ["/analysis?category=All", "/analysis?category=Warning", "/analysis?category=Open"]);
assert.deepEqual(links.map((node) => node.props["aria-current"] ?? null), [null, "page", null]);
assert.ok(text(links[0]).includes("3") && text(links[1]).includes("0"), "zero counts are shown, not hidden");
assert.ok(!text(links[2]).match(/\d/), "unknown counts are not invented");
const toggle = renderer.root.findByProps({ "aria-controls": "side-test" });
assert.equal(toggle.props["aria-expanded"], false);
assert.ok(text(toggle).includes("분류"));
await act(async () => renderer.unmount());
router.dispose();

// 1-1) 제목 없는 사이드바(이상 분석): 그룹 제목이 h2(제목 수준 건너뛰기 없음)
const noHeadingRouter = createMemoryRouter([{ path: "/analysis", element: React.createElement(SideNav, {
  label: "판정 분류", menuId: "side-no-heading", activeId: null,
  groups: [{ title: "이상 로그", items: [{ id: "All", label: "전체", to: "/analysis" }] }] }) }], { initialEntries: ["/analysis"] });
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router: noHeadingRouter }));
});
const plain = renderer.root.findByProps({ id: "side-no-heading" });
assert.deepEqual(plain.findAllByType("h2").map((node) => text(node.children)), ["이상 로그"]);
assert.equal(plain.findAllByType("h3").length, 0);
await act(async () => renderer.unmount());
noHeadingRouter.dispose();

// 1-2) 클래스 병합: 글자 크기 토큰(tokens.css --text-*)은 글자 색 클래스와 겹치지 않는다. 토큰 목록은 tokens.css와 일치.
assert.equal(cn("text-menu text-muted-foreground"), "text-menu text-muted-foreground");
assert.equal(cn("px-3 text-group text-muted-foreground"), "px-3 text-group text-muted-foreground");
assert.equal(cn("text-menu text-sm"), "text-sm", "size tokens still replace each other");
assert.equal(cn("text-muted-foreground text-foreground"), "text-foreground", "colors still replace each other");
const tokensCss = readFileSync(new URL("../src/lib/tokens.css", import.meta.url), "utf8");
const declared = [...new Set([...tokensCss.matchAll(/--text-([a-z0-9-]+?)(?:--[a-z-]+)?\s*:/g)].map((match) => match[1]))].sort();
assert.deepEqual([...fontSizeTokens].sort(), declared, "every --text-* token in tokens.css is registered for class merging");

// 2) URL 탭: Link + aria-current, role=tab 미사용
const tabsRouter = createMemoryRouter(
  [{
    path: "/operations",
    element: React.createElement(LinkTabs, {
      label: "운영 현황 보기",
      activeId: "collect",
      tabs: [
        { id: "overview", label: "개요", to: "/operations?tab=overview" },
        { id: "collect", label: "수집", to: "/operations?tab=collect" },
      ],
    }),
  }],
  { initialEntries: ["/operations?tab=collect"] },
);
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router: tabsRouter }));
});
const tabLinks = renderer.root.findAllByType("a");
assert.ok(tabLinks.every((node) => node.props.className.split(" ").includes("text-menu")), "tab size token survives class merging");
assert.deepEqual(tabLinks.map((node) => node.props.className.split(" ").includes("font-semibold")), [false, true]);
assert.deepEqual(tabLinks.map((node) => node.props["aria-current"] ?? null), [null, "page"]);
assert.equal(renderer.root.findAll((node) => node.props.role === "tab").length, 0, "URL tabs do not use local Tabs ARIA");
await act(async () => renderer.unmount());
tabsRouter.dispose();

// 3) 탐지 설정 하위 화면 뒤로가기 대상
assert.equal(detectionBackTarget("/detection/rules", null), null, "menu page has no back target");
assert.deepEqual(detectionBackTarget("/detection/models/v1", null), { to: "/detection/models", label: "머신러닝 모델" });
assert.deepEqual(
  detectionBackTarget("/detection/operations/replace", { returnTo: "/detection/operations?x=1" }),
  { to: "/detection/operations?x=1", label: "운영 설정" },
  "returns to the detection page it came from with its query",
);
assert.deepEqual(
  detectionBackTarget("/detection/operations/replace", { returnTo: "https://evil.test/detection/x" }),
  { to: "/detection/operations", label: "운영 설정" },
  "external return is ignored",
);
assert.deepEqual(
  detectionBackTarget("/detection/operations/replace", { returnTo: "/analysis" }),
  { to: "/detection/operations", label: "운영 설정" },
  "returns only inside detection settings",
);
assert.deepEqual(
  detectionBackTarget("/detection/evaluations/e1", { returnTo: "/detection/evaluations/e1" }),
  { to: "/detection/evaluations", label: "평가 결과" },
  "self return falls back to the menu page",
);
console.log("sidebar QA passed: single SideNav landmark, group titles, aria-current, honest counts, mobile toggle, URL tabs without tab ARIA, detection back targets");
