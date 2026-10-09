import assert from "node:assert/strict";
import { act } from "react-test-renderer";
import {
  performanceAssessment,
  expectedVersionReview,
} from "../src/features/detection/data/performanceCriteria.ts";
import { conditionFromEvaluationSet } from "../src/features/detection/data/evaluationPreparation.ts";
import { ServiceAction } from "../src/features/detection/components/ServiceAction.tsx";
import { Modal } from "../src/components/ui/Modal.tsx";
import {
  BundleDiff,
  configurationChangeCount,
} from "../src/features/detection/pages/VersionDetails.tsx";
import { versionReadiness } from "../src/features/detection/pages/operationPresentation.ts";
import {
  fixture,
  mount,
  text,
  labeledControl,
} from "./detection-operation-harness.mjs";

const data = structuredClone(fixture);
const candidate = data.versions[1];
const part = { start: null, end: null, rowCount: null, executionCount: null };
data.evaluationSplits = [
  {
    id: "review-split",
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
    id: "review-spec",
    revision: "r1",
    name: "확정 기준",
    metrics: ["delayRecall"],
    metricDefinition: "detected / injected",
    labelSource: "reviewed labels",
    acceptanceCriteria: [
      {
        metric: "delayRecall",
        operator: "gte",
        value: 0.8,
        source: "팀원 합의 v1",
      },
    ],
  },
];
data.evaluationSets = [
  {
    id: "review-set",
    revision: "r1",
    name: "최종 확인 세트",
    immutable: true,
    state: "ready",
    snapshotId: data.snapshots[0].id,
    splitManifestId: "review-split",
    splitRevision: "r1",
    specId: "review-spec",
    specRevision: "r1",
    purpose: "test",
    scenarios: [],
    labelSource: "reviewed labels",
  },
];
const result = data.evaluations.find(
  (item) => item.id === candidate.evaluationId,
);
result.condition = conditionFromEvaluationSet(data.evaluationSets[0]);
result.evaluationSetId = "review-set";
result.evaluationSetRevision = "r1";
result.configurationFingerprint = candidate.configurationFingerprint;
result.state = "succeeded";
result.metrics.delayRecall = 0.9;
assert.equal(performanceAssessment(candidate, data).passed, true);
assert.equal(expectedVersionReview(candidate, data).evaluationId, result.id);
const permissive = structuredClone(data);
permissive.evaluationSpecs[0].acceptanceCriteria[0].value = 0.75;
assert.equal(
  performanceAssessment(permissive.versions[1], permissive).passed,
  true,
);
assert.notDeepEqual(
  expectedVersionReview(candidate, data),
  expectedVersionReview(permissive.versions[1], permissive),
  "same-id changed criterion content changes final precondition",
);
assert.equal(
  expectedVersionReview(candidate, fixture),
  null,
  "missing fixed evidence cannot produce an approval precondition",
);
const unknownLeft = { ...candidate, modelArtifactId: "unavailable-artifact-A" };
const unknownRight = {
  ...candidate,
  modelArtifactId: "unavailable-artifact-B",
};
assert.equal(
  configurationChangeCount(unknownLeft, unknownRight, data),
  1,
  "different missing artifact IDs remain different configurations",
);
assert.ok(
  !versionReadiness(candidate, data).some((reason) =>
    reason.includes("지표가 모두"),
  ),
  "only specified metrics required",
);
const check = (change) => {
  const altered = structuredClone(data);
  change(altered);
  return performanceAssessment(altered.versions[1], altered);
};
assert.equal(
  check((changed) => (changed.evaluationSpecs[0].acceptanceCriteria = null))
    .passed,
  false,
);
assert.equal(
  check((changed) => (changed.evaluations[0].metrics.delayRecall = null))
    .passed,
  false,
);
assert.equal(
  check((changed) => (changed.evaluations[0].metrics.delayRecall = 0.7)).passed,
  false,
);
assert.equal(
  check(
    (changed) =>
      (changed.evaluationSpecs[0].acceptanceCriteria[0].source = " "),
  ).confirmed,
  false,
);
assert.equal(
  check(
    (changed) => (changed.evaluationSpecs[0].acceptanceCriteria[0].value = NaN),
  ).confirmed,
  false,
);
assert.equal(
  check((changed) => {
    changed.evaluationSets[0].purpose = "validation";
    changed.evaluations[0].condition.purpose = "validation";
  }).passed,
  false,
);
assert.equal(
  check(
    (changed) => (changed.evaluations[0].configurationFingerprint = "stale"),
  ).passed,
  false,
);

