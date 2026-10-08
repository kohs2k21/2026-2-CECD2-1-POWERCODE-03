import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { useDraftNavigationGuard } from "../src/components/layout/NavigationGuard.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
import { CreatePage } from "../src/features/detection/DetectionPages.tsx";
import { DetectionLayout } from "../src/features/detection/DetectionLayout.tsx";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const listeners = new Map();
globalThis.window = {
  addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name) => listeners.delete(name),
};
let guard;
const Editor = () => {
  guard = useDraftNavigationGuard();
  return React.createElement(CreatePage);
};
const router = createMemoryRouter(
  [
    { path: "/detection/create", element: React.createElement(Editor) },
    { path: "/settings", element: React.createElement("p", null, "settings") },
  ],
  { initialEntries: ["/detection/create?tab=data-features"] },
);
let renderer;
await act(async () => {
  renderer = create(React.createElement(RouterProvider, { router }));
});
await act(async () =>
  useDraftStore
    .getState()
    .edit("/detection/create", { selected: ["duration"] }),
);
assert.ok(listeners.has("beforeunload"));
let prevented = false;
const unload = {
  preventDefault: () => {
    prevented = true;
  },
  returnValue: undefined,
};
listeners.get("beforeunload")(unload);
assert.equal(prevented, true);
assert.equal(unload.returnValue, "");
await act(async () => router.navigate("/detection/create?tab=training"));
assert.equal(guard.blocker.state, "unblocked");
assert.deepEqual(useDraftStore.getState().drafts["/detection/create"].value, {
  selected: ["duration"],
});
await act(async () => router.navigate("/settings"));
assert.equal(router.state.location.pathname, "/detection/create");
assert.equal(guard.blocker.state, "blocked");
await act(async () => guard.blocker.reset());
assert.equal(router.state.location.search, "?tab=training");
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
await act(async () => router.navigate("/settings"));
await act(async () => {
  useDraftStore.getState().discard(guard.currentPath);
  guard.blocker.proceed();
});
assert.equal(router.state.location.pathname, "/settings");
assert.deepEqual(useDraftStore.getState().drafts, {});
assert.equal(listeners.has("beforeunload"), false);
await act(async () => renderer.unmount());
router.dispose();

// Invalid tab canonicalizes without losing other URL filters; history restores the previous tab.
const tabsRouter = createMemoryRouter(
  [{ path: "/detection/create", element: React.createElement(CreatePage) }],
  { initialEntries: ["/detection/create?tab=invalid&filter=retained"] },
);
await act(async () => {
  renderer = create(
    React.createElement(RouterProvider, { router: tabsRouter }),
  );
});
assert.equal(
  tabsRouter.state.location.search,
  "?tab=data-features&filter=retained",
);
await act(async () =>
  tabsRouter.navigate("/detection/create?tab=rules&filter=retained"),
);
await act(async () => tabsRouter.navigate(-1));
assert.equal(
  tabsRouter.state.location.search,
  "?tab=data-features&filter=retained",
);
await act(async () => renderer.unmount());
tabsRouter.dispose();
// Inline mobile menu: Escape closes it and returns keyboard focus to the toggle.
const menuRouter = createMemoryRouter(
  [
    {
      element: React.createElement(DetectionLayout),
      children: [
        {
          path: "/detection/create",
          element: React.createElement("p", null, "content"),
        },
      ],
    },
  ],
  { initialEntries: ["/detection/create"] },
);
let focusCount = 0;
await act(async () => {
  renderer = create(
    React.createElement(RouterProvider, { router: menuRouter }),
    {
      createNodeMock: (element) =>
        element.type === "button"
          ? {
              focus: () => {
                focusCount++;
              },
            }
          : null,
    },
  );
});
const toggle = () =>
  renderer.root.findByProps({ "aria-controls": "detection-menu" });
await act(async () => toggle().props.onClick());
assert.equal(toggle().props["aria-expanded"], true);
let escapePrevented = false;
await act(async () =>
  renderer.root
    .findByProps({ "aria-label": "탐지 관리 메뉴" })
    .props.onKeyDown({
      key: "Escape",
      preventDefault: () => {
        escapePrevented = true;
      },
    }),
);
assert.equal(toggle().props["aria-expanded"], false);
assert.equal(focusCount, 1);
assert.equal(escapePrevented, true);
assert.equal(menuRouter.state.location.pathname, "/detection/create");
await act(async () => toggle().props.onClick());
const firstMenuLink = renderer.root.findAllByType("a")[0];
await act(async () =>
  firstMenuLink.props.onClick({
    defaultPrevented: false,
    button: 0,
    preventDefault() {},
  }),
);
assert.equal(toggle().props["aria-expanded"], false);
assert.equal(
  focusCount,
  2,
  "collapsing on selection must return focus from the hidden link",
);
await act(async () => renderer.unmount());
menuRouter.dispose();
console.log(
  "draft/navigation QA passed: internal tabs preserve draft, cancel/discard leave, beforeunload registration/cleanup, invalid tab and browser history",
);
