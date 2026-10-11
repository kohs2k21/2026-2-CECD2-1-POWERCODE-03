import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import {
  AuthSessionProvider,
  useAuthSession,
} from "../src/services/auth/AuthSessionProvider.tsx";
import { DetectionGatewayProvider } from "../src/features/detection/data/useDetectionQuery.tsx";
import { developmentDetectionData } from "../src/features/detection/data/fixtures.ts";
import { EvaluationPage } from "../src/features/detection/pages/EvaluationPage.tsx";
import { VersionsPage } from "../src/features/detection/pages/VersionsPage.tsx";
import { CollectionPage } from "../src/features/detection/pages/CollectionPage.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const storage = new Map();
globalThis.window = {
  location: { origin: "http://localhost" },
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
const text = (node) =>
  !node
    ? ""
    : typeof node === "string"
      ? node
      : Array.isArray(node)
        ? node.map(text).join(" ")
        : text(node.children ?? node.props?.children);
const descendants = (node) =>
  Array.isArray(node)
    ? node.flatMap(descendants)
    : React.isValidElement(node)
      ? [node, ...descendants(node.props.children)]
      : [];
let session;
const Probe = () => {
  session = useAuthSession();
  return null;
};
let requests = 0;
let failRead = false;
const fixture = structuredClone(developmentDetectionData);
fixture.versions.push({
  ...fixture.versions[1],
  id: "candidate-other",
  name: "다른 후보",
  evaluationId: null,
});
fixture.collectionHistory = [
  {
    id: "collection-in-range",
    source: "P",
    range: "10월 첫째 주",
    state: "failed",
    requestedAt: "2026-10-07T00:00:00+09:00",
    count: null,
    failure: "수집 중단",
  },
  {
    id: "collection-outside",
    source: "T",
    range: "9월 마지막 주",
    state: "succeeded",
    requestedAt: "2026-09-30T00:00:00+09:00",
    count: null,
    failure: null,
  },
];
const gateway = {
  read: async () => {
    if (failRead) throw new Error("상태 조회 실패");
    return structuredClone(fixture);
  },
  request: async () => {
    requests++;
    throw new Error("unavailable");
  },
};
const mount = async (url, adapter = gateway, waitReady = true) => {
  storage.clear();
  useDraftStore.getState().reset();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  const router = createMemoryRouter(
    [
      {
        path: "/detection/evaluation",
        element: React.createElement(EvaluationPage),
      },
      {
        path: "/detection/versions",
        element: React.createElement(VersionsPage),
      },
      {
        path: "/detection/collection",
        element: React.createElement(CollectionPage),
      },
    ],
    { initialEntries: [url] },
  );
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(
        QueryClientProvider,
        { client: cache },
        React.createElement(
          AuthSessionProvider,
          null,
          React.createElement(Probe),
          React.createElement(
            DetectionGatewayProvider,
            { gateway: adapter },
            React.createElement(RouterProvider, { router }),
          ),
        ),
      ),
    );
  });
  await act(async () =>
    session.login({
      token: "temporary-test-token",
      user: {
        id: "admin",
        email: "admin@example.test",
        userType: "admin",
        createdAt: "2026-10-08T00:00:00Z",
      },
    }),
  );
  for (
    let i = 0;
    waitReady && i < 20 && text(renderer.toJSON()).includes("불러오는 중");
    i++
  ) {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 8)));
  }
  if (waitReady) assert.ok(!text(renderer.toJSON()).includes("불러오는 중"));
  return {
    renderer,
    router,
    cache,
    close: async () => {
      await act(async () => renderer.unmount());
      cache.clear();
      router.dispose();
    },
  };
};
const labeledControl = (renderer, label, kind = "input") =>
  renderer.root
    .findAllByType("label")
    .find((node) => text(node.children).includes(label))
    .findByType(kind);

export { fixture, gateway, mount, text, labeledControl, descendants };
export const setReadFailure = (value) => {
  failRead = value;
};
export const getRequestCount = () => requests;
