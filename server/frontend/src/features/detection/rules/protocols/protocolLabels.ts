import type { ProtocolDrafts, RuleSetState } from "../ruleSetApi";

/**
 * 프로토콜 설정 화면 문구(contracts/protocol-settings-ui.md 4절). 근거:
 * - 화면·버튼 이름: 사용자 결정 11/13 "프로토콜 설정"(룰 설정 하위, 목록 → 상세)·"정상 프로토콜"·"허용 경로"·[관측 경로로 초안 만들기]
 * - 판정 문장: protocol-rules.md 1절 "거래 완료 때 그 인터페이스 허용 경로에 없으면 이상", 4.1 기능 켜짐 정의
 * - 미등록 규칙 이름·조건: 내장 하드룰 `hard:unregistered-interface` displayName·conditionText(서버 값 그대로 표시)
 * - 사유 이름: protocol-rules.md 3.4 표(관계 후보 화면 reasonLabels와 겹치는 것은 같은 이름)
 * 새 문장은 protocol-settings-ui.md 4절 목록에 함께 적는다.
 */
export const PROTOCOL_LIST_LEAD = "인터페이스별 허용 경로(거치는 채널 순서 전체)를 등록합니다. 켜진 항목이 있으면 거래 완료 때 그 인터페이스의 허용 경로에 없는 경로는 이상으로 판정합니다.";
export const UNREGISTERED_RULE_ID = "hard:unregistered-interface";
/** 서버 경고 protocol_unregistered_without_entries와 같은 문장(rule-set.md v3.6 — 문장은 WebUI 결정). */
export const UNREGISTERED_WITHOUT_ENTRIES = "켜진 프로토콜 항목이 없어 등록되지 않은 인터페이스 규칙이 아무것도 판정하지 않습니다.";
export const FIRST_ENTRY_UNREGISTERED = "등록되지 않은 인터페이스의 거래도 이상으로 판정(첫 항목 저장 때 함께 켬, 끌 수 있음)";
export const APPLIES_FROM = "저장 이후 새 판정부터 적용됩니다. 이미 이상으로 기록된 판정은 다시 판정하지 않습니다.";
export const PATHS_EMPTY_ISSUE = "허용 경로를 1개 이상 등록해 주세요";
export const DRAFT_BUTTON = "관측 경로로 초안 만들기";
export const DRAFT_LEAD = "학습 기간에 관측된 경로를 코드가 집계했습니다. 확인한 경로만 골라 허용 경로에 추가하고 저장합니다.";
export const DRAFT_NOT_OBSERVED = "학습 기간에 이 인터페이스의 경로가 관측되지 않았습니다.";
export const CHANNEL_INPUT_HELP = "채널 ID를 한 줄에 하나씩 거치는 순서대로 입력합니다(시작 → … → 끝). 값은 입력한 그대로 저장합니다.";
export const PATHS_MAX = 64;
export const STEPS_MAX = 64;

/** 초안 없음 사유(protocol-settings-ui.md 2절 state). 빈 초안으로 보이지 않게 사유를 그대로 보인다. */
export const draftUnavailableText = (drafts: Extract<ProtocolDrafts, { state: "protocol_drafts_unavailable" }>) =>
  drafts.reason === "no_result"
    ? "관측 경로 초안 없음 · 성공한 규칙 추천 후보 작업 결과가 없습니다"
    : "관측 경로 초안 없음 · 최근 후보 작업 결과에 경로 집계가 없습니다(경로 집계 전 Trainer 결과)";

/** protocol-rules.md 3.4 사유 → 화면 이름. 모르는 값은 코드 그대로. */
export const pathReasonLabels: Record<string, string> = {
  cross_split: "학습 분할 경계",
  anomaly_label: "이상 라벨 거래",
  no_transaction_row: "거래 행 없음",
  transaction_row_ambiguous: "거래 행 모호",
  interface_absent: "인터페이스 없음",
  graph_truncated: "단계 수 상한 초과",
  process_id_missing: "PROCESS_ID 없음",
  precedes_ambiguous: "선행 모호",
  depend_process_id_missing: "선행 값 없음",
  start_ambiguous: "시작 단계 모호",
  chain_broken: "선행 사슬 끊김",
  channel_absent: "채널 없음",
  channel_ambiguous: "채널 모호",
  path_too_long: "경로 단계 상한 초과",
  routes_too_many: "경로 수 상한 초과",
};
export const pathReasonText = (code: string) => pathReasonLabels[code] ?? code;

/** 비율 표기: 한 자리 내림(규칙 추천 충족률과 같은 방식), 0·1만 정수. */
export const ratioText = (ratio: number) => {
  if (ratio <= 0) return "0%";
  if (ratio >= 1) return "100%";
  const tenth = Math.floor(ratio * 1000) / 10;
  return `${Math.min(99.9, Math.max(0.1, tenth)).toFixed(1)}%`;
};
export const count = (value: number) => value.toLocaleString("ko-KR");

/** 같은 채널 배열(원래 문자열·순서·길이 그대로)인가. 판정 규칙(protocol-rules.md 5.2)과 같은 비교. */
export const samePath = (left: readonly string[], right: readonly string[]) => left.length === right.length && left.every((value, index) => value === right[index]);
export const pathIndex = (paths: readonly (readonly string[])[], channels: readonly string[]) => paths.findIndex((path) => samePath(path, channels));

export const protocolEntry = (state: RuleSetState, interfaceId: string) => (state.protocolRules ?? []).find((rule) => rule.interfaceId === interfaceId) ?? null;
export const unregisteredRule = (state: RuleSetState) => state.hardRules.find((rule) => rule.id === UNREGISTERED_RULE_ID) ?? null;
/** 프로토콜 기능 켜짐 = 켜진 항목이 1개 이상(protocol-rules.md 4.1). */
export const protocolFeatureOn = (state: RuleSetState) => (state.protocolRules ?? []).some((rule) => rule.enabled);

/** 줄마다 채널 하나. 줄 끝 CR만 떼고 값은 그대로(trim 없음 — 서버가 원래 값으로 비교). 빈 줄은 버린다. */
export const parseChannelLines = (text: string) => text.split("\n").map((line) => line.replace(/\r$/, "")).filter((line) => line !== "");
