import assert from "node:assert/strict";
import {
  adaptRealtimeAnomalyEvent,
  adaptRealtimeAnomalyEvents,
  filterRealtimeAnalysisItems,
  getLatestRealtimeDetectionAt,
  getRealtimeCategoryCounts,
  getRealtimeDetailByEventId,
  matchesRealtimeCategory,
} from "../src/features/analysis/utils/realtimeAdapter.ts";

const firstEvent = {
  schemaVersion: 1,
  eventId: "backend-event-1",
  provenance: "backend-ingest",
  detectedAt: "2026-09-23T09:12:34+09:00",
  processName: "process-a",
  channelName: "channel-a",
  transactionId: "transaction-a",
  status: "Open",
  responseCode: "R-101",
  anomalyScore: 0.42,
  processTimeMs: 125,
  riskScore: 12,
  riskLevel: 1,
  severity: "Warning",
};

const secondEvent = {
  schemaVersion: 1,
  eventId: "backend-event-2",
  provenance: "backend-ingest",
  responseCode: "R-202",
  anomalyScore: 0.9,
  processTimeMs: 800,
  riskScore: 88,
  riskLevel: 3,
  severity: "Critical",
};

const item = adaptRealtimeAnomalyEvent(firstEvent);
assert.deepEqual(item, {
  log: {
    logId: "backend-event-1",
    severity: "Warning",
    status: "Open",
    detectedAt: "2026-09-23T09:12:34+09:00",
    processName: "process-a",
    channelName: "channel-a",
    transactionId: "transaction-a",
    responseCode: "R-101",
    anomalyScore: 0.42,
    processTimeMs: 125,
    riskScore: 12,
    riskLevel: 1,
  },
},
  "inbox adapter should copy only fields in the backend event contract",
);
assert.equal("transaction" in item, false, "adapter must not invent transaction detail");
assert.equal("messages" in item, false, "adapter must not invent message detail");
assert.equal("llmReport" in item, false, "adapter must not invent an LLM report");

const missingFieldsItem = adaptRealtimeAnomalyEvent(secondEvent);
assert.equal(missingFieldsItem.log.detectedAt, undefined);
assert.equal(missingFieldsItem.log.processName, undefined);
assert.equal(missingFieldsItem.log.transactionId, undefined);
assert.equal(getLatestRealtimeDetectionAt([missingFieldsItem]), null);

const events = [firstEvent, secondEvent];
const items = adaptRealtimeAnomalyEvents(events);
assert.deepEqual(
  filterRealtimeAnalysisItems(items, "Critical", "backend-event-2").map(
    ({ log }) => log.logId,
  ),
  ["backend-event-2"],
  "event IDs and severity should remain searchable and filterable in the list",
);
assert.deepEqual(
  getRealtimeCategoryCounts(items),
  { All: 2, Critical: 1, Warning: 1, Info: 0, Open: 0, Resolved: 0 },
  "source status must not be treated as a workflow status",
);
assert.equal(
  matchesRealtimeCategory(items[0], "All"),
  true,
  "All should include the received event regardless of source status",
);
assert.equal(
  matchesRealtimeCategory(items[0], "Open"),
  false,
  "source status Open must not enter the workflow Open category",
);
assert.equal(
  getRealtimeDetailByEventId(events, "backend-event-1"),
  firstEvent,
  "opening an inbox item should resolve to the original backend event for detail",
);
assert.equal(getRealtimeDetailByEventId(events, "missing"), null);
assert.equal(
  getLatestRealtimeDetectionAt(items),
  firstEvent.detectedAt,
  "latest detection should retain the source timestamp text",
);

console.log("frontend realtime adapter QA passed: contract-only inbox fields, raw-event detail lookup, source-status separation, filtering, and missing-value handling");
