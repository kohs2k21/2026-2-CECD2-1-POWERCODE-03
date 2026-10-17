import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { clearJobRequestKeys, forgetJobRequestKey, jobRequestKey } from "../src/services/api/job-idempotency.ts";
import { createPipelineJob } from "../src/services/api/pipeline.api.ts";
import { TrainerJobResult } from "../src/features/pipeline/TrainerJobResult.tsx";
import { clearStoredToken, storeToken } from "../src/services/auth/session.ts";

globalThis.React = React;
const session = new Map();
const local = new Map([["token", "first-session"]]);
globalThis.window = {
  location: { origin: "http://local.test" },
  sessionStorage: { getItem: (key) => session.get(key) ?? null, setItem: (key, value) => session.set(key, value), removeItem: (key) => session.delete(key) },
  localStorage: { getItem: (key) => local.get(key) ?? null, setItem: (key, value) => local.set(key, value), removeItem: (key) => local.delete(key) },
};

const input = { kind: "train", sourceId: "source-a", featureSetId: "set-a", modelKey: "iforest", detectionPolicy: { mode: "ml_only", deadlineMs: 3000, combination: "selected_path" } };
const key = jobRequestKey("actor-a", input);
assert.equal(key, jobRequestKey("actor-a", { ...input, detectionPolicy: { combination: "selected_path", mode: "ml_only", deadlineMs: 3000 } }));
assert.notEqual(key, jobRequestKey("actor-b", input));
assert.notEqual(key, jobRequestKey("actor-a", { ...input, sourceId: "source-b" }));
assert.notEqual(key, jobRequestKey("actor-a", { ...input, featureSetId: "set-b" }));
const renewed = jobRequestKey("actor-a", input, true);
assert.notEqual(key, renewed);
assert.equal(renewed, jobRequestKey("actor-a", input));
const action = { ...input, newExecutionOf: "old-job" };
const actionKey = jobRequestKey("actor-a", action);
assert.notEqual(renewed, actionKey);
assert.equal(actionKey, jobRequestKey("actor-a", action));
forgetJobRequestKey("actor-a", action);
assert.notEqual(actionKey, jobRequestKey("actor-a", action));
storeToken("second-session");
assert.equal(session.size, 0);
assert.notEqual(renewed, jobRequestKey("actor-a", input));
clearStoredToken();
assert.equal(session.size, 0);

let fail = true;
const headers = [];
globalThis.fetch = async (_url, options) => {
  headers.push(options.headers.get("Idempotency-Key"));
  if (fail) throw new TypeError("connection lost");
  return new Response(JSON.stringify({ id: "job-a", status: "queued", kind: "train", inputSpec: input }));
};
const retryKey = jobRequestKey("actor-a", input);
await assert.rejects(createPipelineJob(input, retryKey));
fail = false;
await createPipelineJob(input, jobRequestKey("actor-a", input));
assert.deepEqual(headers, [retryKey, retryKey]);

const job = { id: "job-a", kind: "evaluate", status: "succeeded", inputSpec: {}, resultSummary: {
  evaluationId: "evaluation-a", configVersionId: "candidate-a", snapshotId: "snapshot-a",
  evaluationEnvelope: {
    rule_ml: { status: "unavailable", reason: "not_implemented", metrics: { fake: 0 }, metricVersion: "v1" },
    synthetic: { status: "failed", reason: "evaluation_failed", metrics: null, metricVersion: "v1" },
    execution_cost: { status: "measured", reason: null, metrics: {
      count: { value: 0, unit: "count", numerator: 0, denominator: 10, coverage: 1, reason: null },
      duration: { value: null, unit: "ms", numerator: null, denominator: null, coverage: null, reason: "not_observed" },
    }, metricVersion: "v1" },
    operational: { status: "not_applicable", reason: "no_operation", metrics: null, metricVersion: "v1" },
  },
} };
const markup = renderToStaticMarkup(React.createElement(TrainerJobResult, { job }));
assert(markup.includes("candidate-a") && markup.includes("evaluation-a") && markup.includes("snapshot-a"));
assert(markup.includes("미측정") && markup.includes("측정 실패") && markup.includes("해당 없음"));
assert(markup.includes("count: 0") && markup.includes("duration: 미측정"));
assert(!markup.includes("fake: 0"));
const unmeasured = renderToStaticMarkup(React.createElement(TrainerJobResult, { job: { ...job, resultSummary: {
  applicability: "unmeasured", reasonCodes: ["insufficient_labeled_population"], metrics: { tp: 0, fp: 0, tn: 0, fn: 0, precision: 0, recall: 0 },
} } }));
assert(unmeasured.includes("미측정") && unmeasured.includes("insufficient_labeled_population"));
assert(!unmeasured.includes("Precision:") && !unmeasured.includes("Recall:"));
const noPredictions = renderToStaticMarkup(React.createElement(TrainerJobResult, { job: { ...job, resultSummary: {
  applicability: "valid", metrics: { tp: 0, fp: 0, tn: 10, fn: 3, precision: 0, recall: 0, falsePositiveRate: 0, model: { precision: 0 } },
} } }));
assert(noPredictions.includes("Precision: 미측정") && noPredictions.includes("Recall: 0") && noPredictions.includes("FPR: 0"));
assert(!noPredictions.includes("model:"));
const legacy = renderToStaticMarkup(React.createElement(TrainerJobResult, { job: { ...job, resultSummary: {} } }));
assert.equal((legacy.match(/미측정/g) ?? []).length, 4);
clearJobRequestKeys();
console.log("PASS: actor/source/payload idempotency, network retries, explicit new execution, logout isolation, unavailable/failed/null/zero result display");
