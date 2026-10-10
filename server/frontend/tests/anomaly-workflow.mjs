import assert from "node:assert/strict";
import { isRealtimeAnomalyEvent } from "../src/types/realtime.ts";
import {
  adaptRealtimeAnomalyEvents,
  getRealtimeCategoryCounts,
  matchesRealtimeCategory,
} from "../src/features/analysis/utils/realtimeAdapter.ts";
const event = {
  schemaVersion: 1,
  eventId: "event-1",
  provenance: "backend-ingest",
  responseCode: "R1",
  anomalyScore: 0.5,
  processTimeMs: 200,
  riskScore: 40,
  riskLevel: 2,
  severity: "Warning",
  status: "Resolved",
};
assert.equal(
  isRealtimeAnomalyEvent(event),
  true,
  "old v1 without optional evidence remains valid",
);
const legacy = adaptRealtimeAnomalyEvents([event]);
assert.equal(legacy[0].log.workflowStatus, undefined);
assert.equal(
  matchesRealtimeCategory(legacy[0], "Resolved"),
  false,
  "source status is not workflow state",
);
for (const invalid of [["Open"], {}, "normal", null]) {
  const malformed = { ...event, workflowStatus: invalid };
  assert.equal(
    isRealtimeAnomalyEvent(malformed),
    true,
    "bad optional metadata must not reject valid basic event",
  );
  assert.equal(
    adaptRealtimeAnomalyEvents([malformed])[0].log.workflowStatus,
    undefined,
  );
}
const items = adaptRealtimeAnomalyEvents([
  event,
  { ...event, eventId: "event-2", workflowStatus: "Open" },
  { ...event, eventId: "event-3", workflowStatus: "Resolved" },
]);
assert.equal(matchesRealtimeCategory(items[1], "Open"), true);
assert.equal(matchesRealtimeCategory(items[2], "Resolved"), true);
assert.deepEqual(getRealtimeCategoryCounts(items), {
  All: 3,
  Critical: 0,
  Warning: 3,
  Info: 0,
  Open: 1,
  Resolved: 1,
});
assert.equal(
  isRealtimeAnomalyEvent({ ...event, anomalyScore: 2, workflowStatus: "Open" }),
  false,
  "optional additions must not weaken mandatory validation",
);
console.log(
  "anomaly workflow QA passed: legacy v1, invalid optional metadata, source/workflow separation, classification and mandatory validation",
);
