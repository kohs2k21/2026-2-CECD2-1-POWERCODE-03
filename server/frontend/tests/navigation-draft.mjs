import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { createMemoryRouter, RouterProvider, Outlet } from "react-router-dom";
import {
  NavigationGuard,
  useDraftNavigationGuard,
} from "../src/components/layout/NavigationGuard.tsx";
import { Dialog } from "../src/components/ui/dialog.tsx";
import { Button } from "../src/components/ui/button.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
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
  return React.createElement(Outlet);
};
const router = createMemoryRouter(
  [
    {
      element: React.createElement(Editor),
      children: [
        {
          path: "/detection/create",
          element: React.createElement("p", null, "create"),
        },
        {
          path: "/settings",
          element: React.createElement("p", null, "settings"),
        },
      ],
    },
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
await act(async () => guard.blocker.proceed());
assert.equal(router.state.location.pathname, "/settings");
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
assert.ok(
  listeners.has("beforeunload"),
  "preserved background drafts retain unload protection",
);
await act(async () => router.navigate("/detection/create?tab=training"));
assert.deepEqual(useDraftStore.getState().drafts["/detection/create"].value, {
  selected: ["duration"],
});
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

// Exercise the product dialog's preserve action, rather than only the router primitive.
const actualRouter = createMemoryRouter(
  [
    {
      element: React.createElement(
        React.Fragment,
        null,
        React.createElement(NavigationGuard, {
          logoutRequested: false,
          onCancelLogout() {},
          onLogout() {},
        }),
        React.createElement(Outlet),
      ),
      children: [
        {
          path: "/detection/create",
          element: React.createElement("p", null, "create"),
        },
        {
          path: "/detection/evaluation",
          element: React.createElement("p", null, "evaluation"),
        },
      ],
    },
  ],
  { initialEntries: ["/detection/create"] },
);
await act(async () => {
  renderer = create(
    React.createElement(RouterProvider, { router: actualRouter }),
  );
});
await act(async () =>
  useDraftStore
    .getState()
    .edit("/detection/create", { customFeatureIds: ["custom-1"] }),
);
await act(async () => actualRouter.navigate("/detection/evaluation"));
const childElements = (node) =>
  Array.isArray(node)
    ? node.flatMap(childElements)
    : React.isValidElement(node)
      ? [node, ...childElements(node.props.children)]
      : [];
const preserve = childElements(
  renderer.root.findByType(Dialog).props.children,
).find(
  (node) =>
    node.type === Button && node.props.children === "변경 사항 저장 후 이동",
);
assert.ok(preserve);
assert.equal(
  preserve.props.variant ?? "default",
  "default",
  "preserve action is the primary highlighted button",
);
const dialogButtons = () =>
  childElements(renderer.root.findByType(Dialog).props.children).filter(
    (node) => node.type === Button,
  );
assert.deepEqual(
  dialogButtons().map((node) => node.props.children),
  ["변경 사항 저장 후 이동", "버리고 이동"],
);
// Escape/outside-close cancels navigation without adding a third action button.
await act(async () =>
  renderer.root.findByType(Dialog).props.onOpenChange(false),
);
assert.equal(actualRouter.state.location.pathname, "/detection/create");
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
await act(async () => actualRouter.navigate("/detection/evaluation"));
await act(async () => preserve.props.onClick());
assert.equal(actualRouter.state.location.pathname, "/detection/evaluation");
assert.deepEqual(
  useDraftStore.getState().drafts["/detection/create"].value.customFeatureIds,
  ["custom-1"],
);
assert.ok(listeners.has("beforeunload"));
await act(async () => actualRouter.navigate("/detection/create"));
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
await act(async () => actualRouter.navigate("/detection/evaluation"));
const discard = dialogButtons().find(
  (node) => node.props.children === "버리고 이동",
);
assert.equal(
  discard.props.variant,
  "outline",
  "discard action is visually secondary",
);
await act(async () => discard.props.onClick());
assert.equal(actualRouter.state.location.pathname, "/detection/evaluation");
assert.equal(useDraftStore.getState().drafts["/detection/create"], undefined);
assert.equal(listeners.has("beforeunload"), false);
await act(async () => renderer.unmount());
actualRouter.dispose();
useDraftStore.getState().reset();
// Create tab canonicalization/history is exercised with real auth/query/editor providers in detection-create.mjs.
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
  "draft/navigation QA passed: internal query navigation preserves draft, cancel/discard leave, beforeunload registration/cleanup and mobile keyboard focus",
);
