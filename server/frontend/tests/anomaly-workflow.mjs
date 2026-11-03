import assert from "node:assert/strict";
import { isRealtimeAnomalyEvent } from "../src/types/realtime.ts";
// 실시간 이상 이벤트(데모 SSE v1) 계약 검증: 알림 패널이 계속 사용한다. 이상 분석 화면의 옛 이벤트 분류·상세는 #105에서 폐지.
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
assert.equal(isRealtimeAnomalyEvent(event), true, "old v1 without optional evidence remains valid");
for (const invalid of [["Open"], {}, "normal", null]) {
  assert.equal(
    isRealtimeAnomalyEvent({ ...event, workflowStatus: invalid }),
    true,
    "bad optional metadata must not reject valid basic event",
  );
}
assert.equal(
  isRealtimeAnomalyEvent({ ...event, anomalyScore: 2, workflowStatus: "Open" }),
  false,
  "optional additions must not weaken mandatory validation",
);
console.log("anomaly event contract QA passed: legacy v1, invalid optional metadata tolerated, mandatory validation kept");
