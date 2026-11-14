import { httpClient } from "@/services/api/client";
import type { JobListItem } from "@/features/detection/jobs/jobsApi";

/**
 * 프로토콜 경로 설명 `explain_protocol`(contracts/protocol-explanation-job.md). 요청 대상·결과 읽기·화면 문구.
 * - 요청: 1절 두 대상(관측 경로 = 프로토콜 설정 상세, 알림 = 판정 상세). 본문 키는 계약에 적은 것만 보낸다
 * - 결과: 6절 `protocol-explanation-v1`. 저장값 그대로 읽고(문장·경로 다시 만들지 않음) 형식이 틀린 값은 버린다
 * - 문구: 11절(사용자 승인 11/14). 실패는 기존 `<항목> 실패 · <코드>` 형식(7절 코드 그대로)
 */
export const PROTOCOL_EXPLANATION_KIND = "explain_protocol";
export const EXPLAIN_PATHS_BUTTON = "경로 설명 보기";
export const EXPLAIN_ALERT_BUTTON = "설명 보기";
export const EXPLANATION_RUNNING = "설명을 만드는 중입니다…";
export const EXPLANATION_TITLE = "LLM 설명";
export const REVIEW_POINTS_TITLE = "확인할 점";
const EXPLANATION_NOTE = "코드가 집계한 사실만으로 쓴 설명입니다";
/** 결과 아래 작은 글씨. 버전은 결과의 `ruleSetRevision`(미등록 알림이면 null일 수 있음 — 그때는 버전 부분을 붙이지 않는다). */
export const explanationNote = (revision: number | null) => (revision === null ? EXPLANATION_NOTE : `${EXPLANATION_NOTE} · 버전 ${revision} 기준`);

export type ProtocolExplanationTarget =
  | { kind: "observed_paths"; jobId: string; interfaceId: string; ruleSetRevision: number }
  | { kind: "alert"; decisionId: string };
export type ProtocolExplanationRequest = { kind: typeof PROTOCOL_EXPLANATION_KIND; sourceId: string; target: ProtocolExplanationTarget };

export const observedPathsRequest = (sourceId: string, jobId: string, interfaceId: string, ruleSetRevision: number): ProtocolExplanationRequest => ({
  kind: PROTOCOL_EXPLANATION_KIND, sourceId, target: { kind: "observed_paths", jobId, interfaceId, ruleSetRevision },
});
export const alertRequest = (sourceId: string, decisionId: string): ProtocolExplanationRequest => ({
  kind: PROTOCOL_EXPLANATION_KIND, sourceId, target: { kind: "alert", decisionId },
});

const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const sameUuid = (left: unknown, right: string) => typeof left === "string" && left.toLowerCase() === right.toLowerCase();

/** 작업 입력(`input_spec` = 요청 본문 그대로, 1절)이 이 요청과 같은 대상인가. UUID는 대소문자 무시, 인터페이스 ID는 원래 문자열 그대로. */
export const sameRequest = (inputSpec: unknown, request: ProtocolExplanationRequest) => {
  if (!record(inputSpec) || inputSpec.kind !== PROTOCOL_EXPLANATION_KIND || !sameUuid(inputSpec.sourceId, request.sourceId) || !record(inputSpec.target)) return false;
  const target = inputSpec.target;
  if (request.target.kind === "alert") return target.kind === "alert" && sameUuid(target.decisionId, request.target.decisionId);
  return target.kind === "observed_paths" && sameUuid(target.jobId, request.target.jobId)
    && target.interfaceId === request.target.interfaceId && target.ruleSetRevision === request.target.ruleSetRevision;
};

/** 같은 대상의 기존 작업 찾기에 읽는 최근 작업 수(`GET /api/jobs` limit 상한). 그 밖의 옛 작업은 요청하면 서버 재사용(1절)이 돌려준다. */
export const EXISTING_JOB_LIMIT = 100;
export const readProtocolExplanationJobs = (signal?: AbortSignal) =>
  httpClient.get<{ items: JobListItem[] }>("/api/jobs", { params: { kind: PROTOCOL_EXPLANATION_KIND, limit: EXISTING_JOB_LIMIT }, signal });

