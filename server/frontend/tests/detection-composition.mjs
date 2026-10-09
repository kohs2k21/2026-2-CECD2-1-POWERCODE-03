import assert from "node:assert/strict";
import { act } from "react-test-renderer";
import { Link } from "react-router-dom";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { Modal } from "../src/components/ui/Modal.tsx";
import { Button } from "../src/components/ui/button.tsx";
import { CompositionEditor } from "../src/features/detection/pages/CompositionEditor.tsx";
import { EvaluationConditionForm } from "../src/features/detection/pages/EvaluationConditionForm.tsx";
import {
  BundleDetails,
  BundleDiff,
} from "../src/features/detection/pages/VersionDetails.tsx";
import { versionReadiness } from "../src/features/detection/pages/operationPresentation.ts";
import { useDraftStore } from "../src/stores/draftStore.ts";
import {
  composePreview,
  compositionErrors,
  currentConfigurationFingerprint,
  evaluationMatchesConfiguration,
} from "../src/features/detection/data/configuration.ts";
import { compositionInputFrom } from "../src/features/detection/pages/useCompositionDraft.ts";
import {
  fixture,
  gateway,
  mount,
  text,
  labeledControl,
  descendants,
  getRequestCount,
} from "./detection-operation-harness.mjs";

const candidate = fixture.versions[1];
const result = fixture.evaluations[0];
const composition = {
  name: "새 조합",
  modelArtifactId: candidate.modelArtifactId,
  ruleVersionIds: candidate.ruleVersionIds,
  explanationVersion: "explanation-v2",
};
const preview = composePreview(composition, fixture);
const artifact = fixture.modelArtifacts.find(
  (item) => item.id === composition.modelArtifactId,
);
assert.ok(preview);
assert.deepEqual(preview.featureIds, artifact.featureIds);
assert.equal(preview.preprocessingVersion, artifact.preprocessingVersion);
assert.equal(preview.fitVersion, artifact.fitVersion);
assert.equal(preview.evaluationId, null);
const unknownExplanationPreview = composePreview(
  { ...composition, explanationVersion: "" },
  fixture,
);
assert.ok(
  unknownExplanationPreview,
  "unknown explanation still permits local composition preview",
);
assert.equal(unknownExplanationPreview.explanationVersion, null);
assert.equal(fixture.activeVersionId, "version-current");
assert.equal(evaluationMatchesConfiguration(result, candidate, fixture), true);
assert.ok(
  compositionErrors(
    {
      ...composition,
      ruleVersionIds: [fixture.ruleVersions[0].id, fixture.ruleVersions[1].id],
    },
    fixture,
  ).some((error) => error.includes("같은 룰")),
);
assert.equal(
  composePreview({ ...composition, modelArtifactId: "missing" }, fixture),
  null,
);
assert.equal(
  compositionInputFrom({ ...composition, ruleVersionIds: [3] }),
  null,
);
assert.equal(
  compositionInputFrom({ ...composition, featureIds: ["ignored"] }).featureIds,
  undefined,
);
for (const change of [
  (data) => {
    data.ruleVersions[1].rules[0].threshold += 100;
  },
  (data) => {
    data.ruleVersions[1].rules[0].operator = "gte";
  },
  (data) => {
    data.modelArtifacts[1].preprocessingVersion = "changed-preprocessing";
  },
  (data) => {
    data.modelArtifacts[1].featureIds.reverse();
  },
]) {
  const changed = structuredClone(fixture);
  change(changed);
  assert.equal(
    evaluationMatchesConfiguration(result, changed.versions[1], changed),
    false,
  );
  assert.ok(
    versionReadiness(changed.versions[1], changed).some((reason) =>
      /구성|재평가/.test(reason),
    ),
  );
}
const mutableRulesChanged = structuredClone(fixture);
mutableRulesChanged.rules[0].threshold = 99999;
assert.equal(
  evaluationMatchesConfiguration(result, candidate, mutableRulesChanged),
  true,
  "immutable version contents bind evidence; editable rule catalog does not",
);

