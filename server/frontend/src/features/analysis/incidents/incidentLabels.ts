import { categoryLabels, DASH } from "../labels";
import type { IncidentCriteria, IncidentFlag, IncidentGrade, IncidentOccurredBasis, IncidentPeriod, IncidentRowBasis } from "./incidentApi";

/**
 * 사건 보기 화면 문구(contracts/incidents-ui.md 5절). Process Flow 문구(labels.ts)와 섞지 않는다.
 * - 기준 설명 이름표: 사건 소속은 같은 시간창·같은 기준값으로 묶였다는 뜻(상관관계, plan 4절 "거래 간 간선을 인과로 표기하지 않음")
 */
export const INCIDENT_CRITERIA_NOTE = "같은 시간·같은 값으로 묶은 사건";
export const incidentNavLabel = "사건 보기";
export const incidentNavGroupTitle = "사건";

/** 기준 종류(Detector incidents.md 4.2 키 이름). 모르는 키 이름은 그대로 보인다. */
const criteriaKindLabels: Record<string, string> = { response_code: "응답코드", channel_id: "채널" };
export const criteriaKindLabel = (kind: string | null) => (kind ? (criteriaKindLabels[kind] ?? kind) : "기준");
/**
 * 긴 기준값(Detector incidents.md 4.2·8절): 저장 키에는 해시만 있고, API가 구성원 원천 행에서 원래 값을 찾지 못하면 value null.
 * 화면은 해시를 보이지 않고 이 이름표를 보인다.
 */
export const LONG_VALUE_OMITTED = "값 생략(긴 값)";
export const longValueOmittedTitle = "기준값이 길어 사건에는 해시만 저장되고, 구성원 원천 행에서 원래 값을 읽지 못함";
/** 제목·시간축 이름표에 넣는 기준값 최대 글자 수(넘으면 말줄임, 전체 값은 기준값 칸·title). */
const CRITERIA_TEXT_MAX = 60;
export const criteriaValueText = (criteria: Pick<IncidentCriteria, "value" | "longValue">) =>
  criteria.value ? criteria.value : criteria.longValue ? LONG_VALUE_OMITTED : DASH;
export const criteriaText = (criteria: Pick<IncidentCriteria, "kind" | "value" | "longValue">) => {
  const value = criteriaValueText(criteria);
  return `${criteriaKindLabel(criteria.kind)} ${value.length > CRITERIA_TEXT_MAX ? `${value.slice(0, CRITERIA_TEXT_MAX)}…` : value}`;
};

/**
 * 구성원 시각 출처(Detector incidents.md 3절·8절): occurred_basis가 decided_at이면 원천 발생 시각(END_TIME·START_TIME)을 쓰지 못해
 * 판정 시각으로 대신한 값이다. 출처가 원천 시각일 때만 "발생 시각"이라고 부른다.
 */
export type TimeBasisKind = "source" | "decided" | "mixed";
export const timeBasisKind = (basis: IncidentOccurredBasis): TimeBasisKind =>
  basis.decidedAt === 0 ? "source" : basis.processTimeRaw === 0 ? "decided" : "mixed";
export const rowTimeBasisKind = (basis: IncidentRowBasis | null): TimeBasisKind =>
  basis === "decided_at" ? "decided" : basis === "mixed" ? "mixed" : "source";
/** 시각 옆 표시(원천 시각이면 없음). */
export const timeBasisMarks: Record<TimeBasisKind, string | null> = { source: null, decided: "판정 시각 기준", mixed: "일부 판정 시각 기준" };
export const timeBasisTitle = "원천 발생 시각을 쓸 수 없어 판정 시각으로 대신한 구성원이 있음(지금은 원본 시간대 미검증이라 대부분 판정 시각)";
/** 구성원 표 시각 칸 머리·설명. */
export const memberTimeHeads: Record<TimeBasisKind, string> = { source: "발생 시각", decided: "판정 시각", mixed: "발생·판정 시각" };
export const memberOrderNotes: Record<TimeBasisKind, string> = { source: "첫 발생 시각 순", decided: "첫 판정 시각 순", mixed: "첫 시각 순" };
/** 시간축 표시(보이는 사건 중 판정 시각 기준이 있을 때). */
export const timelineBasisNotes: Record<TimeBasisKind, string | null> = { source: null, decided: "시간축: 판정 시각 기준", mixed: "시간축: 일부 판정 시각 기준" };
/** 여러 흐름의 출처를 합친 것(시간축). */
export const combinedTimeBasis = (list: IncidentOccurredBasis[]) =>
  timeBasisKind(list.reduce((sum, b) => ({ processTimeRaw: sum.processTimeRaw + b.processTimeRaw, decidedAt: sum.decidedAt + b.decidedAt }), { processTimeRaw: 0, decidedAt: 0 }));

