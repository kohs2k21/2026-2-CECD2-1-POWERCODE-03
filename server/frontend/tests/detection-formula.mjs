import assert from "node:assert/strict";
import {
  compileFormula,
  evaluateFormula,
  legacyEditorExpression,
  featureOptions,
  featureDefinitionPayload,
  FORMULA_LIMITS,
} from "../src/features/detection/data/formula.ts";
import {
  createDefaultDraft,
  appendCustomFeature,
  removeCustomFeature,
} from "../src/features/detection/data/createDraft.ts";
import {
  defaultFeatureEditor,
  validateFeatureEditor,
} from "../src/features/detection/data/featureBuilder.ts";
import { featureCatalog } from "../src/features/detection/data/catalog.ts";

const fields = [
  {
    id: "x",
    name: "수치",
    type: "number",
    unit: "unitless",
    readiness: "ready",
  },
  {
    id: "y",
    name: "분모",
    type: "number",
    unit: "unitless",
    readiness: "ready",
  },
  {
    id: "process.START_TIME",
    name: "시작",
    type: "timestamp",
    unit: "ISO8601",
    readiness: "ready",
  },
  {
    id: "process.END_TIME",
    name: "종료",
    type: "timestamp",
    unit: "ISO8601",
    readiness: "ready",
  },
  {
    id: "label",
    name: "분류",
    type: "category",
    unit: "unitless",
    readiness: "ready",
  },
  {
    id: "identifier",
    name: "고유ID",
    type: "identifier",
    unit: "unitless",
    readiness: "unsupported",
  },
];
const compile = (source, features = fields) => compileFormula(source, features);
const calculate = (source, samples = {}, options = {}) =>
  evaluateFormula(compile(source), samples, options);
assert.equal(calculate("1 + 2 * 3").value, 7);
assert.equal(calculate("-(1 + 2) / .5").value, -6);
assert.equal(calculate("[x] + y", { x: " 4 ", y: 2 }).value, 6);
assert.equal(
  calculate("if([x] > 0, log1p([x]), 0)", { x: 3 }).value,
  Math.log1p(3),
);
assert.equal(compile("[x] > 0").outputType, "boolean");
assert.equal(compile("is_missing([x])").outputType, "boolean");
assert.equal(calculate("if(is_missing([x]), 1, 0)", { x: null }).value, 1);
assert.equal(calculate("[label] == 'delay'", { label: "delay" }).value, true);
assert.equal(
  calculate("[identifier] == 'abc'", { identifier: "abc" }).value,
  true,
);
for (const formula of [
  "[identifier]+1",
  "[label]*2",
  "if([x],1,0)",
  "if(true,1,'bad')",
  "evil([x])",
  "Math.random()",
  "[not_found]",
  "[x].constructor",
  "1;globalThis.hacked=true",
  "1e309",
  "if(true,1)",
  "1 +",
])
  assert.ok(compile(formula).errors.length, formula);
for (const samples of [
  { x: true },
  { x: "NaN" },
  { x: "Infinity" },
  { x: "0x10" },
  { x: Infinity },
])
  assert.equal(calculate("[x]+1", samples).value, null);
assert.equal(calculate("[x]/[y]", { x: 1, y: 0 }).value, null);
assert.match(calculate("[x]/[y]", { x: 1, y: 0 }).reason, /분모/);
assert.equal(calculate("[x]+1", { x: "" }).value, null);
assert.match(
  calculate("[x]+1", { x: null }, { missingPolicy: "reject" }).reason,
  /필수/,
);
assert.equal(calculate("[x] == null", { x: null }).value, null);
assert.equal(
  calculate("if(true,1,1/0)").value,
  1,
  "only selected branch is evaluated",
);
assert.equal(calculate("log1p([x])", { x: -1 }).value, null);
assert.equal(calculate("1e308 * 1e308").value, null);
const times = {
  "process.START_TIME": "2026-10-09T09:00:00+09:00",
  "process.END_TIME": "2026-10-09T00:00:03Z",
};
const duration = compile("duration_ms(PROCESS.END_TIME, [PROCESS.START_TIME])");
assert.deepEqual(duration.inputIds, ["process.END_TIME", "process.START_TIME"]);
assert.equal(evaluateFormula(duration, times).value, 3000);
assert.equal(duration.unit, "ms");
assert.equal(
  calculate("duration_s([process.END_TIME],[process.START_TIME])", times).value,
  3,
);
assert.equal(
  compile("duration_s([process.END_TIME],[process.START_TIME])").unit,
  "s",
);
assert.equal(
  calculate("[process.END_TIME] == [process.START_TIME]", {
    ...times,
    "process.END_TIME": "2026-10-09T00:00:00Z",
  }).value,
  true,
  "timestamp equality normalizes offsets",
);
assert.equal(
  calculate("hour([process.START_TIME])", times, { timezone: "Asia/Seoul" })
    .value,
  9,
);
assert.equal(
  calculate("hour([process.START_TIME])", times, { timezone: "UTC" }).value,
  0,
);
assert.equal(calculate("hour([process.START_TIME])", times).value, null);
for (const invalid of [
  "2026-02-30T00:00:00Z",
  "2026-13-01T00:00:00Z",
  "2026-10-09T24:00:00Z",
  "2026-10-09T00:00:00",
  "2026-10-09T00:00:00+14:01",
])
  assert.equal(
    calculate("duration_ms([process.END_TIME],[process.START_TIME])", {
      ...times,
      "process.END_TIME": invalid,
    }).value,
    null,
    invalid,
  );