const viewData = structuredClone(fixture);
viewData.modelArtifacts.push({
  ...artifact,
  id: "unfinished-artifact",
  name: "미완료 산출물",
  state: "running",
});
viewData.ruleVersions.push({
  ...viewData.ruleVersions[0],
  id: "unfinished-rules",
  name: "미완료 룰",
  state: "queued",
});
let view = await mount(
  "/detection/versions?tab=configuration&candidate=candidate-delay",
  {
    ...gateway,
    read: async () => structuredClone(viewData),
  },
);
await act(async () =>
  useDraftStore.getState().edit("/detection/create", {
    editor: "create",
    customFeatureIds: ["custom-1"],
    name: "기존 사용자 초안",
  }),
);
const editor = view.renderer.root.findByType(CompositionEditor);
assert.ok(
  !editor
    .findAllByType("label")
    .some((node) => text(node).includes("설명 기준")),
  "no explanation version textbox",
);
assert.match(text(editor), /설명 기준.*미확인/);
assert.ok(!text(editor).includes("미완료 산출물"));
assert.ok(!text(editor).includes("미완료 룰"));
const modelSelect = labeledControl(
  view.renderer,
  "운영할 모델 산출물",
  "select",
);
await act(async () =>
  modelSelect.props.onChange({ target: { value: artifact.id } }),
);
const previewDetails = editor
  .findAllByType(BundleDetails)
  .find((node) => node.props.bundle.id === "composition-preview");
assert.deepEqual(previewDetails.props.bundle.featureIds, artifact.featureIds);
assert.equal(previewDetails.props.bundle.fitVersion, artifact.fitVersion);
assert.equal(
  useDraftStore.getState().drafts["/detection/versions"].dirty,
  true,
);
assert.deepEqual(
  useDraftStore.getState().drafts["/detection/create"].value.customFeatureIds,
  ["custom-1"],
);
assert.match(text(editor), /아직 생성된 후보가 아닙니다/);
const creation = editor.findByType(ServiceAction);
assert.equal(creation.props.operation, "createCandidate");
assert.equal(creation.props.available, false);
assert.equal(
  creation.props.requestDisabled,
  true,
  "unknown explanation prevents final candidate creation",
);
await act(async () => creation.findByType("button").props.onClick());
const confirmation = creation.findByType(Modal);
assert.equal(confirmation.props.isOpen, true);
assert.equal(
  descendants(confirmation.props.children)
    .filter((node) => node.type === Button)
    .at(-1).props.disabled,
  true,
);
assert.equal(getRequestCount(), 0);
await act(async () => confirmation.props.onOpenChange(false));
const conflictingRule = editor
  .findAllByType("label")
  .find((node) => text(node).includes(fixture.ruleVersions[1].name));
await act(async () =>
  conflictingRule
    .findByType("input")
    .props.onChange({ target: { checked: true } }),
);
assert.match(text(editor), /같은 룰의 여러 버전/);
assert.equal(
  editor.findAllByType(ServiceAction).length,
  0,
  "conflicting rule versions cannot create a preview candidate",
);
await act(async () =>
  conflictingRule
    .findByType("input")
    .props.onChange({ target: { checked: false } }),
);
const handoff = editor
  .findAllByType(Link)
  .find((node) => text(node) === "이 구성 평가하기");
const transferred = structuredClone(handoff.props.state.detectionComposition);
await act(async () =>
  view.router.navigate(handoff.props.to, { state: handoff.props.state }),
);
assert.equal(view.router.state.location.pathname, "/detection/evaluation");
assert.equal(
  labeledControl(view.renderer, "평가할 모델 산출물", "select").props.value,
  transferred.modelArtifactId,
);
assert.match(text(view.renderer.toJSON()), /다음 평가 조건/);
assert.match(text(view.renderer.toJSON()), /로컬 구성 미리보기/);
assert.ok(
  !text(view.renderer.toJSON()).includes("기록된 처리 상태"),
  "composition preview has no recorded candidate results",
);
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?tab=candidates&candidate=candidate-delay&q=처리시간",
    { state: view.router.state.location.state },
  ),
);
assert.deepEqual(
  view.router.state.location.state.detectionComposition,
  transferred,
  "URL filtering preserves handoff state",
);
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?tab=composition&candidate=candidate-delay&q=처리시간",
    { state: view.router.state.location.state },
  ),
);
for (const label of ["평가 프로토콜", "시나리오", "평가 목적"]) {
  await act(async () =>
    labeledControl(view.renderer, label).props.onChange({
      target: { value: `입력-${label}` },
    }),
  );
}
const form = view.renderer.root.findByType(EvaluationConditionForm);
assert.equal(form.props.localPreview, true);
assert.equal(
  form.findByType(ServiceAction).props.disabled,
  true,
  "local preview cannot execute evaluation before real candidate creation",
);
const conditionDraft = structuredClone(
  useDraftStore.getState().drafts["/detection/evaluation"].value
    .conditionsByCandidate,
);
await act(async () =>
  labeledControl(view.renderer, "구성 이름").props.onChange({
    target: { value: "평가할 새 이름" },
  }),
);
const merged = useDraftStore.getState().drafts["/detection/evaluation"].value;
assert.equal(merged.editor, "evaluation");
assert.deepEqual(merged.conditionsByCandidate, conditionDraft);
assert.equal(merged.composition.name, "평가할 새 이름");
await act(async () =>
  labeledControl(view.renderer, "평가 목적").props.onChange({
    target: { value: "목적 변경" },
  }),
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.composition
    .name,
  "평가할 새 이름",
);
assert.deepEqual(
  useDraftStore.getState().drafts["/detection/create"].value.customFeatureIds,
  ["custom-1"],
);
assert.equal(getRequestCount(), 0);
await view.close();

