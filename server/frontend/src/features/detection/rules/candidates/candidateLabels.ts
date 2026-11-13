import { HttpError } from "@/services/api/client";
import type { CandidateJob, Counts, RelationCandidate, RelationKind, RelationStatus, ThresholdCandidate, ThresholdStatus } from "./candidateApi";

/**
 * 규칙 추천 화면 문구(contracts/rule-candidates-ui.md 8절). 근거:
 * - 종류 이름: "필수 단계·선행 관계·허용 분기·메시지 인계"(relation-rules.md 2절 kind의 화면 이름)
 * - 상태 기준: Trainer relation_candidates.py candidate_status(적용 30건 미만 근거 부족, 충족률 99% 이상 후보, 95~99% 검토 필요)
 * - 건수 이름: "적용·충족·위반·보류"(Trainer applicable·satisfied·violated·undetermined의 화면 이름)
 * - 짝 경고 문장: Detector relation-rules.md 4절 "경고 문장(WebUI가 그대로 씀)"
 * - 승인 불가 사유: Detector relation-rules.md 8절 "Detector v3 지원이 API v3 저장보다 먼저 배포돼야 한다"
 */
export const CANDIDATES_TITLE = "규칙 추천";
export const INTERFACE_SEARCH = "인터페이스 ID 검색";
export const INTERFACE_SEARCH_EMPTY = "일치하는 인터페이스가 없습니다.";
export const RELATION_SECTION = "관계 규칙 후보";
export const THRESHOLD_SECTION = "처리 시간(이상 징후) 후보";
export const kindLabels: Record<RelationKind, string> = { required_step: "필수 단계", precedes: "선행 관계", message_handoff: "메시지 인계" };
export const relationStatusLabels: Record<RelationStatus, string> = {
  candidate: "후보", review_needed: "검토 필요", insufficient_evidence: "근거 부족", not_candidate: "기준 미달",
};
export const relationStatusBadge: Record<RelationStatus, "success" | "warning" | "outline" | "secondary"> = {
  candidate: "success", review_needed: "warning", insufficient_evidence: "outline", not_candidate: "secondary",
};
export const thresholdStatusLabels: Record<ThresholdStatus, string> = {
  candidate: "후보", insufficient_evidence: "근거 부족", no_variation: "값 변화 없음", rejected: "제외",
};
export const countLabels: Record<keyof Counts, string> = { applicable: "적용", satisfied: "충족", violated: "위반", undetermined: "보류" };
/** Trainer listedForPairing: 기준 미달 선행 관계를 메시지 인계의 짝으로 함께 보인 항목. */
export const PAIR_LISTED = "인계 짝";
export const PAIR_WARNING = "짝 선행 규칙이 없어 선행이 다르면 인계를 확인하지 않습니다.";
export const APPROVE = "승인";
export const approvalBlockedText = (reason: string | null) =>
  reason === "rule_set_hash_v3_pending"
    ? "아직 저장 불가 · 관계 규칙·단계별 범위 저장(룰 세트 형식 3)은 Detector 지원 배포 뒤 열립니다"
    : `아직 저장 불가 · ${reason ?? "사유 없음"}`;
/** 위반·보류 사유(Trainer REASON_VOCABULARY 키). 모르는 값은 코드 그대로. */
export const reasonLabels: Record<string, string> = {
  step_absent: "필수 단계 없음",
  successor_is_start_step: "후행이 시작 단계(DEPEND NONE)",
  other_predecessor: "다른 선행 단계",
  self_dependency: "자기 자신을 선행으로 지정",
  message_id_mismatch: "메시지 ID 불일치",
  predecessor_missing: "선행 단계 로그 없음",
  no_in_message: "후행 IN 메시지 없음",
  no_out_message: "선행 OUT 메시지 없음",
  dependency_conflict: "재시도 간 선행 값 다름",
  successor_ambiguous_retry: "후행 재시도 모호",
  predecessor_ambiguous_retry: "선행 재시도 모호",
  transaction_row_ambiguous: "거래 행 모호",
  depend_process_id_missing: "선행 값 없음",
  missing_message_id: "메시지 ID 없음",
  multiple_messages: "메시지 여러 건",
  excluded_transaction_suffix: "제외 끝자리 거래",
  unkeyed_rows: "PROCESS_ID 없는 행",
};
export const reasonText = (code: string | null) => (code === null ? "—" : reasonLabels[code] ?? code);

export const scopeAdapter = (scope: Record<string, unknown> | null) => (typeof scope?.adapterType === "string" ? scope.adapterType : null);
/** 경로 문구: 필수 단계는 단계 하나, 선행·인계는 앞 → 뒤. */
export const pathText = (item: Pick<RelationCandidate, "kind" | "from" | "to">) =>
  item.kind === "required_step" ? item.from.processId : `${item.from.processId} → ${item.to?.processId ?? "—"}`;
