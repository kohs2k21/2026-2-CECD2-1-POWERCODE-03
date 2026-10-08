import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AuthSessionProvider,
  useAuthSession,
} from "../src/services/auth/AuthSessionProvider.tsx";
import { DetectionGatewayProvider } from "../src/features/detection/data/useDetectionQuery.tsx";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { Modal } from "../src/components/ui/Modal.tsx";
import { Button } from "../src/components/ui/button.tsx";
import { ErrorState } from "../src/components/ui/feedback.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map();
globalThis.window = {
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
let session;
const Probe = () => {
  session = useAuthSession();
  return null;
};
const calls = [];
const gateway = {
  read: async () => {
    throw new Error("not used");
  },
  request: (operation, payload, signal) =>
    new Promise((resolve, reject) =>
      calls.push({ operation, payload, signal, resolve, reject }),
    ),
};
const cache = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
let payload = { rule: { name: "룰", threshold: 3000 } };
const tree = () =>
  React.createElement(
    QueryClientProvider,
    { client: cache },
    React.createElement(
      AuthSessionProvider,
      null,
      React.createElement(Probe),
      React.createElement(
        DetectionGatewayProvider,
        { gateway },
        React.createElement(ServiceAction, {
          operation: "createRule",
          label: "룰 생성",
          payload,
          available: true,
        }),
      ),
    ),
  );
let renderer;
await act(async () => {
  renderer = create(tree());
});
const login = async (id, token) =>
  act(async () =>
    session.login({
      token,
      user: {
        id,
        email: `${id}@example.test`,
        userType: "admin",
        createdAt: "2026-10-08T00:00:00Z",
      },
    }),
  );
await login("A", "temporary-A");
await act(async () =>
  useDraftStore.getState().edit("/detection/create", {
    customFeatureIds: ["custom-1"],
    training: { trees: "invalid" },
  }),
);
const modal = () => renderer.root.findByType(Modal);
const text = (node) =>
  typeof node === "string"
    ? node
    : Array.isArray(node)
      ? node.map(text).join(" ")
      : React.isValidElement(node)
        ? text(node.props.children)
        : "";
const elements = (node) =>
  Array.isArray(node)
    ? node.flatMap(elements)
    : React.isValidElement(node)
      ? [node, ...elements(node.props.children)]
      : [];
const open = async () =>
  act(async () => renderer.root.findByType("button").props.onClick());
const submit = async () =>
  act(async () =>
    elements(modal().props.children)
      .filter((node) => node.type === Button)
      .at(-1)
      .props.onClick(),
  );
await open();
await submit();
assert.equal(calls[0].payload.rule.threshold, 3000);
payload = { rule: { name: "룰", threshold: 4000 } };
await act(async () => renderer.update(tree()));
assert.equal(calls[0].signal.aborted, true);
assert.equal(modal().props.isOpen, false);
await act(async () =>
  calls[0].resolve({
    requestId: "old-input",
    state: "accepted",
    message: "accepted",
  }),
);
assert.ok(!text(modal().props.children).includes("old-input"));
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
await open();
await submit();
assert.equal(calls[1].payload.rule.threshold, 4000);
await act(async () => modal().props.onOpenChange(false));
assert.equal(calls[1].signal.aborted, true);
await act(async () =>
  calls[1].resolve({
    requestId: "closed-input",
    state: "accepted",
    message: "accepted",
  }),
);
assert.ok(!text(modal().props.children).includes("closed-input"));
await open();
await submit();
await act(async () => calls[2].reject(new Error("서버 요청 실패")));
assert.equal(
  elements(modal().props.children).find((node) => node.type === ErrorState)
    .props.title,
  "서버 요청 실패",
);
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
await submit();
await act(async () =>
  calls[3].resolve({
    requestId: "accepted-only",
    state: "accepted",
    message: "accepted",
  }),
);
assert.match(text(modal().props.children), /요청 접수: accepted-only/);
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].dirty,
  true,
  "rule receipt must not mark unrelated feature/training draft saved",
);
await act(async () => modal().props.onOpenChange(false));
await open();
await submit();
await login("B", "temporary-B");
assert.equal(calls[4].signal.aborted, true);
await act(async () =>
  calls[4].resolve({
    requestId: "old-account",
    state: "accepted",
    message: "accepted",
  }),
);
assert.ok(!text(modal().props.children).includes("old-account"));
assert.equal(modal().props.isOpen, false);
assert.deepEqual(useDraftStore.getState().drafts, {});
await act(async () => renderer.unmount());
cache.clear();
console.log(
  "service action QA passed: confirmed payload, changed-input/close/account cancellation, late receipt exclusion, failure keeps draft, receipt is not whole draft save",
);
