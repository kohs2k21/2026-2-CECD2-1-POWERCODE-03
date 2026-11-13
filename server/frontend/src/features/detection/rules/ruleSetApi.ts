import { httpClient } from "@/services/api/client";

/**
 * 룰 세트 API(rule-set.md, v3 절 포함)·수식 추천(llm-recommendation-job.md)·프로토콜 초안(protocol-settings-ui.md) 요청·응답.
 * 오류는 httpClient가 HttpError로 던진다. 성공·빈 룰로 바꾸지 않는다.
 */
export type Severity = "info" | "warning" | "critical";
export type SoftRuleOperator = "gte" | "gt" | "lte" | "lt";
/** 코드별 위험도. `default`가 있으면 등록되지 않은 코드의 단계(`hard:transaction-failed`, rule-set.md v3.5). */
export type SeverityByCode = { byCode: Record<string, Severity>; default?: Severity };
export type HardRuleSeverity = Severity | SeverityByCode;
export type Actor = { actorId: string; displayName: string };
export type HardRule = {
  id: string;
  methodVersion: string;
  displayName: string;
  conditionText: string;
  severity: HardRuleSeverity;
  enabled: boolean;
  /** severity가 들어 있는 규칙만 위험도를 바꿀 수 있다(모양은 내장 기본값과 같음). */
  editable: string[];
};
/** 소프트룰 적용 범위(relation-rules.md 4절). interfaceId는 표시용. 생성 후 바꿀 수 없다. */
export type SoftRuleScope = { processId: string; adapterType?: string; processHubId?: string; interfaceId?: string } | null;
export type RelationKind = "precedes" | "message_handoff" | "required_step" | "allowed_branch";
export type RelationRule = {
  id: string;
  kind: RelationKind;
  scope: Record<string, string> | null;
  from: { processId: string };
  to: { processId: string } | null;
  match: { from: string; to: string; key: string } | null;
  severity: Severity;
  enabled: boolean;
  source: unknown;
  createdAtRevision: number;
  updatedAtRevision: number;
};
export type ProtocolPath = { channels: string[]; source?: unknown };
export type ProtocolRule = {
  id: string;
  interfaceId: string;
  /** 서버가 pathKey 코드 포인트 순으로 저장한다. */
  paths: ProtocolPath[];
  severity: Severity;
  enabled: boolean;
  source: unknown;
  createdAtRevision: number;
  updatedAtRevision: number;
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
  /** v3 응답은 항상 키가 있다(전역 = null). */
  scope?: SoftRuleScope;
};
export type RuleSetState = {
  revision: number;
  contentHash: string;
  contentHashVersion: number;
  savedAt: string;
  savedBy: Actor;
  restoredFromRevision: number | null;
  counts: {
    hardEnabled: number; hardTotal: number; softEnabled: number; softTotal: number;
    relationEnabled?: number; relationTotal?: number; protocolEnabled?: number; protocolTotal?: number;
  };
  hardRules: HardRule[];
  softRules: SoftRule[];
  /** v1·v2 행은 서버가 빈 배열로 읽는다. */
  relationRules: RelationRule[];
  protocolRules: ProtocolRule[];
};
export type RuleSetChangeType =
  | "hard_enabled" | "hard_disabled" | "hard_updated" | "soft_added" | "soft_updated" | "soft_removed" | "soft_enabled" | "soft_disabled"
  | "relation_added" | "relation_removed" | "relation_updated" | "relation_enabled" | "relation_disabled"
  | "protocol_added" | "protocol_removed" | "protocol_updated" | "protocol_enabled" | "protocol_disabled";
