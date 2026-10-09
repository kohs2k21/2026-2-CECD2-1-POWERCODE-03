import assert from "node:assert/strict";
import {
  parseAnomalyEvidence,
  readAnomalyEvidence,
  saveAnomalyFeedback,
  feedbackWriteAllowed,
  validFeedbackDraft,
} from "../src/features/analysis/data/anomalyEvidence.ts";
const detail = parseAnomalyEvidence(
  {
    eventId: "id/1",
    configurationVersion: "v4",
    decisionState: "partial",
    rule: {
      execution: "completed",
      version: "r3",
      measurements: [
        {
          name: "처리 시간",
          value: 500,
          threshold: 300,
          unit: "ms",
          rawBody: "removed",
        },
      ],
    },
    ml: { execution: "unavailable", reason: "입력 피처 부족" },
    workflowStatus: "Open",
    capabilities: { saveFeedback: true },
    hypotheses: ["하위 프로세스 지연 가능성", null, { BODY: "removed" }],
    BODY: "removed",
  },
  "id/1",
);
assert.equal(detail.configurationVersion, "v4");
assert.equal(detail.decisionState, "partial");
assert.equal(detail.ml.execution, "unavailable");
assert.equal(detail.BODY, undefined);
assert.equal(detail.rule.measurements[0].rawBody, undefined);
assert.deepEqual(detail.hypotheses, ["하위 프로세스 지연 가능성"]);
assert.equal(feedbackWriteAllowed("user", true), false);
assert.equal(feedbackWriteAllowed("admin", undefined), false);
assert.equal(feedbackWriteAllowed("admin", true), true);
assert.equal(validFeedbackDraft({ kind: "action", reason: "  " }), false);
assert.equal(
  validFeedbackDraft({
    kind: "suspected-false-positive",
    reason: "판정 기준 확인",
  }),
  true,
);
const missing = parseAnomalyEvidence(
  {
    eventId: "id/1",
    rule: { execution: "broken" },
    ml: {
      execution: "partial",
      measurements: [{ name: "score", value: null, threshold: NaN }],
    },
    workflowStatus: "normal",
    capabilities: { saveFeedback: "true" },
  },
  "id/1",
);
assert.equal(missing.configurationVersion, undefined);
assert.equal(missing.rule, undefined);
assert.equal(missing.workflowStatus, undefined);
assert.equal(missing.canSaveFeedback, false);
assert.equal(missing.ml.measurements[0].value, undefined);
const malformed = parseAnomalyEvidence(
  {
    eventId: "id/1",
    decisionState: ["complete"],
    rule: { execution: ["completed"] },
    ml: { execution: {} },
    hypotheses: {},
  },
  "id/1",
);
assert.equal(malformed.decisionState, undefined);
assert.equal(malformed.rule, undefined);
assert.equal(malformed.ml, undefined);
assert.deepEqual(malformed.hypotheses, []);
assert.throws(() => parseAnomalyEvidence({ eventId: "other" }, "id/1"));
let token = "local-only-test-a";
globalThis.window = {
  location: { origin: "http://127.0.0.1:5173" },
  localStorage: { getItem: () => token },
};
globalThis.fetch = async (url) => {
  assert.equal(url, "/api/anomaly/events/id%2F1/detail");
  return new Response(JSON.stringify({ eventId: "id/1" }));
};
assert.equal((await readAnomalyEvidence("id/1")).canSaveFeedback, false);
globalThis.fetch = async () => {
  token = "local-only-test-b";
  return new Response(JSON.stringify({ eventId: "id/1" }));
};
await assert.rejects(
  readAnomalyEvidence("id/1"),
  (e) => e.name === "AbortError",
);
globalThis.fetch = async () => new Response(JSON.stringify({ success: true }));
await assert.rejects(
  saveAnomalyFeedback("id/1", { kind: "action", reason: "검토" }),
  /저장 결과/,
);
globalThis.fetch = async (_url, options) => {
  assert.deepEqual(JSON.parse(options.body), {
    kind: "action",
    reason: "검토",
  });
  return new Response(
    JSON.stringify({
      eventId: "id/1",
      feedbackId: "f1",
      savedAt: "2026-10-09T10:00:00Z",
    }),
  );
};
assert.equal(
  (await saveAnomalyFeedback("id/1", { kind: "action", reason: " 검토 " }))
    .feedbackId,
  "f1",
);
console.log(
  "anomaly evidence QA passed: safe read model, absent/partial evidence, hypotheses, capabilities, feedback receipt, session isolation",
);