/** 어댑터 칸: scope null = 모든 어댑터(Trainer SCOPE_NULL_POLICY). 필수 단계는 인터페이스 범위라 어댑터 없음. */
export const adapterText = (item: Pick<RelationCandidate, "kind" | "scope">) =>
  item.kind === "required_step" ? "—" : scopeAdapter(item.scope) ?? "모든 어댑터";

/**
 * 충족률(0~1) → 백분율 한 자리. 99.96%를 100%로 반올림하지 않도록 내림, 0보다 크면 0.1% 이상으로 보인다(정확히 0·1만 0%·100%).
 */
export const rateText = (rate: number | null) => {
  if (rate === null) return "—";
  if (rate >= 1) return "100%";
  if (rate <= 0) return "0%";
  const tenths = Math.min(999, Math.max(1, Math.floor(rate * 1000)));
  return `${(tenths / 10).toFixed(1)}%`;
};
export const n = (value: number) => value.toLocaleString("ko-KR");
export const thresholdText = (item: Pick<ThresholdCandidate, "operator" | "displayThreshold" | "unit">) =>
  item.displayThreshold === null || item.operator === null ? "—" : `${item.operator === "gt" ? ">" : item.operator === "gte" ? "≥" : item.operator} ${item.displayThreshold} ${item.unit}`.trim();
/** 제안 임계값의 근거: Trainer source data_p95 = 코드가 계산한 기준 표본 p95(LLM 제안 아님). */
export const THRESHOLD_BASIS = "기준 표본 p95(코드 계산)";
/**
 * 처리 시간 후보 한계 문구. 근거: Trainer 처리 시간 후보 `limitation`(한계 원문; 새 결과는 사용자용 문장이고 내부 경로·식은 `limitationRefs`로 옮김 —
 * 화면은 `limitationRefs`·`evidence.note`를 보이지 않는다) + 화면 문구 규칙(본문에 내부 파일 경로·코드 식별자를 보이지 않음).
 * 옛 결과처럼 내부 경로(`.py` 파일 이름, `trainer/foo.py`·`a_b/c` 같은 소문자 경로)가 섞인 문구는 아래 고정 문장으로 바꾼다.
 * 'S/F'·'건/분'처럼 대문자·한글 사이 '/'는 경로가 아니라 그대로 둔다. 고정 문장은 Trainer 새 결과 `limitation` 원문 전체와 같다.
 */
export const END_TIME_LIMITATION =
  "종료 시각(END_TIME)이 없는 실행은 처리 시간 계산에서 제외됩니다. 늦게 끝난 단계는 잡지만, 끝나지 않고 멈춘 단계는 잡지 못합니다.";
const INTERNAL_PATH = /\.py\b|\b[a-z_]+\/[a-z_./]+/;
export const limitationText = (text: string) => (INTERNAL_PATH.test(text) ? END_TIME_LIMITATION : text);
/** 목록용: 바꾼 뒤 같은 문장은 한 번만. */
export const limitationTexts = (texts: string[]) => [...new Set(texts.map(limitationText))];

export const jobStatusLabels: Record<string, string> = { queued: "대기", running: "실행 중", succeeded: "완료", failed: "실패", blocked: "멈춤", cancelled: "취소" };
export const jobStatusText = (job: CandidateJob) => `${jobStatusLabels[job.status] ?? job.status}${job.errorCode ? ` · ${job.errorCode}` : ""}`;

export const candidateErrorTitle = (error: unknown, what: string) =>
  error instanceof HttpError ? `${what} 조회 실패 · ${error.code ?? `http_${error.status}`}` : error instanceof Error && error.message ? error.message : `${what} 조회 실패`;

/**
 * 후보 작업 요청(contracts/rule-candidates-ui.md 8절 "후보 작업 요청"). 화면 용어: 스냅샷 = "저장 데이터셋"(평가 결과 화면 세트 생성과 같음),
 * 조회·요청 실패는 "<무엇> 조회 실패 · <코드>"·"<무엇> 요청 실패 · <코드>"(학습 요청과 같은 형식).
 */
export const JOB_REQUEST = "후보 작업 요청";
export const JOB_REQUEST_SUBMIT = "요청";
export const JOB_REQUEST_RUNNING = "후보 작업 진행 중";
export const JOB_REQUEST_DESCRIPTION = "고른 저장 데이터셋으로 관계 규칙·처리 시간 후보를 다시 계산합니다. 운영 룰은 바뀌지 않고, 작업이 끝나면 이 화면에 새 결과가 나옵니다.";
export const SNAPSHOT_FIELD = "저장 데이터셋";
export const SNAPSHOT_CURRENT = "현재 결과";
export const SNAPSHOTS_EMPTY = "저장 데이터셋이 없습니다. 학습을 요청하면 학습 기간의 저장 데이터셋이 만들어집니다.";
export const SNAPSHOTS_EMPTY_LINK = "머신러닝 모델 › 새 학습";
