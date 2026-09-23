import type { Severity } from "./domain";

export type RealtimeAnomalyEvent = {
  schemaVersion: 1;
  eventId: string;
  provenance: "backend-ingest";
  detectedAt?: string;
  processName?: string;
  channelName?: string;
  transactionId?: string;
  status?: string;
  responseCode: string;
  anomalyScore: number;
  processTimeMs: number;
  riskScore: number;
  riskLevel: 1 | 2 | 3;
  severity: Severity;
};

const severities: ReadonlySet<Severity> = new Set([
  "Info",
  "Warning",
  "Critical",
]);

const optionalStringFields = [
  "detectedAt",
  "processName",
  "channelName",
  "transactionId",
  "status",
] as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const isOptionalStringFieldsValid = (
  value: Record<string, unknown>,
): boolean =>
  optionalStringFields.every(
    (field) => value[field] === undefined || typeof value[field] === "string",
  );

export const isRealtimeAnomalyEvent = (
  value: unknown,
): value is RealtimeAnomalyEvent => {
  if (!isRecord(value)) {
    return false;
  }

  const riskLevel = value.riskLevel;

  return (
    value.schemaVersion === 1 &&
    typeof value.eventId === "string" &&
    value.eventId.trim() !== "" &&
    value.provenance === "backend-ingest" &&
    typeof value.responseCode === "string" &&
    value.responseCode.trim() !== "" &&
    typeof value.anomalyScore === "number" &&
    Number.isFinite(value.anomalyScore) &&
    value.anomalyScore >= 0 &&
    value.anomalyScore <= 1 &&
    typeof value.processTimeMs === "number" &&
    Number.isFinite(value.processTimeMs) &&
    value.processTimeMs >= 0 &&
    typeof value.riskScore === "number" &&
    Number.isFinite(value.riskScore) &&
    typeof riskLevel === "number" &&
    Number.isInteger(riskLevel) &&
    riskLevel >= 1 &&
    riskLevel <= 3 &&
    typeof value.severity === "string" &&
    severities.has(value.severity as Severity) &&
    isOptionalStringFieldsValid(value)
  );
};
