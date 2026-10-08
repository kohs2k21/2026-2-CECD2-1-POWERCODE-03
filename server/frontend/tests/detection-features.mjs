import assert from "node:assert/strict";
import {
  createDefaultDraft,
  validateDataDraft,
  validateTrainingConfig,
  validateRule,
} from "../src/features/detection/data/createDraft.ts";
import {
  defaultFeatureEditor,
  previewFeature,
  validateFeatureEditor,
  canEditCanonicalFeature,
} from "../src/features/detection/data/featureBuilder.ts";
import { featureCatalog } from "../src/features/detection/data/catalog.ts";
const duration = {
  ...defaultFeatureEditor(
    featureCatalog.find((feature) => feature.id === "A00"),
  ),
  timezone: "Asia/Seoul",
};
assert.equal(duration.left, "process.END_TIME");
assert.equal(duration.right, "process.START_TIME");
for (const [id, operation] of [
  ["F03", "weekend"],
  ["F23", "log1p"],
  ["A01", "month"],
  ["A02", "weekday"],
  ["F05", "ratio"],
]) {
  const feature = featureCatalog.find((item) => item.id === id);
  assert.equal(canEditCanonicalFeature(feature), true);
  assert.equal(defaultFeatureEditor(feature).operation, operation);
}
for (const id of ["F07", "F22", "A06", "A14"])
  assert.equal(
    canEditCanonicalFeature(featureCatalog.find((item) => item.id === id)),
    false,
  );
assert.equal(
  previewFeature(duration, "2026-10-08T00:00:01Z", "2026-10-08T00:00:00Z")
    .value,
  1000,
);
assert.equal(
  previewFeature(
    { ...duration, unit: "s" },
    "2026-10-08T00:00:01Z",
    "2026-10-08T00:00:00Z",
  ).value,
  1,
);
assert.equal(
  previewFeature(duration, "2026-10-08T00:00:00Z", "2026-10-08T00:00:01Z")
    .value,
  null,
);
assert.equal(
  previewFeature(duration, "2026-10-08T00:00:00", "2026-10-08T00:00:01").value,
  null,
  "timezone-less raw string must not be silently parsed",
);
assert.equal(
  previewFeature(
    { ...duration, timezone: "" },
    "2026-10-08T00:00:01Z",
    "2026-10-08T00:00:00Z",
  ).value,
  null,
);
const ratio = {
  ...duration,
  name: "successRatio",
  operation: "ratio",
  left: "process.SUCCESS_COUNT",
  right: "process.TOTAL_COUNT",
  unit: "unitless",
};
assert.equal(previewFeature(ratio, "7", "10").value, 0.7);
for (const operation of ["ratio", "subtract"])
  assert.equal(
    previewFeature({ ...ratio, operation, right: "F22" }, "7", "2").value,
    null,
    "right dependency must also require fit before preview",
  );
assert.equal(previewFeature(ratio, "7", "0").value, null);
assert.equal(
  previewFeature(ratio, "", "10").value,
  null,
  "NULL must not become zero",
);
assert.equal(
  previewFeature({ ...ratio, operation: "log1p" }, "-1", "").value,
  null,
);
assert.equal(
  previewFeature({ ...ratio, operation: "trainMedian" }, "12", "").value,
  null,
  "no fit statistics must not produce a measured preview",
);
assert.equal(
  previewFeature({ ...ratio, left: "F22", operation: "log1p" }, "12", "").value,
  null,
  "dependency requiring fit must also remain unmeasured",
);
assert.ok(
  validateFeatureEditor({ ...ratio, left: "process.PROCESS_ID" }).length > 0,
  "identifier cannot be treated as number",
);
assert.ok(validateFeatureEditor({ ...duration, name: "   " }).length > 0);
assert.ok(validateDataDraft(createDefaultDraft()).length > 0);
const configured = {
  ...createDefaultDraft(),
  snapshotId: "snapshot-september",
  start: "2026-09-01",
  end: "2026-09-30",
};
assert.deepEqual(validateDataDraft(configured), []);
assert.ok(validateDataDraft({ ...configured, end: "2026-08-01" }).length > 0);
assert.deepEqual(validateTrainingConfig(configured.training), []);
for (const patch of [
  { trees: "0" },
  { trees: "1.2" },
  { trees: "Infinity" },
  { contamination: " " },
  { contamination: "NaN" },
  { contamination: "1" },
  { seed: "-1" },
])
  assert.ok(
    validateTrainingConfig({ ...configured.training, ...patch }).length > 0,
  );
for (const threshold of [null, NaN, -1])
  assert.ok(
    validateRule({
      id: "a",
      name: "rule",
      field: "A00",
      operator: "gt",
      threshold,
      unit: "ms",
      enabled: true,
      description: "",
    }).length > 0,
  );
console.log(
  "feature builder QA passed: time units/timezone/negative duration, zero denominator, NULL preservation, fit dependency blocking, typed allowed operations and form bounds",
);
