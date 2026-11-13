import type {
  Category,
  CompletenessFilter,
  DecisionCause,
  DecisionItem,
  FeedbackKind,
  HandoffResult,
  ItemSignal,
  ItemStage,
  RiskLevel,
  SignalFilter,
  Sort,
  WorkflowState,
} from "./data/analysisApi";
import { isProtocolRuleId, isRelationRuleId, type EvidenceGroup } from "./data/evidence";
import { kindLabels as relationKindNames } from "@/features/detection/rules/candidates/candidateLabels";

/** 이상 분석 화면 문구(와이어프레임 #93 analysis 용어). */
export const categoryLabels: Record<Category, string> = {
  all: "전체",
  anomaly: "이상",
  anomaly_sign: "이상 징후",
  normal: "정상",
  in_progress: "판정 중·중간 판정",
  undetermined: "정상 여부 미확인",
  failed_replaced: "판정 실패·대체",
  risk_all: "전체",
  risk_critical: "위험",
  risk_warning: "주의",
  risk_info: "참고",
  risk_unrated: "위험도 미정",
  workflow_open: "보류",
  workflow_resolved: "완료",
};
export const signalLabels: Record<ItemSignal, string> = {
  yes: "이상",
  no: "이상 신호 없음",
  unknown: "정상 여부 미확인",
  pending: "판정 중",
  failed: "판정 실패",
};
export const signalFilterLabels: Record<SignalFilter, string> = {
  all: "전체",
  anomaly: "이상",
  anomaly_sign: "이상 징후",
  no: "이상 신호 없음",
  unknown: "정상 여부 미확인",
  // 이전 주소 `signal=yes`(와이어프레임 signalLabel.yes)
  yes: "이상 신호 있음",
};
/** 위험도 단계(와이어프레임 LEVEL_LABEL). */
export const riskLevelLabels: Record<RiskLevel, string> = { critical: "위험", warning: "주의", info: "참고" };
export const riskLevelVariant: Record<RiskLevel, "critical" | "warning" | "secondary"> = {
  critical: "critical",
  warning: "warning",
  info: "secondary",
};
/** 업무 분류(와이어프레임 dFields): 감지됨 / 보류 / 완료. */
export const workflowLabels: Record<WorkflowState, string> = { detected: "감지됨", open: "보류", resolved: "완료" };
/** 처리 상태 버튼(와이어프레임 dWorkflow): 현재 상태별 [다음 상태, 문구, 주 버튼]. */
export const workflowActions: Record<WorkflowState, [WorkflowState, string, boolean][]> = {
  detected: [["open", "처리 보류", false], ["resolved", "처리 완료", true]],
  open: [["detected", "원래 상태로 복원", false], ["resolved", "처리 완료", true]],
  resolved: [["detected", "원래 상태로 복원", false]],
};
/** 관리자 처리 상태 버튼(사용자 11/13): 보류·완료를 하나로 합친 버튼. 대화상자 안에서 처리 보류·처리 완료를 고른다. */
export const classifyLabel = "로그 분류하기";
/** 조사·조치 기록 유형(와이어프레임 feedbackKind). */
export const feedbackKindLabels: Record<FeedbackKind, string> = {
  action: "조치 기록",
  "suspected-false-positive": "오탐 의심",
  "insufficient-evidence": "근거 부족",
};
export const completenessFilterLabels: Record<CompletenessFilter, string> = {
  all: "전체",
  complete: "완료",
  partial: "부분",
  unavailable: "판정불가",
};
export const stageLabels: Record<ItemStage, string> = {
  pending: "판정 중",
  provisional: "중간 판정",
  updated: "근거 추가",
  final: "최종",
  replaced: "최종 · 이전 중간 판정 대체됨",
  failed: "판정 실패(최종 없음)",
};
export const sortLabels: Record<Sort, string> = { newest: "최신순", oldest: "과거순" };
export const runLabels: Record<string, string> = {
  succeeded: "완료",
  failed: "실패",
  not_ready: "미완료",
  skipped: "꺼짐",
};
/** 작업 상태 표시(와이어프레임 용어). 그 밖의 값(예: blocked)은 저장된 상태 코드 그대로. */
export const jobStatusLabels: Record<string, string> = {
  queued: "대기",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
};
export const DASH = "—";
export const UNAVAILABLE = "— 확인 불가";

/**
 * Process Flow 보기 문구(비교 시제품). 근거 어휘: server/contracts/process-graph.md
 * - 보기 이름: 트리 보기 = "로그 구성", 그래프 보기 = "실행 순서"(사용자 11/12 결정). 내부 값·저장값은 tree·graph 그대로
 * - 인계 비교 결과(hands_off evidence.result·선행 간선 evidence.handoff): match·mismatch·undetermined
 * - missing 노드 reason: not_logged(PROCESS 상한 안에서 행 없음). not_loaded는 확인 불가(UNAVAILABLE)
 * - process 노드 verification: ambiguous(같은 시도가 2건 이상 등, 대표 시도를 고르지 않음)
 * "판단 보류"는 인계 비교 undetermined 간선에만 쓰고, 모호 PROCESS 노드는 "연결 모호"로 구분한다.
 */
