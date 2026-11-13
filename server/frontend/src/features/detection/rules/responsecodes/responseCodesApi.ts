import { httpClient } from "@/services/api/client";
import type { Severity } from "../ruleSetApi";

/**
 * 응답 코드 사전 API(contracts/response-codes-ui.md 3·4절) 요청·응답. 코드별 위험도는 현재 룰 세트 `hard:transaction-failed`의 값이고,
 * 바꿀 때는 룰 세트 저장(PUT /api/rule-set)을 쓴다. 형식이 틀리면 빈 목록으로 바꾸지 않고 오류로 던진다.
 */
export type CodeSeverity = { level: Severity; source: "code" | "default" };
export type ActorRef = { actorId: string; displayName: string };
export type ResponseCodeRuleSet = { revision: number; ruleId: string; enabled: boolean; defaultSeverity: Severity; registeredCodes: number };
export type ResponseCodeItem = {
  code: string;
  /** false = 룰 세트 byCode에만 있고 사전에 없는 코드(메시지 등 null). */
  inDictionary: boolean;
  enumName: string | null;
  codeType: string | null;
  httpStatus: string | null;
  messageKey: string | null;
  message: string | null;
  origin: "import" | "edited" | null;
  severity: CodeSeverity;
};
export type ResponseCodeList = {
  ruleSet: ResponseCodeRuleSet;
  counts: { dictionary: number; edited: number; missingFromDictionary: number };
  types: { codeType: string; count: number }[];
  items: ResponseCodeItem[];
  pagination: { limit: number; hasMore: boolean; nextCursor: string | null };
};
export type SeverityChange = {
  revision: number; savedAt: string; savedBy: ActorRef; restoredFromRevision: number | null; before: CodeSeverity; after: CodeSeverity;
};
export type MessageChange = { kind: "import" | "edit"; messageBefore: string | null; messageAfter: string; changedAt: string; changedBy: ActorRef };
export type ResponseCodeDetail = ResponseCodeItem & {
  /** 사전 행 수정 번호(메시지 편집 expectedRevision). 사전에 없으면 null. */
  revision: number | null;
  updatedAt: string | null;
  updatedBy: ActorRef | null;
  ruleSet: ResponseCodeRuleSet;
  messageHistory: MessageChange[];
  severityHistory: SeverityChange[];
};

const fail = (): never => { throw new Error("response_code_response_invalid"); };
const listShape = (value: ResponseCodeList) =>
  (Array.isArray(value?.items) && Array.isArray(value.types) && value.ruleSet && value.counts && value.pagination ? value : fail());
const detailShape = (value: ResponseCodeDetail) =>
  (typeof value?.code === "string" && Array.isArray(value.messageHistory) && Array.isArray(value.severityHistory) && value.ruleSet && value.severity ? value : fail());

export const RESPONSE_CODE_PAGE = 200;
export const readResponseCodes = async (filter: { q: string; type: string; cursor?: string | null }, signal?: AbortSignal) =>
  listShape(await httpClient.get<ResponseCodeList>("/api/response-codes", {
    params: { limit: RESPONSE_CODE_PAGE, ...(filter.q.trim() ? { q: filter.q.trim() } : {}), ...(filter.type ? { type: filter.type } : {}), ...(filter.cursor ? { cursor: filter.cursor } : {}) },
    signal,
  }));
export const readResponseCode = async (code: string, signal?: AbortSignal) =>
  detailShape(await httpClient.get<ResponseCodeDetail>(`/api/response-codes/${encodeURIComponent(code)}`, { signal }));
export const saveResponseCodeMessage = async (code: string, body: { message: string; expectedRevision: number | null }) =>
  detailShape(await httpClient.put<ResponseCodeDetail>(`/api/response-codes/${encodeURIComponent(code)}`, body));
