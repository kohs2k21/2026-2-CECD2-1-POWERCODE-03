import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthSessionProvider } from "../src/services/auth/AuthSessionProvider.tsx";
import { DetectionGatewayProvider } from "../src/features/detection/data/useDetectionQuery.tsx";
import { developmentDetectionData } from "../src/features/detection/data/fixtures.ts";
import { createDevelopmentGateway } from "../src/features/detection/data/gateway.ts";
import { CreatePage } from "../src/features/detection/DetectionPages.tsx";
import { useDraftNavigationGuard } from "../src/components/layout/NavigationGuard.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { Modal } from "../src/components/ui/Modal.tsx";
import { DialogFooter } from "../src/components/ui/dialog.tsx";
import { defaultFeatureEditor } from "../src/features/detection/data/featureBuilder.ts";
import { featureCatalog } from "../src/features/detection/data/catalog.ts";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => callback();
const storage = new Map([["token", "test-session"]]);
const listeners = new Map();
globalThis.window = {
  location: { origin: "http://localhost" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener: (name, listener) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(listener);
  },
  removeEventListener: (name, listener) =>
    listeners.get(name)?.delete(listener),
  setTimeout,
  clearTimeout,
};
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      user: {
        id: "admin",
        email: "admin@example.test",
        userType: "admin",
        createdAt: "2026-10-08T00:00:00Z",
      },
    }),
    { headers: { "content-type": "application/json" } },
  );
let guard;
const GuardProbe = () => {
  guard = useDraftNavigationGuard();
  return null;
};
const createFixture = structuredClone(developmentDetectionData);
createFixture.trainingJobs.push({
  ...createFixture.trainingJobs[0],
  id: "training-completed",
  state: "succeeded",
  candidateId: "candidate-delay",
});
const gateway = createDevelopmentGateway(createFixture);
let writes = 0;
const adapter = {
  ...gateway,
  request: (...args) => {
    writes++;
    return gateway.request(...args);
  },
};
const cache = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});
const router = createMemoryRouter(
  [
    {
      path: "/detection/create",
      element: React.createElement(
        React.Fragment,
        null,
        React.createElement(CreatePage),
        React.createElement(GuardProbe),
      ),
    },
    {
      path: "/operations",
      element: React.createElement("p", null, "operations"),
    },
  ],
  { initialEntries: ["/detection/create?tab=invalid&filter=retained"] },
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
        React.createElement(
          DetectionGatewayProvider,
          { gateway: adapter },
          React.createElement(RouterProvider, { router }),
        ),
      ),
    ),
  );
});
const tick = async () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15));
  });
for (
  let i = 0;
  i < 40 && renderer.root.findAllByType("input").length === 0;
  i++
)
  await tick();
const byLabel = (label) =>
  renderer.root
    .findAllByType("label")
    .find((node) => node.children[0] === label)
    ?.findByType("input");
const byTextarea = (label) =>
  renderer.root
    .findAllByType("label")
    .find((node) => node.children[0] === label)
    ?.findByType("textarea");
const bySelect = (label) =>
  renderer.root
    .findAllByType("label")
    .find((node) => node.children[0] === label)
    ?.findByType("select");
const clickButton = async (label) =>
  act(async () =>
    renderer.root
      .findAllByType("button")
      .find((node) => node.children.includes(label))
      .props.onClick(),
  );
const nameInput = byLabel("구성 이름");
assert.deepEqual(renderer.root.findByType("h1").children, ["모델·룰 생성"]);
const layoutOrder = renderer.root
  .findAll((node) =>
    [
      "detection-page-header",
      "create-tabs",
      "detection-form-grid detection-feedback",
    ].includes(node.props.className),
  )
  .slice(0, 3)
  .map((node) => node.props.className);
assert.deepEqual(
  layoutOrder,
  [
    "detection-page-header",
    "create-tabs",
    "detection-form-grid detection-feedback",
  ],
  "heading then tabs then composition name must precede feature content",
);
assert.equal(router.state.location.search, "?tab=dataset&filter=retained");
await act(async () =>
  router.navigate("/detection/create?tab=rules&filter=retained"),
);
await act(async () => router.navigate(-1));
assert.equal(router.state.location.search, "?tab=dataset&filter=retained");
assert.ok(nameInput);
await act(async () =>
  byLabel("구성 이름").props.onChange({ target: { value: "실제 초안 이름" } }),
);
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
assert.equal(
  listeners.get("beforeunload").size,
  1,
  "actual editor must register unload guard",
);
let prevented = false;
const unload = {
  preventDefault: () => (prevented = true),
  returnValue: undefined,
};
listeners.get("beforeunload").forEach((listener) => listener(unload));
assert.equal(prevented, true);
assert.equal(unload.returnValue, "");
await act(async () =>
  router.navigate("/detection/create?tab=features&filter=retained"),
);
const rawLabels = renderer.root
  .findAllByType("input")
  .map((node) => node.props["aria-label"])
  .filter((label) => label?.endsWith("학습 피처 선택"));
