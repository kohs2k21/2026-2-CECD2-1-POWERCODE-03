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
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { Modal } from "../src/components/ui/Modal.tsx";
import { Button } from "../src/components/ui/button.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
import {
  conditionsMatch,
  evaluationErrors,
  metricText,
  storageAssessment,
  validDateRange,
  versionReadiness,
} from "../src/features/detection/pages/operationPresentation.ts";

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
const mount = async (url) => {
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
            { gateway },
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
    i < 20 && text(renderer.toJSON()).includes("불러오는 중");
    i++
  ) {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 8)));
  }
  assert.ok(!text(renderer.toJSON()).includes("불러오는 중"));
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

const condition = fixture.evaluations[0].condition;
assert.equal(conditionsMatch(condition, { ...condition }), true);
for (const key of Object.keys(condition))
  assert.equal(
    conditionsMatch(condition, { ...condition, [key]: "different" }),
    false,
  );
assert.equal(conditionsMatch(undefined, condition), false);
assert.deepEqual(evaluationErrors(condition, fixture), []);
assert.ok(
  evaluationErrors({ ...condition, splitVersion: "wrong-split" }, fixture).some(
    (error) => error.includes("분할"),
  ),
);
assert.ok(
  evaluationErrors(
    { ...condition, snapshotId: "snapshot-october" },
    fixture,
  ).some((error) => error.includes("준비")),
);
assert.equal(metricText(null, "percent"), "—");
assert.equal(metricText(0, "percent"), "0.0%");
assert.equal(metricText(1.1, "percent"), "—");
assert.ok(
  versionReadiness(fixture.versions[1], fixture).some((reason) =>
    reason.includes("측정"),
  ),
);
assert.equal(validDateRange("2026-10-01", "2026-10-07"), true);
assert.equal(validDateRange("2026-10-08", "2026-10-07"), false);
assert.equal(validDateRange("2026-99-01", ""), false);
assert.equal(validDateRange("2026-02-30", ""), false);

const volume = {
  ...fixture.storage[0],
  state: "fresh",
  path: "/data/test",
  observedAt: "2026-10-08T10:00:00+09:00",
  totalBytes: 1000,
  availableBytes: 140,
  warningAvailableBytes: 100,
};
assert.equal(
  storageAssessment(volume).insufficient,
  true,
  "percent threshold alone warns",
);
assert.equal(
  storageAssessment({
    ...volume,
    availableBytes: 800,
    warningAvailableBytes: 900,
  }).insufficient,
  true,
  "absolute threshold alone warns",
);
assert.equal(
  storageAssessment({ ...volume, state: "stale" }).label,
  "오래된 관측",
);
assert.equal(
  storageAssessment({ ...volume, state: "failed" }).label,
  "관측 실패",
);
assert.equal(
  storageAssessment({ ...volume, availableBytes: 500, path: null }).label,
  "관측 정보 불완전",
);
assert.equal(storageAssessment(fixture.storage[0]).usedPercent, null);

let view = await mount(
  "/detection/evaluation?candidate=candidate-delay&retained=yes",
);
assert.match(text(view.renderer.toJSON()), /비교 불가/);
assert.match(text(view.renderer.toJSON()), /미측정값은 —/);
await act(async () =>
  labeledControl(view.renderer, "평가 프로토콜").props.onChange({
    target: { value: "edited-protocol" },
  }),
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].dirty,
  true,
);
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?candidate=candidate-other&retained=yes",
  ),
);
assert.equal(labeledControl(view.renderer, "평가 프로토콜").props.value, "");
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?candidate=candidate-delay&retained=yes",
  ),
);
assert.equal(
  labeledControl(view.renderer, "평가 프로토콜").props.value,
  "edited-protocol",
);
await act(async () =>
  labeledControl(view.renderer, "후보 검색").props.onChange({
    target: { value: "처리시간" },
  }),
);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("retained"),
  "yes",
);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("candidate"),
  "candidate-delay",
);
const evaluate = view.renderer.root.findByType(ServiceAction);
assert.equal(evaluate.props.available, false);
assert.equal(evaluate.props.disabled, false);
await act(async () => evaluate.findByType("button").props.onClick());
assert.equal(evaluate.findByType(Modal).props.isOpen, true);
assert.match(text(evaluate.findByType(Modal).props.children), /작업 서비스/);
assert.equal(
  descendants(evaluate.findByType(Modal).props.children)
    .filter((node) => node.type === Button)
    .at(-1).props.disabled,
  true,
);
assert.equal(requests, 0);
failRead = true;
await act(async () =>
  view.cache.invalidateQueries({ queryKey: ["detection-workbench", "admin"] }),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.match(text(view.renderer.toJSON()), /상태 조회 실패/);
assert.match(text(view.renderer.toJSON()), /이전 조회 내용/);
assert.equal(
  labeledControl(view.renderer, "평가 프로토콜").props.value,
  "edited-protocol",
);
failRead = false;
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?candidate=missing&result=missing",
  ),
);
assert.match(text(view.renderer.toJSON()), /선택한 항목을 찾을 수 없습니다/);
await view.close();

