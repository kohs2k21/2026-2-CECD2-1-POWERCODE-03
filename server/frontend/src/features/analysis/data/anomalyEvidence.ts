import { httpClient } from "../../../services/api/client";
import { getStoredToken } from "../../../services/auth/session";
export type EvidenceExecution =
  "completed" | "partial" | "unavailable" | "not-run";
export type EvidenceMeasurement = {
  name: string;
  value?: number | string;
  threshold?: number | string;
  unit?: string;
};
export type DetectionEvidence = {
  execution: EvidenceExecution;
  reason?: string;
  version?: string;
  measurements: EvidenceMeasurement[];
};
export type FeedbackKind =
  "action" | "suspected-false-positive" | "insufficient-evidence";
export type FeedbackDraft = { kind: FeedbackKind; reason: string };
export type AnomalyEvidenceDetail = {
  eventId: string;
  summary?: string;
  configurationVersion?: string;
  decisionState?: "complete" | "partial" | "unavailable";
  workflowStatus?: "Open" | "Resolved";
  rule?: DetectionEvidence;
  ml?: DetectionEvidence;
  hypotheses: string[];
  configurationId?: string;
  evaluationId?: string;
  canSaveFeedback: boolean;
};
const record = (v: unknown): v is Record<string, unknown> =>
  Boolean(v) && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): string | undefined =>
  typeof v === "string" && v.trim() ? v : undefined;
const scalar = (v: unknown): number | string | undefined =>
  typeof v === "number" && Number.isFinite(v) ? v : text(v);
const parseEvidence = (value: unknown): DetectionEvidence | undefined => {
  if (
    !record(value) ||
    typeof value.execution !== "string" ||
    !["completed", "partial", "unavailable", "not-run"].includes(
      value.execution,
    )
  )
    return undefined;
  const measurements = Array.isArray(value.measurements)
    ? value.measurements.flatMap((item) => {
        if (!record(item) || !text(item.name)) return [];
        return [
          {
            name: text(item.name)!,
            value: scalar(item.value),
            threshold: scalar(item.threshold),
            unit: text(item.unit),
          },
        ];
      })
    : [];
  return {
    execution: value.execution as EvidenceExecution,
    reason: text(value.reason),
    version: text(value.version),
    measurements,
  };
};
export const parseAnomalyEvidence = (
  value: unknown,
  eventId: string,
): AnomalyEvidenceDetail => {
  if (!record(value) || value.eventId !== eventId)
    throw new Error("이상 이벤트 근거 응답을 확인할 수 없습니다.");
  return {
    eventId,
    summary: text(value.summary),
    configurationVersion: text(value.configurationVersion),
    decisionState:
      typeof value.decisionState === "string" &&
      ["complete", "partial", "unavailable"].includes(value.decisionState)
        ? (value.decisionState as AnomalyEvidenceDetail["decisionState"])
        : undefined,
    workflowStatus:
      value.workflowStatus === "Open" || value.workflowStatus === "Resolved"
        ? value.workflowStatus
        : undefined,
    rule: parseEvidence(value.rule),
    ml: parseEvidence(value.ml),
    hypotheses: Array.isArray(value.hypotheses)
      ? value.hypotheses.filter(
          (item): item is string =>
            typeof item === "string" && Boolean(item.trim()),
        )
      : [],
    configurationId: text(value.configurationId),
    evaluationId: text(value.evaluationId),
    canSaveFeedback:
      record(value.capabilities) && value.capabilities.saveFeedback === true,
  };
};
const ensureCurrentSession = (token: string | null, signal?: AbortSignal) => {
  if (signal?.aborted || token !== getStoredToken())
    throw new DOMException("Session changed", "AbortError");
};
export const readAnomalyEvidence = async (
  eventId: string,
  signal?: AbortSignal,
) => {
  const token = getStoredToken();
  const value = await httpClient.get<unknown>(
    `/api/anomaly/events/${encodeURIComponent(eventId)}/detail`,
    { signal },
  );
  ensureCurrentSession(token, signal);
  return parseAnomalyEvidence(value, eventId);
};
export const validFeedbackDraft = (draft: FeedbackDraft) =>
  ["action", "suspected-false-positive", "insufficient-evidence"].includes(
    draft.kind,
  ) &&
  Boolean(draft.reason.trim()) &&
  draft.reason.length <= 2000;
export const feedbackWriteAllowed = (
  role: string | undefined,
  capability: boolean | undefined,
) => role === "admin" && capability === true;
export const saveAnomalyFeedback = async (
  eventId: string,
  draft: FeedbackDraft,
  signal?: AbortSignal,
) => {
  if (!validFeedbackDraft(draft))
    throw new Error("조사·조치 사유를 1~2,000자로 입력해 주세요.");
  const token = getStoredToken();
  const value = await httpClient.post<unknown>(
    `/api/anomaly/events/${encodeURIComponent(eventId)}/feedback`,
    { kind: draft.kind, reason: draft.reason.trim() },
    { signal },
  );
  ensureCurrentSession(token, signal);
  if (
    !record(value) ||
    value.eventId !== eventId ||
    !text(value.feedbackId) ||
    typeof value.savedAt !== "string" ||
    !Number.isFinite(Date.parse(value.savedAt))
  )
    throw new Error("피드백 저장 결과를 확인할 수 없습니다.");
  return {
    eventId,
    feedbackId: value.feedbackId as string,
    savedAt: value.savedAt,
  };
};