assert.equal(
  new Set(rawLabels).size,
  rawLabels.length,
  "T/P duplicate raw names need qualified accessible labels",
);
await act(async () =>
  router.navigate("/detection/create?tab=data-features&feature=F07"),
);
assert.equal(
  byLabel("파생변수 이름"),
  undefined,
  "unsupported canonical expressions must remain read-only",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].value.featureEdits.F07,
  undefined,
);
await act(async () =>
  router.navigate("/detection/create?tab=data-features&feature=A00"),
);
assert.equal(
  byLabel("파생변수 이름"),
  undefined,
  "catalog templates must stay unchanged",
);
await clickButton("복제하여 편집");
const clonedId = new URLSearchParams(router.state.location.search).get(
  "feature",
);
assert.equal(clonedId, "custom-1");
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].value.featureEdits.A00,
  undefined,
);
assert.ok(
  renderer.root
    .findAllByType("th")
    .some((node) => node.children.includes("유형")),
);
assert.ok(
  renderer.root
    .findAllByType("td")
    .some((node) => node.children.includes("원본")),
);
assert.ok(
  renderer.root
    .findAllByType("td")
    .some((node) => node.children.includes("파생")),
);
await act(async () =>
  byLabel("파생변수 이름").props.onChange({ target: { value: " " } }),
);
await clickButton("편집 내용 유지");
assert.equal(
  byLabel("파생변수 이름").props.value,
  " ",
  "validation failure preserves actual input",
);
assert.ok(renderer.root.findAll((node) => node.props.role === "alert").length);
await act(async () =>
  byLabel("파생변수 이름").props.onChange({
    target: { value: "처리시간 사용자 정의" },
  }),
);
const setExpression = async (expression) =>
  act(async () =>
    byTextarea("수식").props.onChange({
      target: { value: expression, selectionStart: expression.length },
    }),
  );
await setExpression("if([process.ERROR_COUNT] > 0, 1, 0)");
await act(async () =>
  byLabel("process.ERROR_COUNT 검증 입력").props.onChange({
    target: { value: "2" },
  }),
);
await clickButton("미리보기");
assert.ok(
  renderer.root
    .findAll((node) => node.props.role === "status")
    .some((node) => node.children.join("").includes("결과: 1")),
  "direct formula preview uses typed sample values",
);
await setExpression("[deleted-feature] + 1");
assert.ok(
  renderer.root.findAll((node) => node.props.role === "alert").length,
  "missing references show inline error",
);
await setExpression("[process.ERROR_");
let preventedAutocomplete = false;
await act(async () =>
  byTextarea("수식").props.onKeyDown({
    key: "Enter",
    preventDefault: () => (preventedAutocomplete = true),
  }),
);
assert.equal(preventedAutocomplete, true);
assert.equal(
  byTextarea("수식").props.value,
  "[process.ERROR_COUNT]",
  "keyboard autocomplete inserts canonical reference",
);
await setExpression("[process.TOTAL_COUNT]");
await act(async () => {
  byTextarea("수식").props.onClick({ currentTarget: { selectionStart: 17 } });
});
await act(async () =>
  byTextarea("수식").props.onKeyDown({
    key: "Enter",
    preventDefault: () => {},
  }),
);
assert.equal(
  byTextarea("수식").props.value,
  "[process.TOTAL_COUNT]",
  "autocomplete replaces the entire bracket reference at an interior cursor",
);
await setExpression("log1p([process.TOTAL_COUNT])");
await act(async () =>
  byTextarea("수식").props.onClick({ currentTarget: { selectionStart: 2 } }),
);
await act(async () =>
  byTextarea("수식").props.onKeyDown({
    key: "Enter",
    preventDefault: () => {},
  }),
);
assert.equal(
  byTextarea("수식").props.value,
  "log1p([process.TOTAL_COUNT])",
  "function autocomplete preserves existing argument list",
);
await setExpression("[process.ERROR_COUNT] / [process.TOTAL_COUNT]");
await act(async () =>
  router.navigate("/detection/create?tab=dataset&filter=retained"),
);
assert.ok(bySelect("고정 데이터셋"));
assert.equal(
  byTextarea("수식"),
  undefined,
  "dataset tab excludes feature editing",
);
await act(async () =>
  bySelect("고정 데이터셋").props.onChange({
    target: { value: "snapshot-september" },
  }),
);
await act(async () =>
  router.navigate(
    `/detection/create?tab=features&feature=${clonedId}&filter=retained`,
  ),
);
assert.equal(byLabel("파생변수 이름").props.value, "처리시간 사용자 정의");
assert.equal(
  byTextarea("수식").props.value,
  "[process.ERROR_COUNT] / [process.TOTAL_COUNT]",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].value.snapshotId,
  "snapshot-september",
  "tab changes preserve shared dataset and feature drafts",
);
await act(async () =>
  router.navigate("/detection/create?tab=rules&rule=duration-limit"),
);
const ruleCreation = () =>
  renderer.root
    .findAllByType(ServiceAction)
    .find((node) => node.props.operation === "createRule");
