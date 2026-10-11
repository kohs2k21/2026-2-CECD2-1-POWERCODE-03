import assert from "node:assert/strict";
import { act } from "react-test-renderer";
import { comparisonResult } from "../src/features/detection/data/evaluationComparison.ts";
import { conditionFromEvaluationSet } from "../src/features/detection/data/evaluationPreparation.ts";
import { receiptTaskHref } from "../src/features/detection/data/actionReceipt.ts";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { EvaluationConditionForm } from "../src/features/detection/pages/EvaluationConditionForm.tsx";
import { EvaluationComparison } from "../src/features/detection/pages/EvaluationComparison.tsx";
import { useDraftStore } from "../src/stores/draftStore.ts";
import {
  fixture,
  mount,
  text,
  labeledControl,
} from "./detection-operation-harness.mjs";

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
    fitLineage: "train-fit",
    testRunCount: null,
  },
];
data.evaluationSpecs = [
  {
    id: "spec-fixed",
    revision: "r1",
    name: "지표 기준",
    metrics: ["delayRecall"],
    metricDefinition: "detected / injected",
    labelSource: "label evidence",
    acceptanceCriteria: null,
  },
];
data.evaluationSets = [
  {
    id: "set-fixed",
    revision: "r1",
    name: "공통 세트",
    immutable: true,
    state: "ready",
    snapshotId: data.snapshots[0].id,
    splitManifestId: "split-fixed",
    splitRevision: "r1",
    specId: "spec-fixed",
    specRevision: "r1",
    purpose: "validation",
    scenarios: [],
    labelSource: "label evidence",
  },
];
const set = data.evaluationSets[0];
const condition = conditionFromEvaluationSet(set);
const active = data.versions.find((item) => item.id === data.activeVersionId);
const candidates = data.versions.filter((item) => item.state === "candidate");
for (const bundle of [active, ...candidates])
  data.evaluations.push({
    ...data.evaluations[0],
    id: `exact-${bundle.id}`,
    candidateId: bundle.id,
    condition,
    evaluationSetId: set.id,
    evaluationSetRevision: set.revision,
    configurationFingerprint: bundle.configurationFingerprint,
    state: "succeeded",
    requestedAt: "2026-10-10T00:10:00+09:00",
    metrics: {
      delayRecall: 0.7,
      stallRecall: null,
      burstRecall: null,
      latencyMs: null,
      unavailableRate: null,
    },
  });
const candidate = candidates[0];
const correct = comparisonResult(candidate, set, data);
for (const state of ["accepted", "queued", "running", "failed"]) {
  const latest = {
    ...correct,
    id: `latest-${state}`,
    state,
    requestedAt: "2026-10-10T01:00:00+09:00",
  };
  assert.equal(
    comparisonResult(candidate, set, {
      ...data,
      evaluations: [latest, correct],
    }).id,
    correct.id,
    "pending/failed attempts do not replace completed comparison evidence",
  );
}
assert.equal(
  correct.id,
  `exact-${candidate.id}`,
  "scan full result collection instead of bundle.evaluationId",
);
for (const patch of [
  { condition: { ...condition, purpose: "test" } },
  { condition: { ...condition, evaluationSetRevision: "old" } },
  { configurationFingerprint: "old" },
  { evaluationSetRevision: "different" },
]) {
  const changed = {
    ...correct,
    ...patch,
    id: "newer-mismatch",
    requestedAt: "2026-10-10T00:20:00+09:00",
  };
  assert.equal(
    comparisonResult(candidate, set, {
      ...data,
      evaluations: [changed, correct],
    }).id,
    correct.id,
  );
}
assert.equal(
  comparisonResult({ ...candidate, featureVersion: "changed" }, set, data),
  undefined,
);
assert.equal(
  receiptTaskHref({
    requestId: "request-not-job",
    state: "accepted",
    message: "",
  }),
  "/notifications?filter=tasks",
);
assert.equal(
  receiptTaskHref({
    requestId: "request-not-job",
    state: "accepted",
    message: "",
    job: {
      type: "evaluation",
      id: "real-eval-job",
      resultId: "real-eval-result",
    },
  }),
  "/detection/evaluation?tab=candidates&result=real-eval-result",
);
assert.equal(
  receiptTaskHref({
    requestId: "request-not-job",
    state: "accepted",
    message: "",
    job: { type: "evaluation", id: "queue-job-only" },
  }),
  "/notifications?filter=tasks",
);
assert.equal(
  receiptTaskHref({
    requestId: "request-not-job",
    state: "accepted",
    message: "",
    job: { type: "training", id: "real-train-job" },
  }),
  "/detection/create?tab=training&job=real-train-job",
);
const adapter = {
  read: async () => structuredClone(data),
  request: async () => {
    throw new Error("must not request");
  },
};
let view = await mount(
  `/detection/evaluation?tab=candidates&result=exact-${active.id}&retained=yes`,
  adapter,
);
assert.equal(
  view.renderer.root.findByType(EvaluationConditionForm).props.target.id,
  active.id,
);
assert.match(text(view.renderer.toJSON()), /기록된 평가 결과 상세/);
assert.match(text(view.renderer.toJSON()), /운영 중/);
await view.close();
data.versions.push({
  ...candidate,
  id: "archived-target",
  state: "archived",
  name: "과거 대상",
});
data.evaluations.push({
  ...correct,
  id: "archived-result",
  candidateId: "archived-target",
});
view = await mount(
  "/detection/evaluation?result=archived-result&retained=yes",
  adapter,
);
assert.equal(
  view.renderer.root.findByType(EvaluationConditionForm).props.target.id,
  "archived-target",
);
assert.match(text(view.renderer.toJSON()), /archived-result/);
await view.close();
view = await mount(
  `/detection/evaluation?tab=candidates&candidate=${candidate.id}&retained=yes`,
  adapter,
);
await act(async () =>
  labeledControl(view.renderer, "고정 평가 세트", "select").props.onChange({
    target: { value: "set-fixed@r1" },
  }),
);
await act(async () =>
  view.renderer.root
    .findAllByType("input")
    .find(
      (node) => node.props["aria-label"] === `${candidates[1].name} 비교 대상`,
    )
    .props.onChange({ target: { checked: true } }),
);
const panel = view.renderer.root.findByType(EvaluationComparison);
const action = panel.findByType(ServiceAction);
assert.equal(action.props.disabled, false);
assert.equal(action.props.payload.targets.length, 3);
assert.deepEqual(action.props.payload.condition, condition);
assert.match(text(panel), /70.0%/);
assert.match(text(panel), /—/);
assert.equal(
  new URLSearchParams(view.router.state.location.search).get("retained"),
  "yes",
);
await act(async () =>
  view.router.navigate("/detection/evaluation?tab=preparation", {
    state: view.router.state.location.state,
  }),
);
await act(async () =>
  labeledControl(view.renderer, "평가 세트 이름").props.onChange({
    target: { value: "함께 보존" },
  }),
);
const draft = useDraftStore.getState().drafts["/detection/evaluation"].value;
assert.ok(draft.comparison.targetIds.includes(candidates[1].id));
assert.equal(draft.conditionsByCandidate[candidate.id].evaluationSetId, set.id);
assert.equal(draft.preparation.name, "함께 보존");
await view.close();
console.log(
  "evaluation comparison passed: full result exact binding, multi selection, active/archived deep links, no invented receipt job IDs, merged draft siblings",
);
