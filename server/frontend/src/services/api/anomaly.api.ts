import { httpClient } from "./client";

export type RawAnomalyInput = {
  logId?: string;
  detectedAt?: string;
  processName?: string;
  channelName?: string;
  transactionId?: string;
  status?: string;
  anomalyScore: number;
  processTimeMs: number;
  responseCode: string;
};

export type RiskEvaluationResult = {
  logId?: string;
  riskScore: number;
  riskLevel: 1 | 2 | 3;
  severity: "Info" | "Warning" | "Critical";
};

export type RiskEvaluationResponse = {
  schemaVersion: 1;
  message: string;
  results: RiskEvaluationResult[];
};

const optionalStringFields = [
  "logId",
  "detectedAt",
  "processName",
  "channelName",
  "transactionId",
  "status",
] as const;

const validateRawLog = (log: RawAnomalyInput): void => {
  if (
    !Number.isFinite(log.anomalyScore) ||
    log.anomalyScore < 0 ||
    log.anomalyScore > 1
  ) {
    throw new Error("anomalyScore must be a finite number between 0 and 1.");
  }

  if (!Number.isFinite(log.processTimeMs) || log.processTimeMs < 0) {
    throw new Error("processTimeMs must be a non-negative finite number.");
  }

  if (typeof log.responseCode !== "string" || log.responseCode.trim() === "") {
    throw new Error("responseCode must be a non-empty string.");
  }

  optionalStringFields.forEach((field) => {
    const value = log[field];
    if (value !== undefined && typeof value !== "string") {
      throw new Error(`${field} must be a string when provided.`);
    }
  });
};

export async function evaluateAnomalyRisk(
  logs: RawAnomalyInput[],
): Promise<RiskEvaluationResponse> {
  if (!Array.isArray(logs) || logs.length === 0) {
    throw new Error("At least one raw anomaly log is required.");
  }

  logs.forEach(validateRawLog);
  return httpClient.post("/api/anomaly/evaluate-risk", { logs });
}