export const flowViewLabels = { tree: "로그 구성", graph: "실행 순서" } as const;
export const flowViewGroupLabel = "Process Flow 보기";
export const handoffLabels: Record<HandoffResult, string> = { match: "인계 일치", mismatch: "인계 불일치", undetermined: "판단 보류" };
export const graphLabels = {
  notLogged: "로그 없음",
  ambiguous: "연결 모호",
  noMessages: "메시지 0건",
  graph: "실행 순서",
  loadFailed: "실행 순서 불러오기 실패",
  controls: "실행 순서 조작",
  zoomIn: "확대",
  zoomOut: "축소",
  fitView: "전체 보기",
} as const;
/**
 * Process Flow 상자 범례·건수(사용자 11/12 요청): 범례는 상자 오른쪽 위, 건수는 오른쪽 아래.
 * 노드 바탕 = 판정 결과(사용자 11/12 요청): 이상 = 붉은 바탕, 이상 징후 = 노란 바탕, 그 밖(정상·판정 없음·판정 중) = 흰 바탕(범례 항목 없음).
 * 판정 대상(판정 PROCESS)은 바탕색이 아니라 표적 아이콘으로 보인다(흰 바탕이어도 찾을 수 있게). 로그 없음(점선)은 그 노드가 있을 때만 범례에 보인다.
 * 연결 모호는 노드 안 배지 글자로 뜻이 보여 범례에 넣지 않는다.
 */
export const flowLegendLabels = {
  name: "노드 표시 범례",
  anomaly: categoryLabels.anomaly,
  anomalySign: categoryLabels.anomaly_sign,
  focus: "판정 대상",
  notLogged: graphLabels.notLogged,
} as const;
export const flowCountsText = (counts: { processes: number; messages: number; errors: number }) =>
  `(프로세스 ${counts.processes}건 · 메시지 ${counts.messages}건 · 에러 ${counts.errors}건)`;

/** 검사 완료: 최종 판정 status. 최종 판정이 없으면 판정 중 / 최종 없음. */
export const completenessText = (item: DecisionItem) =>
  item.status ? completenessFilterLabels[item.status] : item.kind === "failed" ? "최종 없음" : "판정 중";

/**
 * 판정 결과 문구(와이어프레임 resultText): 이상 신호가 있으면 이상. 서버가 저장된 v4 composite.grade로 돌려준 등급이 anomaly_sign이면 이상 징후.
 * 등급은 저장값만 쓴다(v3에서 등급을 추정하지 않음, detection-decision.md U6).
 */
export const resultText = (item: DecisionItem) => {
  if (item.signal !== "yes") return signalLabels[item.signal];
  return item.grade === "anomaly_sign" ? "이상 징후" : signalLabels.yes;
};
export const resultVariant = (item: DecisionItem): "critical" | "warning" | "success" | "secondary" | "info" =>
  item.signal === "yes"
    ? item.grade === "anomaly_sign"
      ? "warning"
      : "critical"
    : item.signal === "no"
      ? "success"
      : item.signal === "failed"
        ? "critical"
        : item.signal === "pending"
          ? "info"
          : "secondary";