assert.equal(
  ruleCreation().props.requestDisabled,
  false,
  "rule creation must not require a dataset or training configuration",
);
assert.equal(
  ruleCreation().props.payload.rule.enabled,
  undefined,
  "experiment creation must not carry activation selection",
);
assert.ok(
  !renderer.root
    .findAllByType("label")
    .some((node) => node.children.includes("이 룰 선택")),
);
await act(async () =>
  byLabel("룰 이름").props.onChange({ target: { value: "새 임계 룰" } }),
);
await act(async () =>
  byLabel("임계값").props.onChange({ target: { value: "abc" } }),
);
assert.equal(
  byLabel("임계값").props.value,
  "abc",
  "invalid numeric text must remain editable",
);
assert.equal(ruleCreation().props.requestDisabled, true);
const maintain = renderer.root
  .findAllByType("button")
  .find((node) => node.children.includes("룰 입력 확인"));
await act(async () => maintain.props.onClick());
assert.ok(
  renderer.root.findAll((node) => node.props.role === "alert").length > 0,
  "invalid rule input needs inline error",
);
await act(async () =>
  router.navigate("/detection/create?tab=rules&rule=count-mismatch"),
);
assert.equal(
  byLabel("룰 이름").props.value,
  "합계 불일치",
  "rule ID switch must not mix prior rule draft",
);
await act(async () =>
  router.navigate("/detection/create?tab=rules&rule=duration-limit"),
);
assert.equal(byLabel("룰 이름").props.value, "새 임계 룰");
assert.equal(byLabel("임계값").props.value, "abc");
await act(async () =>
  router.navigate(
    "/detection/create?tab=rules&recommendation=recommendation-count",
  ),
);
await clickButton("룰 초안으로 가져오기");
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].value.rules[
    "error-count-limit"
  ].enabled,
  true,
);
await act(async () =>
  router.navigate(
    "/detection/create?tab=rules&rule=error-count-limit&recommendation=recommendation-count",
  ),
);
await act(async () =>
  byLabel("룰 이름").props.onChange({
    target: { value: "직접 수정한 추천 룰" },
  }),
);
await clickButton("추천 검토 제외");
let linked =
  useDraftStore.getState().drafts["/detection/create"].value.rules[
    "error-count-limit"
  ];
assert.equal(
  linked.enabled,
  true,
  "recommendation review does not set experiment activation",
);
assert.equal(linked.name, "직접 수정한 추천 룰");
await clickButton("룰 초안으로 가져오기");
linked =
  useDraftStore.getState().drafts["/detection/create"].value.rules[
    "error-count-limit"
  ];
assert.equal(linked.enabled, true);
assert.equal(linked.name, "직접 수정한 추천 룰");
await act(async () => router.navigate("/detection/create?tab=training"));
const beforeValidation =
  useDraftStore.getState().drafts["/detection/create"].value;
const f05 = defaultFeatureEditor(
  featureCatalog.find((item) => item.id === "F05"),
);
const setF05 = async (edit) =>
  act(async () =>
    useDraftStore.getState().edit("/detection/create", {
      ...beforeValidation,
      snapshotId: "snapshot-september",
      start: "2026-09-01",
      end: "2026-09-30",
      training: {
        ...beforeValidation.training,
        featureIds: ["F05"],
        trees: "100",
      },
      featureEdits: { ...beforeValidation.featureEdits, F05: edit },
    }),
  );
const trainAction = () =>
  renderer.root
    .findAllByType(ServiceAction)
    .find((node) => node.props.operation === "train");
