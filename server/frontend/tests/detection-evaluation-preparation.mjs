import assert from "node:assert/strict";
import { act } from "react-test-renderer";
import { Link } from "react-router-dom";
import {
  inspectEvaluationCsv,
  evaluationCsvLimits,
} from "../src/features/detection/data/evaluationCsv.ts";
import {
  newEvaluationPreparation,
  preparationErrors,
  evaluationSetErrors,
  conditionFromEvaluationSet,
} from "../src/features/detection/data/evaluationPreparation.ts";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { EvaluationConditionForm } from "../src/features/detection/pages/EvaluationConditionForm.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
import {
  fixture,
  mount,
  text,
  labeledControl,
} from "./detection-operation-harness.mjs";

const csv = inspectEvaluationCsv(
  '\uFEFF"scenario",label,BODY,token\r\ndelay,anomaly,"private,body",secret\r\nnormal,unknown,private,secret',
);
assert.deepEqual(csv.errors, []);
assert.deepEqual(csv.metadata.columns, ["scenario", "label"]);
assert.equal(csv.metadata.rowCount, 2);
assert.ok(!JSON.stringify(csv.metadata).includes("private"));
for (const input of [
  'scenario,label\ndelay,"anomaly',
  "scenario,scenario\ndelay,anomaly",
  "scenario,label\ndelay,maybe",
  "scenario,label\ndelay,anomaly,extra",
])
  assert.equal(inspectEvaluationCsv(input).metadata, null);
assert.equal(
  inspectEvaluationCsv("x".repeat(evaluationCsvLimits.bytes + 1)).metadata,
  null,
);
assert.equal(
  inspectEvaluationCsv(`scenario,label\n${"delay,anomaly\n".repeat(10001)}`)
    .metadata,
  null,
);
const data = structuredClone(fixture);
const part = { start: null, end: null, rowCount: null, executionCount: null };
data.evaluationSplits = [
  {
    id: "split-fixed",
    revision: "r1",
    snapshotId: data.snapshots[0].id,
    train: part,
    validation: part,
    test: part,
    overlapChecked: true,
    trainOnlyFit: true,
    fitLineage: "train-fit-1",
    testRunCount: null,
  },
];
data.evaluationSpecs = [
  {
    id: "spec-fixed",
    revision: "r1",
    name: "검토 기준",
    metrics: ["delayRecall"],
    metricDefinition: "detected / injected",
    labelSource: "검토한 라벨",
    acceptanceCriteria: null,
  },
];
data.evaluationSets = [
  {
    id: "set-fixed",
    revision: "r1",
    name: "불변 세트",
    immutable: true,
    state: "ready",
    snapshotId: data.snapshots[0].id,
    splitManifestId: "split-fixed",
    splitRevision: "r1",
    specId: "spec-fixed",
    specRevision: "r1",
    purpose: "validation",
    scenarios: [],
    labelSource: "검토한 라벨",
  },
];
assert.deepEqual(evaluationSetErrors(data.evaluationSets[0], data), []);
assert.ok(
  evaluationSetErrors({ ...data.evaluationSets[0], immutable: false }, data)
    .length,
);
assert.ok(
  evaluationSetErrors({ ...data.evaluationSets[0], splitRevision: "old" }, data)
    .length,
);
assert.ok(preparationErrors(newEvaluationPreparation(), data).length);
assert.ok(
  preparationErrors(
    {
      ...newEvaluationPreparation(),
      name: "CSV only",
      snapshotId: data.snapshots[0].id,
      splitManifestId: "split-fixed",
      specId: "spec-fixed",
      csv: csv.metadata,
    },
    data,
  ).some((error) => error.includes("CSV 확인 정보만")),
  "CSV metadata cannot generate a server test source",
);
let view = await mount("/detection/evaluation?tab=preparation&retained=yes", {
  read: async () => structuredClone(data),
  request: async () => {
    throw new Error("must not request");
  },
});
await act(async () =>
  labeledControl(view.renderer, "평가 세트 이름").props.onChange({
    target: { value: "보존 초안" },
  }),
);
await act(async () =>
  view.router.navigate(
    "/detection/evaluation?tab=candidates&candidate=candidate-delay",
  ),
);
await act(async () =>
  labeledControl(view.renderer, "고정 평가 세트", "select").props.onChange({
    target: { value: "set-fixed@r1" },
  }),
);
const form = view.renderer.root.findByType(EvaluationConditionForm);
assert.deepEqual(
  form.findByType(ServiceAction).props.payload.condition,
  conditionFromEvaluationSet(data.evaluationSets[0]),
);
assert.equal(form.findByType(ServiceAction).props.disabled, false);
assert.equal(form.findByType(ServiceAction).props.available, false);
assert.equal(
  form.findAllByType("input").length,
  0,
  "all fixed set conditions readonly",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.preparation
    .name,
  "보존 초안",
);
await act(async () =>
  view.router.navigate("/detection/evaluation?tab=preparation"),
);
assert.equal(
  labeledControl(view.renderer, "평가 세트 이름").props.value,
  "보존 초안",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value
    .conditionsByCandidate["candidate-delay"].evaluationSetId,
  "set-fixed",
);
assert.match(text(view.renderer.toJSON()), /합격선/);
assert.ok(!text(view.renderer.toJSON()).includes("평가 성공"));
await view.close();
data.preparationCapabilities.createEvaluationSet = true;
view = await mount("/detection/evaluation?tab=candidates&retained=yes", {
  read: async () => structuredClone(data),
  request: async () => {
    throw new Error("must not request");
  },
});
const candidateLink = view.renderer.root
  .findAllByType(Link)
  .find((node) => node.props.to.includes("candidate=candidate-delay"));