export type RuleSetChange = {
  type: RuleSetChangeType;
  id: string | null;
  name?: string;
  displayName?: string;
  clientRef?: string;
  restored?: boolean;
  /** 관계 규칙 변경 표시 값. */
  kind?: RelationKind;
  from?: string;
  to?: string | null;
  /** 관계(표시용)·프로토콜 항목의 인터페이스. */
  interfaceId?: string | null;
  /** protocol_added: 경로 수. */
  paths?: number;
  /** soft_updated·hard_updated·relation_updated: [이전, 이후]. protocol_updated: {pathsAdded, pathsRemoved, severity?}. */
  diff?: Record<string, unknown>;
};
/** 저장은 허용하는 경고(rule-set.md v3.6). 새 규칙은 미리보기에서 ruleId null + clientRef. */
export type RuleSetWarning = { code: string; ruleId: string | null; clientRef?: string; message: string };
export type SoftRuleRequest = {
  id: string | null;
  clientRef?: string;
  name: string;
  field: string;
  operator: string;
  threshold: number;
  severity: string;
  enabled: boolean;
  scope?: SoftRuleScope;
};
export type RelationRuleRequest = {
  id: string | null;
  clientRef?: string;
  kind: RelationKind;
  scope: Record<string, string> | null;
  from: { processId: string };
  to: { processId: string } | null;
  match: RelationRule["match"];
  severity: Severity;
  enabled: boolean;
  source?: unknown;
};
export type ProtocolRuleRequest = {
  id: string | null;
  clientRef?: string;
  interfaceId: string;
  paths: ProtocolPath[];
  severity: Severity;
  enabled: boolean;
  source?: unknown;
};
export type RuleSetRequest = {
  expectedRevision: number;
  confirmHardRuleDisable: boolean;
  restoreFromRevision: number | null;
  note: null;
  hardRules: { id: string; enabled: boolean; severity?: HardRuleSeverity }[];
  softRules: SoftRuleRequest[];
  relationRules: RelationRuleRequest[];
  protocolRules: ProtocolRuleRequest[];
};
export type RuleSetPreview =
  | { valid: true; changes: RuleSetChange[]; hardDisabledAfterSave: string[]; warnings?: RuleSetWarning[] }
  | { valid: false; error: { code: string } };
export type RuleSetSaved = { revision: number; contentHash: string; appliedAt: string; changes: RuleSetChange[]; warnings?: RuleSetWarning[] };
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
/** 이력 스냅샷은 저장 당시 그대로(v1·v2 행은 관계·프로토콜 배열·scope가 없다). */
export type HistoryDetail = HistoryItem & {
  snapshot: { hardRules: { id: string; enabled: boolean; severity?: unknown }[]; softRules: SoftRule[]; relationRules?: RelationRule[]; protocolRules?: ProtocolRule[] };
};
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

/** 프로토콜 초안(protocol-settings-ui.md 2절, Trainer `recommend_relations` 결과 `protocolPathDrafts`). */
export type DraftJob = { id: string; status: string; errorCode: string | null; createdAt: string; finishedAt: string | null; snapshotId: string | null };
export type DraftPath = {
  channels: string[];
  pathKey: string;
  transactions: number;
  /** 경로 거래 / 경로를 만든 거래(갈라진 거래는 경로마다 세어 합이 1을 넘을 수 있다). */
  ratio: number;
  rare: boolean;
  fromBranching: number;
  exampleTransactionIds: (string | null)[];
};
export type DraftInterfaceSummary = {
  interfaceId: string;
  status: "draft" | "insufficient_evidence";
  transactions: number;
  pathTransactions: number;
  branchingTransactions: number;
  distinctPaths: number;
  excludedReasons: Record<string, number>;
  endpoints: { compared: number; mismatch: number; notCompared: number };
  omittedPaths: { paths: number; transactions: number };
  listedPaths: number;
};
export type DraftInterface = Omit<DraftInterfaceSummary, "listedPaths"> & { paths: DraftPath[] };
export type ProtocolDrafts =
  | { state: "protocol_drafts_unavailable"; reason: "no_result" | "not_in_result"; latestJob: DraftJob | null; job?: DraftJob }
  | {
    state: "available";
    latestJob: DraftJob | null;
    job: DraftJob;
    snapshotId: string | null;
    sourceId: string | null;
    period: { from: string | null; until: string | null };
    split: string | null;
    parameters: { minTransactions: number; rarePercent: number; maxPathSteps: number; maxRoutes: number; maxProcessRows: number; maxOutputPaths: number; reasonOrder: string[] };
    excludedReasons: Record<string, number>;
    transactionIdMissingRows: number;
    endpointMismatch: number;
    interfaces: DraftInterfaceSummary[];
    /** interfaceId를 주면: 그 인터페이스(학습 기간에 없으면 null). */
    interface?: DraftInterface | null;
  };
export const readProtocolDrafts = (interfaceId: string | null, signal?: AbortSignal) =>
  httpClient.get<ProtocolDrafts>("/api/rule-set/protocol-drafts", { params: interfaceId === null ? {} : { interfaceId }, signal });