assert.equal(
  calculate("duration_ms([process.START_TIME],[process.END_TIME])", times)
    .value,
  null,
);
for (const fn of ["train_median", "frequency_encode", "robust_z"]) {
  const fit = compile(`${fn}([x])`);
  assert.equal(fit.fitRequired, true);
  assert.equal(evaluateFormula(fit, { x: 123 }).value, null);
}
const nested = [
  ...fields,
  {
    id: "custom-1",
    name: "사용자1",
    type: "number",
    unit: "unitless",
    expression: "[x]*2",
    readiness: "ready",
  },
  {
    id: "custom-2",
    name: "사용자2",
    type: "number",
    unit: "unitless",
    expression: "[custom-1]+1",
    readiness: "ready",
  },
];
const linked = compile("[custom-2]", nested);
assert.equal(
  evaluateFormula(linked, { x: 3, "custom-2": 999 }).value,
  7,
  "derived sample cannot override definition",
);
assert.deepEqual(linked.sampleInputIds, ["x"]);
assert.ok(linked.inputIds.includes("custom-1"));
const cycle = nested.map((field) =>
  field.id === "custom-1" ? { ...field, expression: "[custom-2]" } : field,
);
const timeDependencies = [
  ...fields,
  {
    id: "custom-hour",
    name: "Seoul시간",
    type: "number",
    unit: "unitless",
    readiness: "ready",
    expression: "hour([process.START_TIME])",
    timezone: "Asia/Seoul",
  },
  {
    id: "custom-hour-parent",
    name: "UTC부모",
    type: "number",
    unit: "unitless",
    readiness: "ready",
    expression: "[custom-hour]+1",
    timezone: "UTC",
  },
  {
    id: "custom-time-equality",
    name: "동일시각",
    type: "boolean",
    unit: "unitless",
    readiness: "ready",
    expression: "[process.END_TIME] == [process.START_TIME]",
  },
];
assert.equal(
  evaluateFormula(compile("[custom-hour-parent]", timeDependencies), times, {
    timezone: "UTC",
  }).value,
  10,
  "child definition owns timezone independently of caller",
);
assert.equal(
  evaluateFormula(compile("[custom-time-equality]", timeDependencies), {
    ...times,
    "process.END_TIME": "2026-10-09T00:00:00Z",
  }).value,
  true,
  "nested equality preserves typed AST annotations",
);
assert.ok(
  compile(
    "[custom-hour-parent]",
    timeDependencies.map((item) =>
      item.id === "custom-hour" ? { ...item, timezone: undefined } : item,
    ),
  ).errors.length,
  "unknown child timezone cannot inherit parent's configuration",
);
const rejecting = [
  ...nested.map((item) =>
    item.id === "custom-1" ? { ...item, missingPolicy: "reject" } : item,
  ),
];
assert.match(
  evaluateFormula(compile("is_missing([custom-2])", rejecting), { x: null })
    .reason,
  /필수/,
);
assert.match(compile("[custom-2]", cycle).errors[0], /순환/);
const fitted = nested.map((field) =>
  field.id === "custom-1" ? { ...field, expression: "robust_z([x])" } : field,
);
assert.equal(compile("[custom-2]", fitted).fitRequired, true);
const unknown = nested.map((field) =>
  field.id === "x"
    ? { ...field, readiness: "unconfirmed", reason: "정의 미확인" }
    : field,
);
assert.ok(compile("[custom-2]", unknown).errors.length);
assert.ok(compile("1".repeat(FORMULA_LIMITS.length + 1)).errors.length);
assert.ok(compile("(".repeat(33) + "1" + ")".repeat(33)).errors.length);
assert.ok(compile(Array(300).fill("1").join("+")).errors.length);
for (const unit of ["ms", "s"]) {
  const editor = {
    ...defaultFeatureEditor(featureCatalog.find((item) => item.id === "A00")),
    unit,
    timezone: "UTC",
  };
  const compiled = compile(legacyEditorExpression(editor));
  assert.equal(compiled.unit, unit);
  assert.equal(evaluateFormula(compiled, times).value, unit === "s" ? 3 : 3000);
}
const missing = {
  ...defaultFeatureEditor(),
  operation: "isMissing",
  left: "x",
};
assert.equal(calculate(legacyEditorExpression(missing), { x: null }).value, 1);
assert.equal(calculate(legacyEditorExpression(missing), { x: 0 }).value, 0);
const original = createDefaultDraft();
const first = appendCustomFeature(original, {
  ...defaultFeatureEditor(),
  name: "first",
  expression: "[process.TOTAL_COUNT] + 1",
});
const second = appendCustomFeature(first.draft, {
  ...defaultFeatureEditor(),
  name: "second",
  expression: `[${first.id}] * 2`,
});
const deleted = removeCustomFeature(second.draft, first.id);
assert.equal(
  deleted.featureEdits[second.id].expression,
  `[${first.id}] * 2`,
  "delete must never rewrite user's formula text",
);
assert.ok(
  validateFeatureEditor(
    deleted.featureEdits[second.id],
    featureOptions(deleted),
  ).length,
);
const payload = featureDefinitionPayload(second.draft);
assert.equal(payload[second.id].compiled.version, "formula-v1");
assert.ok(payload[second.id].compiled.ast);
assert.ok(payload[second.id].compiled.inputIds.includes(first.id));
assert.equal(
  payload[second.id].expression,
  second.draft.featureEdits[second.id].expression,
);
assert.equal(payload[second.id].compiled.outputType, "number");
console.log(
  "formula QA passed: bounded parser, safe typed arithmetic, NULL, units, ISO/calendar/offset, legacy equivalence, derived graph/cycle/missingref, fit isolation and serialized definitions",
);