const modelFeatureSave = renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "saveDraft");
for (const key of ["rules", "ruleThresholdInputs", "recommendationDecisions"])
  assert.equal(
    modelFeatureSave.props.payload[key],
    undefined,
    "non-rule saving must not send unvalidated rule edits",
  );
await setF05(f05);
assert.equal(
  trainAction().props.requestDisabled,
  false,
  "valid catalog edit should remain usable",
);
for (const edit of [
  { ...f05, name: " " },
  { ...f05, operation: "trainMedian" },
  { ...f05, right: "F22" },
]) {
  await setF05(edit);
  assert.equal(
    trainAction().props.requestDisabled,
    true,
    "known ID edits must validate definition and both fit dependencies",
  );
}
await setF05(f05);
await act(async () =>
  byLabel("트리 수").props.onChange({ target: { value: "0" } }),
);
assert.ok(
  renderer.root.findAll((node) => node.props.role === "alert").length > 0,
);
await act(async () =>
  router.navigate("/detection/create?tab=training&job=training-completed"),
);
assert.equal(
  renderer.root
    .findAllByType("a")
    .find((node) => node.children.includes("이 후보 평가하기")).props.href,
  "/detection/evaluation?candidate=candidate-delay",
);
await act(async () =>
  router.navigate("/detection/create?tab=training&job=training-queued"),
);
assert.ok(
  !renderer.root
    .findAllByType("a")
    .some((node) => node.children.includes("이 후보 평가하기")),
  "pending jobs must not claim a generated candidate",
);
await act(async () =>
  router.navigate(`/detection/create?tab=data-features&feature=${clonedId}`),
);
assert.equal(byLabel("구성 이름").props.value, "실제 초안 이름");
assert.equal(byLabel("파생변수 이름").props.value, "처리시간 사용자 정의");
await clickButton("파생변수 삭제");
const deletion = () =>
  renderer.root
    .findAllByType(Modal)
    .find((node) => node.props.title === "파생변수 삭제");
assert.equal(deletion().props.isOpen, true);
await act(async () => deletion().props.onOpenChange(false));
assert.equal(
  byLabel("파생변수 이름").props.value,
  "처리시간 사용자 정의",
  "cancel preserves edits",
);
await clickButton("파생변수 추가");
const addedId = new URLSearchParams(router.state.location.search).get(
  "feature",
);
await clickButton("파생변수 삭제");
const deletionButtons = React.Children.toArray(deletion().props.children).find(
  (child) => child.type === DialogFooter,
).props.children;
await act(async () =>
  React.Children.toArray(deletionButtons)
    .find((child) => child.props.children === "삭제")
    .props.onClick(),
);
const afterDeletion =
  useDraftStore.getState().drafts["/detection/create"].value;
assert.equal(afterDeletion.customFeatureIds.includes(addedId), false);
assert.equal(afterDeletion.selectedFeatureIds.includes(addedId), false);
assert.equal(afterDeletion.featureEdits[clonedId].name, "처리시간 사용자 정의");
assert.equal(
  new URLSearchParams(router.state.location.search).has("feature"),
  false,
);
await act(async () =>
  byLabel("속성 검색").props.onChange({
    target: { value: "no-such-feature-filter" },
  }),
);
assert.ok(
  renderer.root
    .findAllByType("div")
    .some((node) =>
      node.children.includes("검색 조건에 맞는 속성이 없습니다."),
    ),
  "filtered custom entries must allow a truthful empty state",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/create"].value.featureEdits[
    clonedId
  ].name,
  "처리시간 사용자 정의",
);
await act(async () => router.navigate("/operations"));
assert.equal(guard.blocker.state, "blocked");
await act(async () => guard.blocker.reset());
assert.equal(router.state.location.pathname, "/detection/create");
assert.equal(useDraftStore.getState().drafts["/detection/create"].dirty, true);
assert.equal(
  writes,
  0,
  "editing and invalid forms must never request unconnected backend",
);
await act(async () => router.navigate("/operations"));
await act(async () => {
  useDraftStore.getState().discard("/detection/create");
  guard.blocker.proceed();
});
assert.equal(router.state.location.pathname, "/operations");
assert.deepEqual(useDraftStore.getState().drafts, {});
await act(async () => renderer.unmount());
router.dispose();
cache.clear();
assert.equal(listeners.get("beforeunload").size, 0);
console.log(
  "create editor QA passed: actual dirty/unload/cancel/discard, tab+ID isolation, retained invalid text, training errors, qualified labels and zero unconnected writes",
);