const two = (n: number) => String(n).padStart(2, "0");
/** 월/일 시:분:초. 값이 없으면 —. */
export const stampText = (iso: string | null | undefined) => {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return DASH;
  return `${two(d.getMonth() + 1)}/${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
};

/**
 * 판정 방법 표시 이름(detection-decision.md #92 C18 대분류 이름, C22 승인 이상 규칙 문구). 원래 ID는 툴팁에 둔다(와이어프레임 "코드는 툴팁").
 * - `hard:<id>` → 이상 규칙(승인 문구), 승인 문구가 없는 하드룰은 `이상 규칙`
 * - `soft:*` → 이상 징후 규칙, `ml:<key>` → 머신러닝 모델(<key>)
 * - 알 수 없는 접두는 null(방법 부분을 생략)
 */
const hardRuleNames: Record<string, string> = {
  "hard:process-status": "상태가 S가 아님",
  "hard:negative-duration": "시작 시각이 종료 시각보다 늦음",
  "hard:success-with-error": "성공 상태이나 오류 건수가 기록됨",
  "hard:count-conservation": "건수 합계 불일치",
  // 룰 세트 v3 내장 규칙: Detector 내장 목록 displayName(hard_rules.py, 룰 설정·응답 코드·프로토콜 설정 화면이 이미 보이는 이름)
  "hard:transaction-failed": "거래 실패",
  "hard:unregistered-interface": "등록되지 않은 인터페이스",
};
/**
 * 근거 구분 이름(판정 상세 세부 방법별 결과·판정 결과 근거). 새 문구 없이 기존 화면 용어만 쓴다.
 * - 이상 규칙·이상 징후 규칙·머신러닝 모델: rule-set.md 용어 절 / 관계 규칙: 룰 설정·규칙 추천(rule-candidates-ui.md) / 정상 프로토콜: protocol-settings-ui.md(결정 11/13)
 * - "규약 위반"은 근거가 관계 규칙(`hard:rel-*`)일 때만, 내장 이상 규칙은 그대로(relation-rules.md 1절)
 * - "관계 확인 보류" = relationNotes 수집 미완료 보류(relation-rules.md 5절, 확정 11/12 사용자)
 */
export const evidenceGroupLabels: Record<EvidenceGroup, string> = {
  hard: "이상 규칙",
  relation: "관계 규칙",
  protocol: "정상 프로토콜",
  soft: "이상 징후 규칙",
  ml: "머신러닝 모델",
};
export const RELATION_VIOLATION = "규약 위반";
export const RELATION_HELD = "관계 확인 보류";
/** 관계 규칙 판정 kind 화면 이름(규칙 추천 kindLabels와 같은 표 — rule-candidates-ui.md 8절). */
export const relationKindLabel = (kind: string | null) => (kind && kind in relationKindNames ? relationKindNames[kind as keyof typeof relationKindNames] : null);
/** 상태 코드 뜻(사용자 11/04). 룰 설정의 코드별 위험도도 같은 표를 쓴다. */
export const statusCodeLabels: Record<string, string> = { S: "성공", F: "실패", P: "처리중", N: "미처리", H: "부분성공", Z: "데이터없음" };
/** 프로토콜 방법 T 코드(protocol-rules.md 5.2) → 프로토콜 알림과 같은 이름(protocol-settings-ui.md 4절 "처음 보는 경로|등록되지 않은 인터페이스"). */
export const protocolHitLabels: Record<string, string> = {
  PROTO_PATH_UNMATCHED: "처음 보는 경로",
  PROTO_INTERFACE_UNREGISTERED: hardRuleNames["hard:unregistered-interface"],
};
const hardResultCodes: Record<string, string> = {
  HARD_NEGATIVE_DURATION: hardRuleNames["hard:negative-duration"],
  HARD_SUCCESS_WITH_ERROR: hardRuleNames["hard:success-with-error"],
  HARD_COUNT_CONSERVATION: hardRuleNames["hard:count-conservation"],
  HARD_TRANSACTION_FAILED: hardRuleNames["hard:transaction-failed"],
  PROTO_PATH_UNMATCHED: `${evidenceGroupLabels.protocol} · ${protocolHitLabels.PROTO_PATH_UNMATCHED}`,
  PROTO_INTERFACE_UNREGISTERED: `${evidenceGroupLabels.protocol} · ${protocolHitLabels.PROTO_INTERFACE_UNREGISTERED}`,
};
/**
 * 결과 코드 표시(detection-decision.md 4.3): `PROCESS_STATUS_H` → `상태 H 부분성공`, `HARD_*` → 규칙 이름,
 * 관계 규칙 `REL_<KIND>`(relation-rules.md 3절) → `규약 위반 · 선행 관계`, 프로토콜 `PROTO_*` → `정상 프로토콜 · …`. 모르는 코드는 원문 그대로.
 */
export const resultCodeLabel = (code: string) => {
  const status = /^PROCESS_STATUS_(.+)$/.exec(code)?.[1];
  if (status) return statusCodeLabels[status] ? `상태 ${status} ${statusCodeLabels[status]}` : `상태 ${status}`;
  const relation = relationKindLabel(/^REL_([A-Z_]+)$/.exec(code)?.[1]?.toLowerCase() ?? null);
  if (relation) return `${RELATION_VIOLATION} · ${relation}`;
  return hardResultCodes[code] ?? code;
};
/**
 * 방법 표시 이름. 관계 규칙 `hard:rel-*`은 `관계 규칙`, 프로토콜 항목 `hard:proto-*`은 `정상 프로토콜`(근거 구분 이름 — 내부 id는 툴팁).
 */
export const methodLabel = (methodId: string): string | null => {
  if (isRelationRuleId(methodId)) return evidenceGroupLabels.relation;
  if (isProtocolRuleId(methodId)) return evidenceGroupLabels.protocol;
  if (methodId.startsWith("hard:")) return hardRuleNames[methodId] ? `이상 규칙(${hardRuleNames[methodId]})` : "이상 규칙";
  if (methodId.startsWith("soft:")) return "이상 징후 규칙";
  if (methodId.startsWith("ml:") && methodId.length > 3) return `머신러닝 모델(${methodId.slice(3)})`;
  return null;
};
/** v3 evidence rules[].ruleKey 표시 이름(detection-decision.md 3.2·U6): 상태 규칙 = 이상 규칙, 그 밖의 등록 Rule = 이상 징후 규칙(과거 등록 Rule). */
export const legacyRuleLabel = (ruleKey: string) =>
  ruleKey === "process-status" ? (methodLabel("hard:process-status") as string) : "이상 징후 규칙(과거 등록 Rule)";
/** 방법 ID 목록 → 표시 이름 목록(중복 제거, 알 수 없는 접두 제외). */
export const methodLabels = (methodIds: string[]) => [...new Set(methodIds.flatMap((id) => methodLabel(id) ?? []))];

/** 이상 징후 규칙 비교 연산자(세부 방법별 결과 표·룰 설정과 같은 기호). */
export const operatorSymbols: Record<string, string> = { gte: "≥", gt: ">", lte: "≤", lt: "<" };
/** 숫자 문자열은 천 단위 구분(룰 설정 numberLabel과 같음), 숫자가 아니면 그대로. */
const numberText = (value: string) => (value.trim() !== "" && Number.isFinite(Number(value)) ? Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 20 }) : value);
const unitText = (value: string, unit: string | null) => (unit ? `${numberText(value)} ${unit}` : numberText(value));
/**
 * 목록 행 이상 원인 한 건 문구(사용자 11/13). 저장값만 쓰고 이름은 기존 표시 이름(결과 코드·규칙 이름·카탈로그 이름)을 쓴다.
 * - 상태 규칙: `상태 F 실패`(+ ` · 응답 코드 E500`, 응답 코드 사전에 메시지가 있으면 ` (메시지)`), 그 밖의 이상 규칙: 승인 규칙 이름(결과 코드 → 이름)
 * - 이상 징후 규칙: `처리 시간 8,000 ms (기준 ≥ 5,000 ms)`, 과거 등록 Rule: `이상 징후 규칙(과거 등록 Rule)`
 * - 머신러닝 모델: `Isolation Forest 원점수 0.84 (판정선 0.61)`
 */
/** `응답 코드 <코드>` + 사전 메시지가 있으면 ` (<메시지>)`(contracts/response-codes-ui.md 5절). */
export const responseCodeText = (responseCode: string, message: string | null | undefined) =>
  message ? `응답 코드 ${responseCode} (${message})` : `응답 코드 ${responseCode}`;
export const causeText = (cause: DecisionCause, responseCode: string | null, responseCodeMessage: string | null = null) => {
  if (cause.category === "hard") {
    if (cause.methodId === "hard:process-status") {
      const status = cause.code ? resultCodeLabel(cause.code) : cause.observedStatus ? resultCodeLabel(`PROCESS_STATUS_${cause.observedStatus}`) : (methodLabel(cause.methodId) as string);
      return responseCode ? `${status} · ${responseCodeText(responseCode, responseCodeMessage)}` : status;
    }
    if (cause.code) return resultCodeLabel(cause.code);
    return (cause.methodId && hardRuleNames[cause.methodId]) ?? (cause.methodId ? methodLabel(cause.methodId) : null) ?? "이상 규칙";
  }
  if (cause.category === "soft") {
    if (cause.ruleKey) return legacyRuleLabel(cause.ruleKey);
    const field = cause.fieldName ?? "이상 징후 규칙";
    const criterion = cause.threshold ? `기준 ${operatorSymbols[cause.operator ?? ""] ?? cause.operator ?? ""} ${unitText(cause.threshold, cause.unit)}`.replace(/\s+/g, " ") : null;
    if (cause.observedValue !== null) return `${field} ${unitText(cause.observedValue, cause.unit)}${criterion ? ` (${criterion})` : ""}`;
    return criterion ? `${field} (${criterion})` : field;
  }
  const name = cause.modelName ?? (cause.methodId ? methodLabel(cause.methodId) : null) ?? "머신러닝 모델";
  const score = cause.score === null ? "" : ` 원점수 ${cause.score.toLocaleString("ko-KR", { maximumFractionDigits: 4 })}`;
  return `${name}${score}${cause.scoreThreshold ? ` (판정선 ${cause.scoreThreshold})` : ""}`;
};
/** 목록 행 이상 원인 줄: 첫 원인 + 더 있으면 ` 외 N건`. 원인이 없으면 null(줄 없음). */
export const causesLine = (item: Pick<DecisionItem, "causes" | "responseCode"> & Partial<Pick<DecisionItem, "responseCodeMessage">>) => {
  const [first, ...rest] = item.causes;
  if (!first) return null;
  return `${causeText(first, item.responseCode, item.responseCodeMessage ?? null)}${rest.length ? ` 외 ${rest.length}건` : ""}`;
};
