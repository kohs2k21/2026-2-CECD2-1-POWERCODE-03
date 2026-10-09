import assert from "node:assert/strict";
import { act } from "react-test-renderer";
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
import {
  fixture,
  gateway,
  mount,
  text,
  labeledControl,
  descendants,
  setReadFailure,
  getRequestCount,
} from "./detection-operation-harness.mjs";
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
assert.match(text(view.renderer.toJSON()), /조건·구성 일치 결과 없음/);
assert.match(text(view.renderer.toJSON()), /미측정값은 —/);
await act(async () =>
  useDraftStore.getState().edit("/detection/evaluation", {
    editor: "evaluation",
    conditionsByCandidate: {
      "candidate-delay": { ...condition, protocolId: "edited-protocol" },
    },
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
assert.ok(!text(view.renderer.toJSON()).includes("edited-protocol"));
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?candidate=candidate-delay&retained=yes",
  ),
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value
    .conditionsByCandidate["candidate-delay"].protocolId,
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
const evaluate = view.renderer.root
  .findAllByType(ServiceAction)
  .find((item) => item.props.label === "평가 실행");
assert.equal(evaluate.props.available, false);
assert.equal(
  evaluate.props.disabled,
  true,
  "historical free conditions are not a fixed evaluation set",
);
await act(async () => evaluate.findByType("button").props.onClick());
assert.equal(evaluate.findByType(Modal).props.isOpen, true);
assert.match(text(evaluate.findByType(Modal).props.children), /작업 서비스/);
assert.equal(
  descendants(evaluate.findByType(Modal).props.children)
    .filter((node) => node.type === Button)
    .at(-1).props.disabled,
  true,
);
assert.equal(getRequestCount(), 0);
setReadFailure(true);
await act(async () =>
  view.cache.invalidateQueries({ queryKey: ["detection-workbench", "admin"] }),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.match(text(view.renderer.toJSON()), /상태 조회 실패/);
assert.match(text(view.renderer.toJSON()), /이전 조회 내용/);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value
    .conditionsByCandidate["candidate-delay"].protocolId,
  "edited-protocol",
);
setReadFailure(false);
await act(async () =>
  view.renderer.root
    .findAllByType("button")
    .find((node) => text(node) === "다시 시도")
    .props.onClick(),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.ok(!text(view.renderer.toJSON()).includes("상태 조회 실패"));
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value
    .conditionsByCandidate["candidate-delay"].protocolId,
  "edited-protocol",
  "retry keeps actual editor dirty state",
);
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
assert.equal(getRequestCount(), 0);
const updateReviewedContent = async (change) =>
  act(async () => {
    view.cache.setQueryData(["detection-workbench", "admin"], (previous) =>
      change(structuredClone(previous)),
    );
    await new Promise((resolve) => setTimeout(resolve, 12));
  });
await act(async () =>
  view.router.navigate(
    "/detection/versions?tab=configuration&candidate=candidate-delay&retained=yes",
  ),
);
assert.equal(
  view.renderer.root
    .findAllByType(ServiceAction)
    .some((node) => node.props.operation === "apply"),
  false,
);
await act(async () => view.router.navigate(-1));
assert.equal(
  labeledControl(view.renderer, "관리자로서").props.checked,
  false,
  "leaving the application tab invalidates approval",
);
assert.equal(
  view.renderer.root
    .findAllByType(ServiceAction)
    .find((node) => node.props.operation === "apply")
    .findByType(Modal).props.isOpen,
  false,
  "leaving application closes its confirmation",
);
for (const changedId of [
  "candidate-delay",
  "version-current",
  "version-previous",
]) {
  await act(async () =>
    labeledControl(view.renderer, "관리자로서").props.onChange({
      target: { checked: true },
    }),
  );
  await act(async () =>
    labeledControl(view.renderer, "롤백 대상의").props.onChange({
      target: { checked: true },
    }),
  );
  for (const action of view.renderer.root.findAllByType(ServiceAction))
    await act(async () => action.findByType("button").props.onClick());
  await updateReviewedContent((data) => {
    data.versions = data.versions.map((version) =>
      version.id === changedId
        ? { ...version, featureVersion: `${version.featureVersion}-updated` }
        : version,
    );
    return data;
  });
  if (changedId !== "version-previous") {
    assert.equal(
      labeledControl(view.renderer, "관리자로서").props.checked,
      false,
    );
    assert.equal(
      view.renderer.root
        .findAllByType(ServiceAction)
        .find((node) => node.props.operation === "apply")
        .findByType(Modal).props.isOpen,
      false,
    );
  }
  if (changedId !== "candidate-delay") {
    assert.equal(
      labeledControl(view.renderer, "롤백 대상의").props.checked,
      false,
    );
    assert.equal(
      view.renderer.root
        .findAllByType(ServiceAction)
        .find((node) => node.props.operation === "rollback")
        .findByType(Modal).props.isOpen,
      false,
    );
  }
}
await act(async () =>
  labeledControl(view.renderer, "관리자로서").props.onChange({
    target: { checked: true },
  }),
);
await updateReviewedContent((data) => {
  data.evaluations[0].condition.protocolId = "new-evidence";
  return data;
});
assert.equal(
  labeledControl(view.renderer, "관리자로서").props.checked,
  false,
  "same ID evaluation evidence changes require renewed approval",
);
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
await act(async () =>
  view.router.navigate(
    "/detection/collection?tab=storage&from=2026-10-01&to=2026-10-07&source=P&retained=yes",
  ),
);
assert.match(text(view.renderer.toJSON()), /관측 미확인/);
assert.equal(
  view.renderer.root
    .findAllByType("button")
    .filter((node) => text(node) === "상태 다시 조회").length,
  1,
  "storage can refresh without changing tabs",
);
await act(async () =>
  view.renderer.root
    .findAllByType("button")
    .find((node) => text(node) === "상태 다시 조회")
    .props.onClick(),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.ok(!text(view.renderer.toJSON()).includes("원본별 최근 수집 상태"));
await act(async () => view.router.navigate(-1));
assert.match(text(view.renderer.toJSON()), /미확인 건수는 0건/);
await act(async () =>
  view.router.navigate(
    "/detection/collection?tab=history&from=2026-10-01&to=2026-10-07&source=P&retained=yes",
  ),
);
assert.match(text(view.renderer.toJSON()), /10월 첫째 주/);
assert.ok(!text(view.renderer.toJSON()).includes("9월 마지막 주"));
await act(async () =>
  view.router.navigate(
    "/detection/collection?tab=status&from=2026-10-01&to=2026-10-07&source=P&retained=yes",
  ),
);
await act(async () =>
  labeledControl(view.renderer, "원본", "select").props.onChange({
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
assert.equal(getRequestCount(), 0);
await view.close();
let releaseRead;
view = await mount(
  "/detection/versions",
  {
    ...gateway,
    read: () =>
      new Promise((resolve) => {
        releaseRead = resolve;
      }),
  },
  false,
);
assert.match(text(view.renderer.toJSON()), /불러오는 중/);
assert.ok(!text(view.renderer.toJSON()).includes("운영 구성 변경 확인"));
await act(async () => releaseRead(structuredClone(fixture)));
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.ok(!text(view.renderer.toJSON()).includes("불러오는 중"));
await view.close();
setReadFailure(true);
view = await mount("/detection/versions");
assert.match(text(view.renderer.toJSON()), /상태 조회 실패/);
assert.ok(!text(view.renderer.toJSON()).includes("현재 운영 구성"));
setReadFailure(false);
await act(async () =>
  view.renderer.root
    .findAllByType("button")
    .find((node) => text(node) === "다시 시도")
    .props.onClick(),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.ok(!text(view.renderer.toJSON()).includes("상태 조회 실패"));
await view.close();
view = await mount("/detection/versions", {
  ...gateway,
  read: async () => ({
    ...structuredClone(fixture),
    versions: [],
    activeVersionId: null,
    applications: [],
  }),
});
assert.match(
  text(view.renderer.toJSON()),
  /현재 운영 버전이 확인되지 않았습니다/,
);
await act(async () =>
  view.router.navigate("/detection/versions?tab=application"),
);
assert.match(text(view.renderer.toJSON()), /적용 후보가 없습니다/);
await act(async () => view.router.navigate("/detection/versions?tab=history"));
assert.match(text(view.renderer.toJSON()), /적용·롤백 이력이 없습니다/);
assert.equal(getRequestCount(), 0);
await view.close();
console.log(
  "detection operation pages tests passed: workflow, pending, fresh error/retry, stale error/retry preserving editor, empty data and zero writes",
);