const knownExplanationData = structuredClone(fixture);
knownExplanationData.versions[1].explanationVersion = "explanation-recorded";
view = await mount("/detection/versions", {
  ...gateway,
  read: async () => structuredClone(knownExplanationData),
});
await act(async () =>
  labeledControl(view.renderer, "운영할 모델 산출물", "select").props.onChange({
    target: { value: artifact.id },
  }),
);
await act(async () =>
  useDraftStore.getState().edit("/detection/versions", {
    editor: "versions-composition",
    composition: {
      ...composition,
      explanationVersion: "untrusted-draft-version",
    },
  }),
);
const inheritedEditor = view.renderer.root.findByType(CompositionEditor);
assert.match(text(inheritedEditor), /explanation-recorded/);
assert.ok(!text(inheritedEditor).includes("untrusted-draft-version"));
assert.equal(
  inheritedEditor.findByType(ServiceAction).props.payload.composition
    .explanationVersion,
  "explanation-recorded",
);
assert.equal(
  inheritedEditor.findByType(ServiceAction).props.requestDisabled,
  false,
);
assert.equal(getRequestCount(), 0);
await view.close();

view = await mount("/detection/evaluation?candidate=candidate-delay");
assert.match(text(view.renderer.toJSON()), /현재 입력 조건과 구성에 대응하는/);
await act(async () =>
  labeledControl(view.renderer, "평가 프로토콜").props.onChange({
    target: { value: "다음 프로토콜" },
  }),
);
assert.match(text(view.renderer.toJSON()), /현재 입력 조건의 결과 없음/);
assert.match(text(view.renderer.toJSON()), /기록된 처리 상태:\s+성공/);
assert.match(text(view.renderer.toJSON()), /process-common-v1/);
assert.equal(fixture.evaluations[0].state, "succeeded");
await view.close();

view = await mount("/detection/versions?candidate=candidate-delay");
await act(async () =>
  labeledControl(view.renderer, "관리자로서").props.onChange({
    target: { checked: true },
  }),
);
let apply = view.renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "apply");
await act(async () => apply.findByType("button").props.onClick());
const changed = structuredClone(fixture);
changed.ruleVersions[1].rules[0].threshold = 3123;
await act(async () =>
  view.cache.setQueryData(["detection-workbench", "admin"], changed),
);
await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
assert.equal(labeledControl(view.renderer, "관리자로서").props.checked, false);
apply = view.renderer.root
  .findAllByType(ServiceAction)
  .find((node) => node.props.operation === "apply");
assert.equal(apply.findByType(Modal).props.isOpen, false);
assert.equal(apply.props.requestDisabled, true);
assert.match(text(view.renderer.toJSON()), /재평가 필요/);
const diff = view.renderer.root.findByType(BundleDiff);
assert.match(text(diff), /3123/);
assert.ok(!text(diff).includes("99999"));
assert.notEqual(
  currentConfigurationFingerprint(changed.versions[1], changed),
  result.configurationFingerprint,
);
assert.equal(getRequestCount(), 0);
await view.close();
console.log(
  "detection composition tests passed: immutable artifacts, exact evaluation binding, handoff and merged drafts, recorded/next conditions, zero writes",
);