assert.equal(
  new URLSearchParams(candidateLink.props.to.slice(1)).get("tab"),
  "candidates",
);
await act(async () =>
  view.router.navigate("/detection/evaluation?tab=preparation"),
);
await act(async () =>
  useDraftStore.getState().edit("/detection/evaluation", {
    editor: "evaluation",
    preparation: {
      ...newEvaluationPreparation(),
      name: "유효 준비",
      snapshotId: data.snapshots[0].id,
      splitManifestId: "split-fixed",
      specId: "spec-fixed",
      csv: csv.metadata,
      scenarios: [
        {
          id: "delay-1",
          kind: "delay",
          targetField: "process.END_TIME",
          condition: "선택 대상",
          intensity: "3000 ms",
          count: "1",
          seed: "0",
        },
      ],
    },
  }),
);
const action = () =>
  view.renderer.root
    .findAllByType(ServiceAction)
    .find((node) => node.props.operation === "createEvaluationSet");
const upload = (file) =>
  view.renderer.root
    .findAllByType("input")
    .find((node) => node.props.type === "file")
    .props.onChange({ target: { files: [file], value: "file.csv" } });
assert.equal(action().props.disabled, false);
let resolveFile;
await act(async () =>
  upload({
    size: 20,
    text: () =>
      new Promise((resolve) => {
        resolveFile = resolve;
      }),
  }),
);
assert.equal(
  action().props.disabled,
  true,
  "pending inspection blocks request even with valid scenarios",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.preparation
    .csv,
  null,
);
await act(async () => resolveFile("scenario,label\ndelay,invalid"));
assert.equal(
  action().props.disabled,
  true,
  "invalid replacement blocks request and clears old CSV metadata",
);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.preparation
    .csv,
  null,
);
await act(async () =>
  upload({ size: 25, text: async () => "scenario,label\ndelay,anomaly" }),
);
assert.equal(action().props.disabled, false);
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.preparation.csv
    .rowCount,
  1,
);
await act(async () =>
  upload({
    size: 20,
    text: () =>
      new Promise((resolve) => {
        resolveFile = resolve;
      }),
  }),
);
await act(async () =>
  view.router.navigate("/detection/evaluation?tab=candidates"),
);
await act(async () => resolveFile("scenario,label\ndelay,anomaly"));
assert.equal(
  useDraftStore.getState().drafts["/detection/evaluation"].value.preparation
    .csv,
  null,
  "late file result after tab unmount is ignored",
);
await view.close();
console.log(
  "evaluation preparation passed: bounded CSV, metadata only, fixed readonly conditions, merged local drafts, no service success",
);