view = await mount(
  "/detection/versions?candidate=candidate-delay&retained=yes",
);
let apply = view.renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "apply");
assert.equal(apply.props.disabled, true);
await act(async () =>
  labeledControl(view.renderer, "관리자로서").props.onChange({
    target: { checked: true },
  }),
);
apply = view.renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "apply");
assert.equal(apply.props.disabled, false);
assert.equal(apply.props.requestDisabled, true);
assert.equal(apply.props.payload.expectedActiveVersionId, "version-current");
await act(async () => apply.findByType("button").props.onClick());
assert.equal(apply.findByType(Modal).props.isOpen, true);
assert.equal(
  descendants(apply.findByType(Modal).props.children)
    .filter((node) => node.type === Button)
    .at(-1).props.disabled,
  true,
);
assert.equal(requests, 0);
await act(async () =>
  view.router.navigate(
    "/detection/versions?candidate=candidate-other&retained=yes",
  ),
);
assert.equal(
  labeledControl(view.renderer, "관리자로서").props.checked,
  false,
  "approval resets for another candidate",
);
apply = view.renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "apply");
assert.equal(
  apply.findByType(Modal).props.isOpen,
  false,
  "confirmation resets for another candidate",
);
await act(async () =>
  view.router.navigate(
    "/detection/versions?history=application-previous&retained=yes",
  ),
);
assert.match(text(view.renderer.toJSON()), /적용 이력 상세/);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("retained"),
  "yes",
);
assert.equal(fixture.activeVersionId, "version-current");
await view.close();

view = await mount(
  "/detection/collection?from=2026-10-01&to=2026-10-07&source=P&retained=yes",
);
assert.match(text(view.renderer.toJSON()), /발생 기준 전체 지연 미측정/);
assert.match(text(view.renderer.toJSON()), /관측 미확인/);
assert.match(text(view.renderer.toJSON()), /미확인 건수는 0건/);
assert.match(text(view.renderer.toJSON()), /10월 첫째 주/);
assert.ok(!text(view.renderer.toJSON()).includes("9월 마지막 주"));
await act(async () =>
  labeledControl(view.renderer, "원천", "select").props.onChange({
    target: { value: "B" },
  }),
);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("from"),
  "2026-10-01",
);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("retained"),
  "yes",
);
assert.match(text(view.renderer.toJSON()), /BODY/);
assert.match(
  text(view.renderer.toJSON()),
  /기본 PROCESS 판정은 BODY 수집을 기다리지 않습니다/,
);
await act(async () =>
  view.router.navigate(
    "/detection/collection?history=collection-in-range&retained=yes",
  ),
);
assert.match(text(view.renderer.toJSON()), /수집 이력 상세/);
assert.match(text(view.renderer.toJSON()), /수집 중단/);
await act(async () =>
  view.router.navigate(
    "/detection/collection?history=missing&from=2026-10-08&to=2026-10-07",
  ),
);
assert.match(text(view.renderer.toJSON()), /유효한 날짜/);
assert.match(text(view.renderer.toJSON()), /선택한 항목을 찾을 수 없습니다/);
assert.equal(requests, 0);
await view.close();
console.log("detection operation pages tests passed");