/** 목록이 비었을 때 덧붙이는 짧은 안내(Detector incidents.md 8절·열린 항목 2: v3 판정은 등급이 없어 구성원이 아님). */
export const incidentEmptyV3Note = "v04 판정 전환 전 판정(v3)은 등급이 없어 사건으로 묶이지 않습니다.";
/** 시간축 막대 색(기준 종류별). 응답코드 = 정보색, 채널 = 경고색, 그 밖 = 흐린 색. */
export const criteriaTone = (kind: string | null) =>
  kind === "response_code" ? "bg-info-text" : kind === "channel_id" ? "bg-warning" : "bg-muted-foreground";

export const periodLabels: Record<IncidentPeriod, string> = { "24h": "최근 24시간", "7d": "최근 7일", "30d": "최근 30일", all: "전체" };

/** 흐름 표시(contracts 2절 flags). 짧은 이름표 + 원인 설명(title). */
export const incidentFlagLabels: Record<IncidentFlag, string> = {
  merge_mismatch: "기준 다른 병합 제외",
  merge_cycle: "병합 순환 제외",
  merge_target_missing: "병합 대상 없음",
  continuation_mismatch: "기준 다른 이어짐 제외",
  continuation_within_segment: "구간 안 이어짐 제외",
  continuation_target_missing: "이어짐 대상 없음",
  continuation_branched: "구간 갈라짐",
  continuation_cycle: "구간 순환",
  member_source_mismatch: "다른 원본 판정 제외",
};
export const incidentFlagTitles: Record<IncidentFlag, string> = {
  merge_mismatch: "병합 대상의 원본·기준 버전·기준값이 달라 병합을 쓰지 않음",
  merge_cycle: "병합이 서로를 가리켜 고리를 이뤄 쓰지 않음",
  merge_target_missing: "병합 대상 사건이 없음",
  continuation_mismatch: "앞 구간의 원본·기준 버전·기준값이 달라 이어짐을 쓰지 않음",
  continuation_within_segment: "앞 구간이 같은 구간이라 이어짐을 쓰지 않음",
  continuation_target_missing: "앞 구간 사건이 없음",
  continuation_branched: "앞·뒤 구간이 2개 이상인 구간이 있음",
  continuation_cycle: "구간 앞뒤가 돌아옴",
  member_source_mismatch: "사건과 원본이 다른 판정은 세지 않음",
};
/** 흐름이 읽기 상한 밖 사건을 가리킴(item.truncated). 문구는 사용자 승인(11/12, contracts/incidents-ui.md 5절). */
export const INCIDENT_PARTIAL = "일부 구간 제외";
export const incidentPartialTitle = "읽기 상한 밖 사건과 이어져 있어 흐름이 일부만일 수 있음";
/** 목록 기본 필터·전환(사용자 선호 추천). */
export const INCIDENT_SHOW_ALL = "모두 보기";
/**
 * 목록 필터 "거래 2건 이상"·빈 목록 보조 "(거래 2건 이상만 표시)" 문구는 사용자 승인(11/12, contracts 5절).
 * 거래 = 해제되지 않은 거래(counts.transactions와 같은 기준, API minTransactions — contracts 3절).
 */
export const incidentFilterNote = "거래 2건 이상";
/** 최신 판정 등급을 모름(v3·형식 오류) → 이상·이상 징후가 아니므로 해제(Detector incidents.md 8절). 상세 머리 "등급 모름 판정 N건 · 해제로 셈"은 사용자 승인(11/12). */
export const GRADE_UNKNOWN = "등급 모름";
export const gradeUnknownTitle = "같은 실행의 최신 판정 등급을 읽을 수 없어(이상·이상 징후가 아님) 해제로 셈";

/** 판정 등급(이상 분석 분류 이름과 같음). */
export const incidentGradeLabels: Record<IncidentGrade, string> = {
  anomaly: categoryLabels.anomaly,
  anomaly_sign: categoryLabels.anomaly_sign,
  normal: "정상",
  undetermined: categoryLabels.undetermined,
};
export const RELEASED = "해제";
export const releasedTitle = "같은 실행의 최신 판정이 이상·이상 징후가 아님";

/** 기간 길이: 1분 미만 = "1분 미만", 그 밖 시·분. */
export const durationText = (ms: number) => {
  if (ms < 60_000) return "1분 미만";
  const minutes = Math.round(ms / 60_000);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}시간${minutes % 60 ? ` ${minutes % 60}분` : ""}` : `${minutes}분`;
};
export const segmentsText = (length: number) => (length > 1 ? `${length}개 구간` : DASH);
export const secondsText = (value: number | null) => (value === null ? DASH : value % 60 === 0 ? `${value / 60}분` : `${value}초`);
