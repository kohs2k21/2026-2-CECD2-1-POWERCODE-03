import { httpClient } from "@/services/api/client";

/**
 * 룰 세트 API(rule-set.md)·수식 추천(llm-recommendation-job.md) 요청·응답.
 * 오류는 httpClient가 HttpError로 던진다. 성공·빈 룰로 바꾸지 않는다.
 */
export type Severity = "info" | "warning" | "critical";
export type SoftRuleOperator = "gte" | "gt" | "lte" | "lt";
export type SeverityByCode = { byCode: Record<string, Severity> };
export type Actor = { actorId: string; displayName: string };
export type HardRule = {
  id: string;
  methodVersion: string;
  displayName: string;
  conditionText: string;
  severity: Severity | SeverityByCode;
  enabled: boolean;
  /** severity가 들어 있는 규칙만 코드별 위험도를 바꿀 수 있다. */
  editable: string[];
};
export type SoftRule = {
  id: string;
  name: string;
  field: string;
  operator: SoftRuleOperator;
  threshold: number;
  unit: string;
  severity: Severity;
  enabled: boolean;
};
export type RuleSetState = {
  revision: number;
  contentHash: string;
  contentHashVersion: number;
  savedAt: string;
  savedBy: Actor;
  restoredFromRevision: number | null;
  counts: { hardEnabled: number; hardTotal: number; softEnabled: number; softTotal: number };
  hardRules: HardRule[];
  softRules: SoftRule[];
};
export type RuleSetChange = {
  type: "hard_enabled" | "hard_disabled" | "hard_updated" | "soft_added" | "soft_updated" | "soft_removed" | "soft_enabled" | "soft_disabled";
  id: string | null;
  name?: string;
  displayName?: string;
  diff?: Record<string, [unknown, unknown]>;
};
export type SoftRuleRequest = {
  id: string | null;
  clientRef?: string;
  name: string;
  field: string;
  operator: string;
  threshold: number;
  severity: string;
  enabled: boolean;
};
export type RuleSetRequest = {
  expectedRevision: number;
  confirmHardRuleDisable: boolean;
  restoreFromRevision: number | null;
  note: null;
  hardRules: { id: string; enabled: boolean; severity?: SeverityByCode }[];
  softRules: SoftRuleRequest[];
};
export type RuleSetPreview =
  | { valid: true; changes: RuleSetChange[]; hardDisabledAfterSave: string[] }
  | { valid: false; error: { code: string } };
export type RuleSetSaved = { revision: number; contentHash: string; appliedAt: string; changes: RuleSetChange[] };
export type HistoryItem = {
  revision: number;
  savedAt: string;
  savedBy: Actor;
  note: string | null;
  restoredFromRevision: number | null;
  contentHash: string;
  contentHashVersion: number;
  changes: RuleSetChange[];
};
export type HistoryPage = { items: HistoryItem[]; pagination: { limit: number; hasMore: boolean; nextCursor: string | null } };
export type HistoryDetail = HistoryItem & { snapshot: { hardRules: { id: string; enabled: boolean; severity?: unknown }[]; softRules: SoftRule[] } };
export type SoftRuleField = {
  fieldId: string;
  displayName: string;
  unit: string;
  valueType: "integer" | "number";
  operators: SoftRuleOperator[];
  status: string;
};
export type PeriodMode = "active_model_training_start" | "recent_1m" | "recent_3m" | "custom";
export type ThresholdStatsRequest = {
  field: string;
  statistic: "p95";
  sourceId: string;
  period: { mode: PeriodMode; from?: string; until?: string; timeBasis?: "persisted_at" };
};
export type ThresholdStats = {
  field: string;
  operator: SoftRuleOperator | null;
  threshold: string | null;
  displayThreshold: string | null;
  unit: string;
  status: "proposed" | "no_applicable" | "rejected";
  rejectedReason?: string;
  evidence: {
    n: number;
    min: number | null;
    max: number | null;
    p95: number | null;
    period: { mode: PeriodMode; from: string; until: string };
  };
};

export const readRuleSet = (signal?: AbortSignal) => httpClient.get<RuleSetState>("/api/rule-set", { signal });
export const previewRuleSet = (body: RuleSetRequest) => httpClient.post<RuleSetPreview>("/api/rule-set/preview", body);
export const saveRuleSet = (body: RuleSetRequest) => httpClient.put<RuleSetSaved>("/api/rule-set", body);
export const readRuleSetHistory = (cursor?: string, signal?: AbortSignal) =>
  httpClient.get<HistoryPage>("/api/rule-set/history", { params: { limit: 50, cursor }, signal });
export const readRuleSetRevision = (revision: number) => httpClient.get<HistoryDetail>(`/api/rule-set/history/${revision}`);
export const readThresholdStats = (body: ThresholdStatsRequest) => httpClient.post<ThresholdStats>("/api/rule-set/threshold-stats", body);

/** 등록 필드는 계약 경로 `GET /api/jobs/capabilities`의 `methodCatalog`로 받는다. 없으면 오류(빈 목록으로 두지 않음). */
export const readSoftRuleFields = async (signal?: AbortSignal) => {
  const capabilities = await httpClient.get<{ methodCatalog?: { softRuleFields?: SoftRuleField[] } }>("/api/jobs/capabilities", { signal });
  const fields = capabilities.methodCatalog?.softRuleFields;
  if (!Array.isArray(fields)) throw new Error("method_catalog_missing");
  return fields.filter((field) => field.status === "supported");
};