const adapter = {
  read: async () => structuredClone(data),
  request: async () => {
    throw new Error("must not request");
  },
};
let view = await mount(
  `/detection/versions?tab=configuration&version=${data.versions[2].id}&retained=yes`,
  adapter,
);
assert.match(text(view.renderer.toJSON()), /선택한 버전 구성/);
assert.match(text(view.renderer.toJSON()), new RegExp(data.versions[2].id));
await act(async () =>
  view.router.navigate("/detection/versions?tab=configuration&version=missing"),
);
assert.match(text(view.renderer.toJSON()), /선택한 항목을 찾을 수 없습니다/);
assert.ok(
  !text(view.renderer.toJSON()).includes("현재 운영 버전"),
  "missing historical id never falls back to current",
);
await act(async () =>
  view.router.navigate("/detection/versions?tab=configuration&version="),
);
assert.match(text(view.renderer.toJSON()), /선택한 항목을 찾을 수 없습니다/);
await view.close();
view = await mount(
  `/detection/versions?tab=application&candidate=${candidate.id}`,
  adapter,
);
assert.match(text(view.renderer.toJSON()), /성능기준 확정/);
assert.match(text(view.renderer.toJSON()), /확정 기준 충족/);
assert.ok(view.renderer.root.findAllByType("details").length > 0);
const diff = view.renderer.root.findByType(BundleDiff);
const beforeRows = diff.findAllByType("tbody")[0].findAllByType("tr").length;
await act(async () =>
  labeledControl(view.renderer, "변경 항목만 보기").props.onChange({
    target: { checked: true },
  }),
);
assert.ok(
  diff.findAllByType("tbody")[0].findAllByType("tr").length <= beforeRows,
);
const applyAction = () =>
  view.renderer.root
    .findAllByType(ServiceAction)
    .find((item) => item.props.operation === "apply");
for (const field of ["evaluationSets", "evaluationSpecs", "evaluationSplits"]) {
  await act(async () =>
    labeledControl(view.renderer, "관리자로서").props.onChange({
      target: { checked: true },
    }),
  );
  await act(async () => applyAction().findByType("button").props.onClick());
  const previousReview = structuredClone(
    applyAction().props.payload.expectedReview,
  );
  const changed = structuredClone(data);
  if (field === "evaluationSets") changed[field][0].labelSource = "다른 라벨";
  if (field === "evaluationSpecs")
    changed[field][0].acceptanceCriteria[0].value = 0.95;
  if (field === "evaluationSplits") changed[field][0].fitLineage = "다른 근거";
  await act(async () =>
    view.cache.setQueryData(["detection-workbench", "admin"], changed),
  );
  await act(async () => new Promise((resolve) => setTimeout(resolve, 12)));
  assert.equal(
    labeledControl(view.renderer, "관리자로서").props.checked,
    false,
  );
  assert.equal(applyAction().findByType(Modal).props.isOpen, false);
  assert.equal(
    applyAction().props.payload.expectedActiveVersionId,
    data.activeVersionId,
  );
  assert.equal(applyAction().props.requestDisabled, true);
  assert.notDeepEqual(
    applyAction().props.payload.expectedReview,
    previousReview,
  );
  await act(async () =>
    view.cache.setQueryData(
      ["detection-workbench", "admin"],
      structuredClone(data),
    ),
  );
}
await view.close();
console.log(
  "version review passed: separate processing and performance criteria, truthful unavailable values, changed-only/collapsed details, historical id lookup, criterion/split/set approval reset",
);