/**
 * 같은 대상의 기존 작업: 서버 재사용(1절 — queued·running·succeeded만 재사용, 실패는 다시 요청 가능)과 같이 실패가 아닌 최근 작업을 먼저,
 * 없으면 가장 최근 실패 작업. 목록은 최근 순.
 */
export const existingJob = (items: readonly JobListItem[], request: ProtocolExplanationRequest) => {
  const matching = items.filter((item) => sameRequest(item.inputSpec, request));
  return matching.find((item) => item.status !== "failed") ?? matching[0] ?? null;
};

export type ProtocolExplanationResult = {
  explanation: string;
  reviewPoints: string[];
  paths: Record<string, string[]>;
  ruleSetRevision: number | null;
};
const strings = (value: unknown): string[] | null => (Array.isArray(value) && value.every((item) => typeof item === "string") ? value : null);

/** 성공 결과(6절). `explanation`이 없거나 형식이 다르면 null(화면은 결과 칸을 그리지 않는다). 형식이 틀린 경로·확인할 점 항목은 버린다. */
export const readExplanationResult = (summary: unknown): ProtocolExplanationResult | null => {
  if (!record(summary) || summary.format !== "protocol-explanation-v1" || typeof summary.explanation !== "string" || !summary.explanation) return null;
  const paths: Record<string, string[]> = {};
  if (record(summary.paths)) for (const [id, channels] of Object.entries(summary.paths)) {
    const list = strings(channels);
    if (list?.length) paths[id] = list;
  }
  const revision = summary.ruleSetRevision;
  return {
    explanation: summary.explanation,
    reviewPoints: (strings(summary.reviewPoints) ?? []).filter((point) => point !== ""),
    paths,
    ruleSetRevision: typeof revision === "number" && Number.isSafeInteger(revision) && revision >= 0 ? revision : null,
  };
};
/** 결과 요약을 읽지 못한 성공 작업(6절 형식이 아님, 화면 전용 코드). 성공으로 보이지 않고 실패 줄에 이 코드를 보인다. */
export const RESULT_UNREADABLE = "protocol_explanation_result_unreadable";
/** 점검 실패 사유 코드(6절 `check.failures`, 실패 작업의 요약에만). 사유 이름은 아직 후보라 코드 그대로 보인다. */
export const checkFailures = (summary: unknown): string[] =>
  record(summary) && record(summary.check) ? (strings(summary.check.failures) ?? []).filter((code) => code !== "") : [];

/** 문장 속 경로 id(`P1`…)를 나눈다: `paths`에 있는 id만 경로로, 영문·숫자·밑줄에 붙은 일치는 글자 그대로. */
export const splitPathIds = (sentence: string, paths: Record<string, string[]>): ({ text: string } | { pathId: string; channels: string[] })[] => {
  const parts: ({ text: string } | { pathId: string; channels: string[] })[] = [];
  let last = 0;
  for (const match of sentence.matchAll(/(?<![A-Za-z0-9_])P\d+(?![A-Za-z0-9_])/g)) {
    const channels = Object.hasOwn(paths, match[0]) ? paths[match[0]] : undefined;
    if (!channels) continue;
    if (match.index > last) parts.push({ text: sentence.slice(last, match.index) });
    parts.push({ pathId: match[0], channels });
    last = match.index + match[0].length;
  }
  if (last < sentence.length) parts.push({ text: sentence.slice(last) });
  return parts;
};

/**
 * 판정 근거 `evidence.protocolReview`(protocol-rules.md 5.3)가 알림 목록과 같은 판별(protocol-settings-ui.md 5절 — `version` 1,
 * `kind` ∈ {path_unmatched, interface_unregistered}, `interfaceId` 비지 않은 문자열)을 통과하는가. 통과해야 알림 대상 설명을 요청한다(10절 접수 검증과 같음).
 */
export const hasProtocolReview = (evidence: Record<string, unknown> | null | undefined) => {
  const review = evidence?.protocolReview;
  return record(review) && review.version === 1 && (review.kind === "path_unmatched" || review.kind === "interface_unregistered")
    && typeof review.interfaceId === "string" && review.interfaceId !== "";
};
